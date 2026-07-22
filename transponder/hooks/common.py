"""What every harness adapter shares: the courier and the witness. Nothing here refuses anything.

This file used to be a lock. It held a mutex through every shell, minted one-time tickets so that
refused sessions could wait, detected its own half-wired installs, and grew a new organ every time
the gate hurt someone (#4, #7, #10, #11). All of that is gone, and it is gone on purpose: the
project stopped blocking agents and started informing them. What remains is exactly two jobs:

  THE COURIER   tell an agent what it cannot see from inside its own context: who else is working
                this checkout and where (shared_note, once); that history moved under it
                (drift_note). Notes reach the model as `hookSpecificOutput.additionalContext` —
                NOT as stdout, which goes to a debug log and reached nobody for the whole life of
                v2. It is the one channel that informs a running agent without refusing its call,
                and it lands beside the tool result: the courier speaks between calls, never before
                one. That is why there is no pre-write warning here any more.

  THE WITNESS   observe what a tool call actually did (witness.snapshot before, diff after), and
                when a write lands inside another agent's declared region, say so LOUDLY, to the
                agent that did it, with the remedy attached. The claims registry says who intends
                what; the witness says what happened; the difference, delivered immediately, is the
                entire enforcement model.

One deliberate exception to "never refuses": hand_back may block the STOP of an agent that is
walking away from a dirty tree — once, to ask it to commit, ignore or stash. That refuses no other
agent anything, ever; it asks the departing agent itself not to leave a mess for the humans and
agents that come next. It is kept because demoting it to a note would make it prose that cannot
fire, which is the failure this project exists to kill.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess

from transponder import env, messages, scope, witness

SEEN_DIR = "seen"            # per-(session, repo): the last HEAD this session saw — the drift check
SNAP_DIR = "snap"            # ...and the witness's before-picture for the tool now running
NOTED_DIR = "noted"          # ...and whether the courier already introduced this shared checkout
WARNED_DIR = "warned"        # ...and whether we already said the witness's settle half is missing
# (an INBOX_DIR lived here for one afternoon; mail moved to transponder.messages, which addresses
#  three ways and marks read per reader instead of deleting for everyone)


def repo_root(cwd: str) -> str | None:
    try:
        res = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd,
                             capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    return res.stdout.strip() or None if res.returncode == 0 else None


def repo_of(path: str) -> str | None:
    """The repo that owns a FILE — keyed on the path, never on the session's cwd, which is a
    different repo often enough to matter (#8)."""
    if not path:
        return None
    d = os.path.dirname(os.path.abspath(path))
    while not os.path.isdir(d):                    # the file may not exist yet — walk up to a dir
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent
    return repo_root(d)


# --- the per-(session, repo) memories -------------------------------------------------------------

def _memo_path(kind: str, session: str, repo: str) -> str:
    """Keyed on the CANONICAL repo, for the reason the claims namespace is (filesystem-is-the-
    namespace): one checkout must not have several keys.

    It used to hash the string it was handed, and the callers do not agree on how to spell a repo —
    `repo_root()` returns git's `C:/Users/...`, `repo_of()` returns an abspath with backslashes, and
    anything outside the hooks passes whatever it has. Same checkout, different sha256, different
    memo. That silently broke more than the inbox that caught it: a session introduced to a shared
    checkout through a Bash (repo_root) could be introduced to it AGAIN through an Edit (repo_of),
    and the "once, or it is spam" guarantee is only as good as the key it is remembered under.
    """
    d = os.path.join(os.path.dirname(env.lock_dir()), kind)
    os.makedirs(d, exist_ok=True)
    key = hashlib.sha256(f"{session}:{env.canonical(repo)}".encode()).hexdigest()[:16]
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


def post(victim: str, repo: str, note: str) -> None:
    """Leave a note FOR the agent whose region was written — the only party that knows what its own
    half-finished work was.

    Until this existed, `victim` appeared in exactly two places in the library: computed in
    scope.violations, and rendered into the OFFENDER's message. The agent whose work was overwritten
    was never addressed by anything. So the remedy had to ask the offender to restore bytes it had
    never seen, which is the one move this library rejects everywhere else — predicting what must
    have been there instead of observing it — and it made the offender write into the region again
    to do it, tripping the alarm a second time on an agent that was complying.

    Carried by transponder.messages as a DIRECT message from `transponder` itself, rather than by a
    store of its own. One substrate: the violation report and an agent's own "I am about to rewrite
    the auth middleware" travel the same route, are marked read the same way, and cannot drift apart
    in behaviour. It is also why reading stopped being destructive — a per-reader seen-set clears a
    message for the agent that read it and leaves it standing for anyone else it was sent to.
    """
    messages.send(sender="transponder", body=note, kind="direct", repo=repo, to=victim)


def collect(session: str, repo: str) -> list[str]:
    """Take delivery of what was addressed TO this agent — direct only, which is the whole line
    between the courier and a feed. Channel and broadcast traffic is never pushed; an agent that
    wants the room calls `messages()` and asks."""
    return [messages.render(m) for m in messages.unread(session, repo, kinds=("direct",))]


def remember_head(session: str, repo: str, head: str | None) -> None:
    _remember(SEEN_DIR, session, repo, head)


def last_seen_head(session: str, repo: str) -> str | None:
    return _recall(SEEN_DIR, session, repo)


def drift_note(session: str, repo: str) -> str | None:
    """The read-side check, packaged: report a move/rewrite since this session last looked, and
    remember where the repo stands now. The one part of this library that was never wrong."""
    verdict = witness.drift(repo, last_seen_head(session, repo))
    remember_head(session, repo, verdict.get("head_commit"))
    if verdict["status"] in ("moved", "rewritten"):
        return verdict["message"]
    return None


# --- the courier -----------------------------------------------------------------------------------

def shared_note(session: str) -> str | None:
    """Introduce the machine, once per session. An agent cannot see the other agents from inside its
    own context — this is the fact it is missing, and it is delivered without anyone having to work
    out where the agent "is".

    It used to introduce ONE CHECKOUT, chosen from the session's cwd, which meant an agent was told
    about its neighbours only if it happened to be sitting in the same folder as them — and an agent
    editing across checkouts (the ordinary case for anyone with a lib and its client open) was told
    nothing at all. Guessing where an agent is was never necessary here: the thing it needs to know
    is that the machine is shared and that it should say what it will edit.
    """
    if scope.declared(session):
        return None                  # a participant got the map back with its grant; the intro is
                                     # for the agent that has not spoken yet
    claims = [c for c in scope.live() if c["session"] != session]
    if not claims or _recall(NOTED_DIR, session, "machine"):
        return None
    _remember(NOTED_DIR, session, "machine", "1")

    out = ["YOU ARE NOT THE ONLY AGENT ON THIS MACHINE.", ""]
    for c in claims:
        out.append(f"  agent {c['session']} holds {', '.join(c['scope'])}"
                   + (f" — {c.get('intent')}" if c.get("intent") else ""))
    out += [
        "",
        "Nothing is blocked, ever. But those regions are somebody's half-finished work.",
        "",
        "DECLARE THE FILES AND FOLDERS YOU INTEND TO EDIT — the ones you will WRITE TO, which is",
        "not always the checkout you are sitting in. Nothing watches a region nobody declared, so",
        "an undeclared file is one where a collision is neither prevented nor reported:",
        "    declare_scope(repo, ['src/thing/**', 'tests/thing/**'], intent='what you are doing')",
        "Widen with extend_scope() the moment you find you need more; release_scope() when done.",
        "",
        "AND SAY WHAT YOU ARE DOING, in a line or two — you are all building one app for one",
        "person, and the map can only say where you are, never what is coming:",
        "    send_message(repo, session_id, 'replacing the auth middleware return type this hour')",
        "That is what lets them write the caller once, for the shape it is about to have, instead",
        "of writing it twice. `messages(repo, session_id)` is how you hear theirs — it is pulled,",
        "never pushed, so ask before you plan anything large.",
        "",
        "Check `scopes(repo)` before planning. Reserve `.git/index` around a commit and release it",
        "after — `git add -A` sweeps up every dirty file in the checkout, including theirs.",
    ]
    return "\n".join(out)


# heads_up() stood here: the pre-write warning for Edit/Write, checked against the map BEFORE the
# write landed. It is deleted, not disabled, because THE MOMENT IT WAS WRITTEN FOR DOES NOT EXIST.
#
# A hook cannot put text in front of a Claude Code agent before its tool runs without refusing the
# call: plain stdout goes to a debug log, and `additionalContext` from PreToolUse is delivered next
# to the TOOL RESULT — after the write. The only pre-execution channel is exit 2, which blocks, and
# this library does not block. So the warning arrived after the thing it warned about, wearing the
# grammar of a warning ("if you write it anyway..."), addressed to an agent that already had.
#
# Keeping it would have meant two code paths saying the same thing at the same moment in different
# words, and a docstring promising a guarantee the harness cannot give. Edit/Write now settle
# exactly like a shell: observed after, reported as a fact, remedy attached. One path, one moment,
# one wording — and observe-do-not-predict was always the honest form of this.
#
# What genuinely still arrives BEFORE any write is not here and does not need to be: the
# `declare_scope` conflict answer (an MCP reply, straight into the agent's context) and the
# shared-checkout intro at UserPromptSubmit/SessionStart, whose stdout the harness does put in
# front of the model.
#
# One behaviour left with it: a participant writing UNCLAIMED ground used to have its claim
# silently extended. settle() covers that case as a note asking for extend_scope() — the same
# treatment a shell has always had, and it does not mutate the map behind the agent's back.


def _rel(repo: str, path: str) -> str:
    """For DISPLAY only — messages read better in repo-relative terms. Claims never store this."""
    p = os.path.abspath(path).replace("\\", "/")
    r = env.canonical(repo).replace("\\", "/")
    return p[len(r):].lstrip("/") if p.lower().startswith(r.lower()) else p


# --- the witness -----------------------------------------------------------------------------------

def watch(repo: str, session: str) -> list[str]:
    """The before-picture, for any tool whose effect is not declared (a shell, an MCP call).

    A surviving snapshot memo means the settle half never ran — a half-wired install. With the lock
    that used to make this dangerous gone, the cost is only blindness; but a blind witness that
    everyone believes is watching is the vacuously-green failure, so it is said out loud, once.
    """
    notes = []
    if _recall(SNAP_DIR, session, repo) and not _recall(WARNED_DIR, session, repo):
        _remember(WARNED_DIR, session, repo, "1")
        notes.append("transponder: the witness's settle half (PostToolUse) is not wired, so writes "
                     "here are NOT being observed. Fix the hooks (python -m transponder.toggle on) and "
                     "restart this session — it snapshotted its hooks when it started.")
    _remember(SNAP_DIR, session, repo, json.dumps({"snap": witness.snapshot(repo)}))
    return notes


def settle(repo: str, session: str, intent: str, declared: str = "") -> list[str]:
    """The after-picture: name the paths that moved, and say whose region they landed in — and be
    honest about which of those two facts we actually have.

    A FINGERPRINT PROVES THE TREE MOVED. IT DOES NOT PROVE YOU MOVED IT. That distinction was
    missing here, and it produced fiction the first time two agents ran at once: a holder appending
    to its own declared file every ten seconds, and a passer-by whose only crime was a `head`/`wc`
    loop long enough to span a tick. Four notes went out naming the reader as the author, and four
    more went to the holder saying its work had been trampled by someone who never wrote a byte.
    The claim in observe-do-not-predict — "an observation cannot be wrong about what a command
    did" — is true of one agent and false of two, which is to say false in the only situation this
    library exists for.

    So attribution is now graded by the evidence actually in hand:

      DECLARED   the tool named the path it would write (Edit/Write). That path moving IS this
                 session's doing, and it is reported and delivered exactly as before.
      OBSERVED   a shell or MCP call, and something in another agent's region moved during it. If
                 that agent was ACTIVE in the same window — its claim renewed inside it — the far
                 likelier author is the agent that declared the region and is working in it, so the
                 writer gets a hedged note and THE HOLDER IS TOLD NOTHING. A false accusation
                 delivered to a victim is worse than silence: it is the one channel built to tell
                 them the truth.
      Otherwise it is still not certain, but no other author is known, so it is reported both ways
      in hedged terms rather than as a proven trespass.
    """
    memo = _recall(SNAP_DIR, session, repo)
    if not memo:
        return []
    _forget(SNAP_DIR, session, repo)
    try:
        memo_obj = json.loads(memo)
    except (json.JSONDecodeError, ValueError):
        return []
    # A memo written before the wrapper existed is the bare snapshot itself.
    before = memo_obj.get("snap", memo_obj) if isinstance(memo_obj, dict) else memo_obj

    after = witness.snapshot(repo)
    written = witness.written_between(repo, before, after)
    if not written:
        return []                                   # it read. It cost nobody anything.

    written = [scope.canon(os.path.join(repo, p)) for p in written]
    scope.renew(session)

    notes = []
    if bad := scope.violations(session, written):
        head_moved = before.get("HEAD") != after.get("HEAD")
        target = scope.canon(declared) if declared else None
        ours = [v for v in bad if v["path"] == target]
        unknown = [v for v in bad if v["path"] != target]

        if ours:
            notes.append(format_violation(repo, ours, head_moved=head_moved))
            for victim, paths in _by_victim(ours).items():
                post(victim, repo, format_intrusion(repo, session, paths, head_moved))
        if unknown:
            # Told to the ONE party that knows the answer, and to nobody else. The witness cannot
            # name an author here, so it does not name one — not "probably the owner", not "probably
            # you". The agent reading this knows whether it wrote, and the channel exists for it to
            # say so.
            notes.append(format_unattributed(repo, unknown, head_moved=head_moved))
    if scope.declared(session) and (loose := scope.stray(session, written)):
        notes.append(
            "transponder: you wrote outside your declared scope, into a region nobody has claimed:\n"
            + "\n".join(f"  {_rel(repo, p)}" for p in loose[:8])
            + "\nNobody was hurt — but the next agent cannot see that this is yours. Declare it: "
              "extend_scope(repo, [...]).")
    remember_head(session, repo, env.git_head(repo))
    return notes


def format_unattributed(repo: str, bad: list[dict], head_moved: bool = False) -> str:
    """A region you do not hold changed while your call ran, and nobody knows who did it.

    Two wordings were tried here first and both were guesses wearing an observation's clothes. "You
    just wrote inside another agent's reserved region" was fiction the first time two agents ran at
    once — a holder appending to its own declared file every ten seconds, a passer-by whose only
    crime was a read loop long enough to span a tick, four false accusations to each party. Grading
    it by whether the owner had recently renewed was no better: renewal proves an agent was AWAKE,
    never that it touched a file, and "nobody else was awake, so it was probably you" is the same
    inference with less behind it.

    What is known is the whole of what is said. The agent reading this is the only party in the
    system that knows whether it wrote, so it is the only party told — and it has a channel to say
    so if it did.
    """
    out = ["A REGION YOU DO NOT HOLD CHANGED WHILE YOUR CALL WAS RUNNING.",
           f"  repo: {repo}", ""]
    for v in bad:
        out.append(f"  {_rel(repo, v['path'])}")
        out.append(f"     belongs to agent {v['victim']} ({', '.join(v['scope'])})"
                   + (f" — {v['intent']}" if v["intent"] else ""))
    out += [
        "",
        "The witness saw the tree move; it cannot see who moved it. Your command may have done this,",
        "or the owner may have been working in their own region at the same moment — from outside,",
        "those are the same picture. So nothing has been said to them, and no one is being accused.",
        "",
        "YOU know which it was:",
        "  * if it was you — stop writing here, leave it exactly as it stands, and tell them:",
        "        send_message(repo, session_id, 'I wrote <paths>; sorry, stopping', to='<agent>')",
        "    then declare the scope you actually needed. Do not restore their work by guess.",
        "  * if it was not you — there is nothing to do. This note is the whole of it.",
    ]
    if head_moved:
        out += [
            "",
            "HISTORY MOVED during this call, and the new commit contains the paths above — the",
            "founding incident of this library is `git add -A` sweeping a neighbour's half-finished",
            "work into somebody else's commit. IF THAT COMMIT IS YOURS, it is the one violation that",
            "is cleanly recoverable, and recovering it now is cheap:",
            "        git reset --soft HEAD~1     # un-commit, keep the tree",
            "        git restore --staged <their paths>",
            "    then stage YOUR paths by name, never `-A`.",
            "IF IT IS NOT YOURS, do NOT run that — you would be undoing somebody else's commit on a",
            "guess, which is the mistake this message exists to avoid making on your behalf.",
        ]
    return "\n".join(out)


def format_violation(repo: str, bad: list[dict], head_moved: bool) -> str:
    """A write landed in another agent's declared region. It was not prevented — nothing is — so
    the least both parties are owed is the truth, immediately, with the remedy attached."""
    out = ["SCOPE VIOLATION — you just wrote inside another agent's reserved region.",
           f"  repo: {repo}", ""]
    for v in bad:
        out.append(f"  {_rel(repo, v['path'])}")
        out.append(f"     belongs to agent {v['victim']} ({', '.join(v['scope'])})"
                   + (f" — {v['intent']}" if v["intent"] else ""))
    out += [
        "",
        "You are not in trouble; you are being told before it becomes a mangled rebase.",
        "THEY HAVE BEEN TOLD TOO — a note is waiting for them, naming you and these paths.",
        "",
        "STOP, and do NOT try to put their work back. You cannot: what you overwrote may never",
        "have been committed, so nothing you can read tells you what it was, and restoring it by",
        "guess is a second write into a region that is still not yours. They know what they were",
        "doing. Leaving it for them is the fix, not laziness.",
        "  1. Stop writing here. Leave the file exactly as it is now.",
        "  2. `git status` / `git diff` — look, so you can say what you did if asked.",
    ]
    if head_moved:
        out += [
            "  3. YOU COMMITTED THEIR WORK, and that one IS yours to undo — it is your commit, and",
            "     it is cleanly recoverable. Do it now, before anything lands on top:",
            "         git reset --soft HEAD~1     # un-commit, keep the tree",
            "         git restore --staged <their paths>",
            "     A `git add -A` sweeps up every dirty file in the checkout, including the ones",
            "     another agent is halfway through. Stage YOUR paths by name, never `-A`.",
        ]
    else:
        out.append("  3. Declare the scope you actually needed, and carry on inside it.")
    return "\n".join(out)


def _by_victim(bad: list[dict]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for v in bad:
        out.setdefault(v["victim"], []).append(v["path"])
    return out


def format_intrusion(repo: str, offender: str, paths: list[str], head_moved: bool) -> str:
    """What the VICTIM is told, on its next tool call. Deliberately not a mirror of the offender's
    message: that one says stop, this one says look. Only this agent knows what its work was, so
    only it can decide whether what is on disk now is a loss, a merge, or fine.

    It is sent ONLY when the write can be attributed — the tool named the path, or nobody else was
    working here. This message is the one thing the victim acts on, and telling it "X wrote here"
    when all we know is "this changed while X was running" spends the credibility of every note
    after it. The unattributable case is told to the passer-by and to nobody else.
    """
    out = ["SOMEONE WROTE IN YOUR REGION — while you were working, another agent wrote here:",
           f"  repo: {repo}", ""]
    for p in paths[:8]:
        out.append(f"  {_rel(repo, p)}")
    out += [
        "",
        f"  by agent {offender}, who has been told to stop and to leave it for you.",
        "",
        "LOOK BEFORE YOU CARRY ON. Your picture of these files is from before that write, so an",
        "edit made against what you remember can overwrite it a second time — this time by you.",
        "  1. `git diff` / re-read the files above. Decide: keep theirs, merge, or restore yours.",
        "  2. Only you know what was half-finished here. Nobody else can make that call.",
    ]
    if head_moved:
        out.append("  3. HISTORY MOVED as well — commits you remember may have been replaced. "
                   "Re-read the log before reasoning about it.")
    return "\n".join(out)


# --- the one exception: the Stop boundary ----------------------------------------------------------

def format_dirty_handback(repo: str, dirty: list[str]) -> str:
    """What a departing agent is told, once, when it walks away from a dirty tree it was working.

    Three routes, not one, because "commit your work" is the wrong instruction for two of the three
    things actually in a dirty tree: an artifact must be ignored, and a scrap should be stashed.
    """
    out = [
        "DON'T LEAVE A DIRTY CHECKOUT BEHIND — commit, ignore, or stash first.",
        "",
        f"You are handing control back with {len(dirty)} uncommitted change(s) in {repo}. Nothing "
        "locks this tree while you are away: the next agent walks straight into your half-finished "
        "edits, and a `git add -A` of theirs sweeps your work into their commit.",
        "",
        "In the tree right now:",
    ]
    out += [f"  {c}" for c in dirty[:12]]
    if len(dirty) > 12:
        out.append(f"  ...and {len(dirty) - 12} more")
    out += [
        "",
        "Pick the one that is actually true of each, then stop again:",
        "  * IT IS YOUR WORK  → commit it:  git add <your paths> && git commit -m \"...\"",
        "  * IT IS AN ARTIFACT  (a data dir, build output, a cache) → ignore it:",
        "        echo '<path>/' >> .gitignore && git add .gitignore && git commit -m \"ignore <path>\"",
        "  * IT IS HALF-FINISHED AND NOT WORTH A COMMIT  → git stash push -u -m \"wip: <what>\"",
        "",
        "You will not be asked twice. If it genuinely must sit uncommitted, just stop again — your",
        "declared scope stays on the map until its lease lapses, so others can still see it is yours.",
    ]
    return "\n".join(out)


def hand_back(repo: str, session: str, already_asked: bool = False) -> tuple[str | None, list[str]]:
    """The Stop boundary: (block, notes). The single place this library still says no — once, to
    the DEPARTING agent itself, about its own mess. It refuses no other agent anything, ever.

    Clean tree: the session's claims in this checkout are released (information that is no longer
    true should leave the map), and it goes with a clear conscience. Dirty tree, and this session
    was a participant here: ask once. Declined: its claims stay on the map until the lease lapses —
    the honest state, since the work IS still there.
    """
    involved = scope.declared(session) and any(
        c["session"] == session for c in scope.touching(repo))
    dirty = env.git_dirty(repo)

    if not dirty:
        scope.release_under(session, repo)
        return None, []
    if involved and not already_asked:
        return format_dirty_handback(repo, dirty), []
    return None, []
