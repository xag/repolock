"""What every harness adapter shares: the parts of an adapter that are not the harness.

An adapter is a thin translation — harness event in, SPEC.md obligation out. The obligations
themselves are identical across harnesses, so they live here and each adapter keeps only its
vendor's wire format.

**v1: observe, do not predict.** There used to be a shell write-classifier here — word lists of
mutating git verbs and mutating commands, a redirect regex, a segment splitter — and it was wrong
in both directions, by construction:

  - it called reads writes. `print("a -> b")` was a redirect into a file named `b")`, so a session
    doing nothing but reading took the lock, and could be refused one (#7). The same false-positive
    class had already locked a two-session fleet out of its own repos once (#4).
  - it called writes reads, and always will. `npm install`, `make`, `uv run ruff --fix`,
    `python scripts/codegen.py` name nothing a list can hold. Deciding whether an arbitrary program
    writes to the tree means running it.

No amount of widening fixes either. The question was wrong. The right one — asked by SPEC.md §7a
and by the old code's own comments, which knew this — is not *"was this a write?"* but *"did the
repo change?"*, and that one is answered exactly, by looking:

  before a tool runs   take lock.fingerprint(repo)
  after it runs        take it again. It moved => that tool wrote. It is a fact, not a guess.

Two things follow, and they are the whole design:

  1. Where the harness hands us GROUND TRUTH, we still prevent. `Edit`/`Write`/`NotebookEdit` carry
     the path they will write, so the repo is known exactly and the lock is taken *before* the
     write, as it always was. No parsing, and no cwd guess either — the lock goes on the repo that
     owns the FILE, not the one the session happens to sit in (#8).
  2. Where it does not — a shell — we do not pretend. We refuse only what is provably unsafe (a live
     holder with a dirty tree: walking into someone's half-finished edits is the founding incident),
     and we DETECT the write afterwards, claiming the lock the moment the tree moves. The honest
     cost is a one-tool-call window in which a shell write is unguarded. v0.1 pretended to close
     that window and did not; this closes it from the second call on, and turns the case it cannot
     prevent into a collision it can *prove* and report, instead of silent corruption.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys

from repolock import env, lock

LEASE_SECONDS = 600          # renewed on every tool call; must outlast the longest single call
SEEN_DIR = "seen"            # per-(session, repo) memory of the last HEAD seen — the drift check
FP_DIR = "fp"                # ...and of the fingerprint taken before the tool now running
TICKET_DIR = "tickets"       # ...and the one command a refused session is allowed to run
WARNED_DIR = "warned"        # ...and whether we already told this session its install is broken


def ticket_for(session: str, repo: str) -> str:
    """The one command the gate will let a BLOCKED session run in the repo it is blocked on.

    A refused session cannot run any shell here — that is the whole point of the refusal — and the
    background waiter that would let it go and do something else is itself a shell. So the gate
    mints the command, and then allows exactly that string and nothing else.

    This is a **capability, not a classification.** Nothing reads the command to judge what it does.
    The hook compares it, byte for byte, against a string it wrote itself; append a single character
    — `... && rm -rf src` — and it is a different string, matches nothing, and is gated like any
    other shell. That distinction is the entire difference between this and the thing that has now
    broken twice (#4, #7): recognising your own token is not the same act as understanding
    someone else's command.

    Deterministic in (session, repo), so the refusal can print it and the next PreToolUse can
    recognise it without any state having to survive in between.
    """
    key = hashlib.sha256(f"ticket:{session}:{env.canonical(repo)}".encode()).hexdigest()[:16]
    return (f"{sys.executable} -m repolock.waitfor \"{env.canonical(repo)}\" --ticket {key}")


def is_ticket(session: str, repo: str, command: str) -> bool:
    """Is this EXACTLY the command we minted for this session and repo? Byte equality, nothing else."""
    return bool(command) and command.strip() == ticket_for(session, repo)


def repo_root(cwd: str) -> str | None:
    try:
        res = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd,
                             capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    return res.stdout.strip() or None if res.returncode == 0 else None


def repo_of(path: str) -> str | None:
    """The repo that owns a FILE — the lock target for every tool that tells us what it will write.

    Keyed on the path, never on the session's cwd. A session sitting in `chores` that edits
    `../craft-laws/x.py` was, until now, taking the lock on `chores` and writing `craft-laws`
    unguarded — while a scratch file under %TEMP% took the lock on `chores` for a write that
    touched no repo at all (#8). Both stop being possible when the target is derived from the
    path.
    """
    if not path:
        return None
    d = os.path.dirname(os.path.abspath(path))
    while not os.path.isdir(d):                    # the file may not exist yet — walk up to a dir
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent
    return repo_root(d)


# --- the per-(session, repo) memories: the HEAD last seen, the fingerprint last taken -----------

def _memo_path(kind: str, session: str, repo: str) -> str:
    d = os.path.join(os.path.dirname(env.record_path(repo)), kind)
    os.makedirs(d, exist_ok=True)
    key = hashlib.sha256(f"{session}:{repo}".encode()).hexdigest()[:16]
    return os.path.join(d, f"{key}.txt")


def _remember(kind: str, session: str, repo: str, value: str | None) -> None:
    if not value:
        return
    try:
        with open(_memo_path(kind, session, repo), "w", encoding="utf-8") as f:
            f.write(value)
    except OSError:
        pass


def _recall(kind: str, session: str, repo: str) -> str | None:
    try:
        with open(_memo_path(kind, session, repo), encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def _forget(kind: str, session: str, repo: str) -> None:
    try:
        os.remove(_memo_path(kind, session, repo))
    except OSError:
        pass


def remember_head(session: str, repo: str, head: str | None) -> None:
    _remember(SEEN_DIR, session, repo, head)


def last_seen_head(session: str, repo: str) -> str | None:
    return _recall(SEEN_DIR, session, repo)


def drift_note(session: str, repo: str) -> str | None:
    """The read-side check, packaged: report a move/rewrite since this session last looked, and
    remember where the repo stands now. Returns None when there is nothing to say. No lock, no
    classification — the soundest thing in the library, and the one that caught the incident that
    started it."""
    verdict = lock.drift(repo, last_seen_head(session, repo))
    remember_head(session, repo, verdict.get("head_commit"))
    if verdict["status"] in ("moved", "rewritten"):
        return verdict["message"]
    return None


# --- the words a refusal, a takeover and a collision owe the next session -----------------------

def format_held(repo: str, attempted: str = "", session: str = "") -> str:
    """The refusal. It owes the blocked session three things, and v0.1 gave it none of them:

      WHAT is happening   — who holds this checkout, what they are actually doing, what they have
                            already touched, and whether they are still moving or idle. "session
                            8663de9b (Bash)" is not information; it is an ID and a tool name.
      WHAT it may still do — the lock takes the shell and the file-editing tools. It does not take
                            Read, Grep, Glob, any other repo on the machine, or the MCP tools. A
                            session that does not know that assumes it is dead in the water.
      HOW to wait          — and this is the one that matters, because a refused session cannot
                            wait on its own: `sleep` is a shell, and the shell is what is blocked.
                            Without `lock_wait` the only options are spin or guess.

    A gate that stops you without telling you what it is waiting for, or offering a way to wait,
    leaves an agent doing exactly what a person would do at a locked door with no sign on it:
    rattling the handle.
    """
    v = lock.status(repo)
    lk = v.get("lock") or {}
    now = env.now()

    held_for = int(now - lk.get("acquired_at", now))
    quiet_for = int(now - lk.get("renewed_at", now))
    dirty = v.get("dirty") or []

    out = ["REPO LOCKED — another agent session is part-way through changing this working copy."]
    if attempted:
        out.append(f"  refused : {attempted}")
    out += [
        f"  repo    : {v['repo']}",
        f"  holder  : session {lk.get('session')}",
        f"  doing   : {lk.get('intent') or 'unknown'}",
        f"  since   : {held_for}s ago"
        + (f", last active {quiet_for}s ago" if quiet_for > 5 else ", still moving"),
        f"  frees in: ~{int(v.get('expires_in') or 0)}s"
        + (" — but activity renews the lease, so it may be longer" if quiet_for <= 5 else ""),
    ]
    if lk.get("idle_since"):
        out.append("  idle    : the holder went back to its human WITHOUT committing — it will not "
                   "renew,\n            so this lapses on schedule and is then yours.")
    if dirty:
        out.append(f"  touched : {len(dirty)} uncommitted change(s) in the tree —")
        out += [f"            {c}" for c in dirty[:8]]
        if len(dirty) > 8:
            out.append(f"            ...and {len(dirty) - 8} more")

    out += [
        "",
        "WHAT YOU CAN STILL DO — you are not stuck, and you should not spin:",
        "  * Read / Grep / Glob this repo freely. They are never gated; only the shell and the",
        "    file-editing tools are. You can read every file here and keep reasoning.",
        "  * Work in any other repo. The lock is per-checkout, not per-machine.",
        "",
        "AND YOU CAN WAIT WITHOUT WAITING AROUND. Do not `sleep` — `sleep` is a shell command, and",
        "the shell is exactly what is blocked. Pick whichever of these fits:",
    ]
    if session:
        out += [
            "",
            "  1. SUBSCRIBE, and get on with something else. Run this in the BACKGROUND",
            "     (run_in_background: true). It exits the moment the lock frees, and your harness",
            "     wakes you when it does. Meanwhile, go and do other work.",
            "",
            f"     {ticket_for(session, repo)}",
            "",
            "     Run it EXACTLY as written: it is a one-time ticket this refusal issued, and the",
            "     gate allows that string and no other. Change one character and it is blocked like",
            "     any other command.",
            "",
            "  2. BLOCK and wait, if you have nothing else to do: call the MCP tool",
            "     lock_wait(repo, timeout_seconds). It returns the instant the lock frees.",
        ]
    else:
        out.append("  * Call the MCP tool  lock_wait(repo, timeout_seconds).")
    out += [
        "",
        "  3. Or decide this is not blocking after all: file an issue with what you were about to",
        "     do, and move on.",
        "",
        "Do not force your way in: you would be writing a tree someone else is mid-change on.",
        "When the lease lapses the next write takes it over automatically, with a handoff telling",
        "you what landed while you waited. Forcing is for a holder that is genuinely wedged.",
    ]
    return "\n".join(out)


def format_handoff(verdict: dict) -> str:
    h = verdict["handoff"]
    note = [f"Took over the lock on {verdict['repo']} ({h['reason']})."]
    if h.get("history_rewritten"):
        note.append(f"WARNING: history was REWRITTEN — the previous holder's base commit "
                    f"{(h.get('base_commit') or '?')[:12]} no longer exists. Re-read before "
                    f"you act on anything you remember about this repo.")
    elif h.get("commits_since"):
        note.append(f"{len(h['commits_since'])} commit(s) landed since they started:")
        note += [f"  {c}" for c in h["commits_since"][:10]]
    if h.get("uncommitted"):
        note.append(f"They left {len(h['uncommitted'])} uncommitted change(s) in the tree — "
                    f"review before writing:")
        note += [f"  {c}" for c in h["uncommitted"][:10]]
    return "\n".join(note)


# --- the three obligations ---------------------------------------------------------------------

def gate(repo: str, session: str, intent: str) -> tuple[str | None, list[str]]:
    """Acquire-or-renew BEFORE a write we know is coming (SPEC.md §7.1). Ground truth only: the
    caller must have been told the path, never have guessed from a command.

    Returns (denial, notes): `denial` is the refusal when a live holder is in the way (the adapter
    turns it into its harness's block), else None; `notes` are what the session should see anyway —
    the takeover handoff, and the commit warning.
    """
    verdict = lock.acquire(repo, session, pid=0, lease_seconds=LEASE_SECONDS, intent=intent)
    if verdict["status"] == "held":
        return format_held(repo, attempted=intent, session=session), []

    notes = []
    if verdict["status"] == "acquired" and verdict.get("handoff"):
        notes.append(format_handoff(verdict))
    if warn := lock.needs_commit_warning(repo, session):
        notes.append(warn["message"])
    remember_head(session, repo, env.git_head(repo))
    return None, notes


def _degraded(repo: str, session: str, intent: str) -> tuple[str | None, list[str]]:
    """No settle event => the pessimistic hold is not affordable, so do not take it.

    Refuse only what is provably unsafe without holding anything: a live holder with a DIRTY tree.
    That is a fact about the checkout, not a guess about the command — someone's half-finished edits
    are in it. A live holder with a clean tree blocks nobody: nothing is in flight, and a lock held
    against sessions that would not have collided is #4's livelock wearing a different hat.

    Shell writes then go unguarded, which is genuinely bad. It is still far better than the
    alternative this replaces, where a `cat` locks the repo for ten minutes and no one can see why.
    """
    warn = ("repo-lock: DEGRADED — the settle hook (PostToolUse) is not wired, so a lock taken "
            "before a shell\nis never handed back, and every read would hold this repo for a full "
            "lease (that is bug #4).\nRunning UNGUARDED for shell commands instead. Declared writes "
            "(Edit/Write) are still locked.\n\nFix: wire PostToolUse (matcher Bash|PowerShell) to "
            "this same script, then RESTART this session —\nit snapshotted its hooks when it "
            "started and cannot see a new one.")
    verdict = lock.status(repo)
    if (verdict["status"] == "locked" and verdict["lock"]["session"] != session
            and verdict["dirty"]):
        return format_held(repo, attempted=intent, session=session), []

    # Once per (session, repo). A warning printed on every tool call for the rest of a session is a
    # warning nobody reads, and the noise would bury the refusals that actually matter.
    if _recall(WARNED_DIR, session, repo):
        return None, []
    _remember(WARNED_DIR, session, repo, "1")
    return None, [warn]


def hold_unknown(repo: str, session: str, intent: str,
                 background: bool = False) -> tuple[str | None, list[str]]:
    """The shell, whose effect we refuse to guess at. Called BEFORE it runs.

    We take the lock. Not because we think it writes — we have no opinion, and forming one is the
    mistake this library was rewritten to stop making — but because taking it is how you find out
    safely. It is held for the duration of THIS TOOL CALL and no longer: settle_unknown() gives it
    straight back the moment the fingerprint proves the command wrote nothing.

    That is what closes the window. Detecting a shell write only *after* it lands leaves a gap in
    which two sessions can write one checkout, and a gap you know about is not a hypothesis to be
    tested in production — it is a hole to be closed. Holding pessimistically for the length of one
    call closes it, and costs a reader the lock for exactly as long as its own command runs.

    This is NOT #4 returning. #4's disease was a reader minting a TEN-MINUTE lease and holding the
    repo against everyone while it did nothing. A reader here holds the lock while its `cat` runs
    and hands it back in the same breath. What it costs is that two sessions cannot run shell
    commands in one checkout at the same instant — which is not a bug in a mutex, it is a mutex.
    """
    # Is the settle half actually wired? A fingerprint memo left over from the LAST call proves it is
    # not: settle_unknown() always forgets the memo, so a surviving one means it never ran.
    #
    # This has to be detected, not documented. The pessimistic hold is only affordable because the
    # lock comes back the instant a command turns out to have read — and if the adapter's "after"
    # event is missing (a half-finished install; a session that snapshotted its hooks before
    # PostToolUse was added, which is EVERY session already running when you upgrade), then nothing
    # ever hands it back, and every `cat` holds the repo for a full lease. That is #4 exactly, from a
    # config typo. "The README says PostToolUse is required" is prose, and prose cannot fire.
    #
    # So we degrade instead of starving: release what we are wrongly holding, stop taking the lock on
    # speculation, and fall back to refusing only what is provably unsafe — a live holder with a
    # dirty tree. Shells go unguarded, which is bad; the alternative is a machine where every read
    # locks a repo for ten minutes, which is worse and much harder to diagnose.
    if _recall(FP_DIR, session, repo):
        _forget(FP_DIR, session, repo)
        lock.release(repo, session, force=True)      # give back what we should never have kept
        return _degraded(repo, session, intent)

    verdict = lock.acquire(repo, session, pid=0, lease_seconds=LEASE_SECONDS, intent=intent)
    if verdict["status"] == "held":
        return format_held(repo, attempted=intent, session=session), []

    # The before-picture, and whether the lock is ours only for this call. settle_unknown() needs
    # both: `acquired` means we took it on speculation and owe it back if nothing moved; `renewed`
    # means we were already holding it for a write, and it is not ours to give back.
    #
    # `background` is the third case, and it is a hole this design would otherwise have shipped. A
    # backgrounded command RETURNS IMMEDIATELY — the harness hands back a task id, PostToolUse fires
    # at LAUNCH, and the fingerprint has of course not moved yet, because the command has not done
    # anything yet. Settling on that would release the lock and let `npm run dev` write the tree
    # unguarded for the next hour. So a background task is never settled by observation: we hold the
    # lock, because we cannot see the end of the thing we started. Honest, and it is the harness's
    # own `run_in_background` field that tells us — a declared fact, not a command we read.
    state = "background" if background else verdict["status"]
    _remember(FP_DIR, session, repo, f"{state}:{lock.fingerprint(repo)}")

    notes = []
    if verdict["status"] == "acquired" and verdict.get("handoff"):
        notes.append(format_handoff(verdict))
    if warn := lock.needs_commit_warning(repo, session):
        notes.append(warn["message"])
    return None, notes


def settle_unknown(repo: str, session: str) -> list[str]:
    """Called AFTER it runs: keep the lock if it wrote, give it back if it did not.

    Unmoved fingerprint => it was a read, whatever it looked like, and the lock we took on spec was
    never needed. Release it now, so a session that only looked is not holding a working copy.

    Moved => it wrote. Keep the lock and hold it while the session stays active, exactly as a
    declared write would. Nobody had to recognise `./deploy.sh` for this to be true.
    """
    memo = _recall(FP_DIR, session, repo)
    if not memo:
        return []                              # nothing was staked on this call (e.g. the ticket)
    _forget(FP_DIR, session, repo)             # settled exactly once; a stale before-picture is a
                                               # fingerprint compared against the wrong moment
    status, _, before = memo.partition(":")

    if status == "background":
        # We are looking at the tree BEFORE the thing we launched has done anything. There is
        # nothing here to observe, and pretending otherwise is how the lock would quietly let go of
        # a repo that a live process is still writing. Keep it: the lease and the session's own
        # activity carry it, and the idle boundary decides at the end.
        return ["Holding the lock on this repo while your background task runs — its writes cannot "
                "be observed until it exits, so the lock is held rather than guessed at."]

    after = lock.fingerprint(repo)
    if before != after:                        # it wrote. We are a writer, and we hold the lock.
        remember_head(session, repo, env.git_head(repo))
        return []

    if status != "acquired":                   # we already held it for a real write — keep it
        return []

    # It read. Hand back the lock we took on speculation. `force` bypasses the dirty-tree refusal,
    # which is right and not a fudge: that refusal guards the IDLE boundary (do not hand a half-
    # finished tree to the next session). Here the fingerprint proves we changed nothing, so any
    # dirt in the tree is exactly the dirt we found — and holding a repo hostage over someone
    # else's uncommitted work, having written nothing ourselves, is #4 wearing a hat.
    lock.release(repo, session, force=True)
    return []
