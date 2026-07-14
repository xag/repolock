"""What every harness adapter shares: the courier and the witness. Nothing here refuses anything.

This file used to be a lock. It held a mutex through every shell, minted one-time tickets so that
refused sessions could wait, detected its own half-wired installs, and grew a new organ every time
the gate hurt someone (#4, #7, #10, #11). All of that is gone, and it is gone on purpose: the
project stopped blocking agents and started informing them. What remains is exactly two jobs:

  THE COURIER   tell an agent what it cannot see from inside its own context: who else is working
                this checkout and where (scope.touching); that the file it is about to edit sits in
                another agent's declared region (heads_up); that history moved under it (drift_note).
                Notes ride the hook's stdout into the agent's context on its next tool call — the
                one channel that reaches a running agent without interrupting it.

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

from transponder import env, scope, witness

SEEN_DIR = "seen"            # per-(session, repo): the last HEAD this session saw — the drift check
SNAP_DIR = "snap"            # ...and the witness's before-picture for the tool now running
NOTED_DIR = "noted"          # ...and whether the courier already introduced this shared checkout
WARNED_DIR = "warned"        # ...and whether we already said the witness's settle half is missing


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
    d = os.path.join(os.path.dirname(env.lock_dir()), kind)
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
    remember where the repo stands now. The one part of this library that was never wrong."""
    verdict = witness.drift(repo, last_seen_head(session, repo))
    remember_head(session, repo, verdict.get("head_commit"))
    if verdict["status"] in ("moved", "rewritten"):
        return verdict["message"]
    return None


# --- the courier -----------------------------------------------------------------------------------

def shared_note(repo: str, session: str) -> str | None:
    """Introduce a shared checkout, once. An agent cannot see the other agents from inside its own
    context — this is the fact it is missing, delivered at the first moment it matters and not
    repeated on every call (a note printed forever is a note nobody reads).

    This used to be a refusal that held the session out until it declared. It teaches the same
    protocol as a note, and a note cannot wedge the machine.
    """
    if scope.declared(session):
        return None                  # a participant got the map back with its grant; the intro is
                                     # for the agent that has not spoken yet
    claims = [c for c in scope.touching(repo) if c["session"] != session]
    if not claims or _recall(NOTED_DIR, session, repo):
        return None
    _remember(NOTED_DIR, session, repo, "1")

    out = [f"THIS CHECKOUT IS SHARED — you are not alone in {repo}:", ""]
    for c in claims:
        out.append(f"  agent {c['session']} is working {', '.join(c['scope'])}"
                   + (f" — {c.get('intent')}" if c.get("intent") else ""))
    out += [
        "",
        "Nothing is blocked. But their regions are their half-finished work: stay out of them, and",
        "SAY WHERE YOU WILL WRITE so they can stay out of yours:",
        "    declare_scope(repo, ['src/thing/**', 'tests/thing/**'], intent='what you are doing')",
        "Check `scopes(repo)` before planning. Reserve `.git/index` around a commit and release it",
        "after — `git add -A` sweeps up every dirty file in the checkout, including theirs.",
    ]
    return "\n".join(out)


def heads_up(repo: str, session: str, path: str, intent: str) -> list[str]:
    """A declared write (Edit/Write carry their path), checked against the map BEFORE it lands.

    This is information at its most valuable moment — the write has not happened yet — and it is
    still not a gate: the agent is warned, and proceeds. If it writes anyway, the witness reports
    the violation as a fact; but almost always the warning is enough, because the failure mode was
    never malice, it was not knowing the other agent existed.

    For an agent with a declared scope, writing into UNCLAIMED ground quietly extends its claim —
    the registry should say what participants are actually touching, and that is information too.
    """
    target = scope.canon(path)
    my = scope.scope_of(session)
    if scope.covers(my, target):
        scope.renew(session)
        return []

    for c in scope.touching(repo):
        if c["session"] != session and scope.covers(c["scope"], target):
            return [
                f"HEADS UP — {_rel(repo, path)} is inside agent {c['session']}'s declared region "
                f"({', '.join(c['scope'])}"
                + (f" — {c['intent']}" if c.get("intent") else "") + ").",
                "That is someone's work in progress. Nothing stops you, but if you write it anyway "
                "it will be witnessed and reported to both of you. Better: negotiate — pick a "
                "non-overlapping scope, or split the work and file an issue for their part.",
            ]

    if my:                                      # a participant on unclaimed ground: keep the map true
        v = scope.declare(repo, session, list(my) + [target], intent)
        if v["status"] == "granted":
            return [f"transponder: extended your scope to cover {_rel(repo, path)}."]
    return []


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
    _remember(SNAP_DIR, session, repo, json.dumps(witness.snapshot(repo)))
    return notes


def settle(repo: str, session: str, intent: str) -> list[str]:
    """The after-picture: name the paths that moved, and say whose region they landed in.

    Unmoved — almost every call — costs nothing and says nothing. Moved into your own scope: yours,
    renewed, silent. Moved into another agent's declared region: the loudest thing this library
    says, with the remedy attached, because it could not have been prevented (the target of a shell
    is not knowable before it runs — the old §7a proof still stands) and it must not be silent.
    """
    memo = _recall(SNAP_DIR, session, repo)
    if not memo:
        return []
    _forget(SNAP_DIR, session, repo)
    try:
        before = json.loads(memo)
    except (json.JSONDecodeError, ValueError):
        return []

    after = witness.snapshot(repo)
    written = witness.written_between(repo, before, after)
    if not written:
        return []                                   # it read. It cost nobody anything.

    written = [scope.canon(os.path.join(repo, p)) for p in written]
    scope.renew(session)

    notes = []
    if bad := scope.violations(session, written):
        notes.append(format_violation(repo, bad,
                                      head_moved=before.get("HEAD") != after.get("HEAD")))
    if scope.declared(session) and (loose := scope.stray(session, written)):
        notes.append(
            "transponder: you wrote outside your declared scope, into a region nobody has claimed:\n"
            + "\n".join(f"  {_rel(repo, p)}" for p in loose[:8])
            + "\nNobody was hurt — but the next agent cannot see that this is yours. Declare it: "
              "extend_scope(repo, [...]).")
    remember_head(session, repo, env.git_head(repo))
    return notes


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
        "You are not in trouble; you are being told before it becomes a mangled rebase. STOP, then:",
        "  1. `git status` / `git diff` — look at what is actually there.",
        "  2. Put back what was theirs. Do not commit it, do not 'fix' it.",
    ]
    if head_moved:
        out += [
            "  3. YOU COMMITTED THEIR WORK. This is the one violation that is cleanly recoverable,",
            "     so recover it now, before anything else lands on top:",
            "         git reset --soft HEAD~1     # un-commit, keep the tree",
            "         git restore --staged <their paths>",
            "     A `git add -A` sweeps up every dirty file in the checkout, including the ones",
            "     another agent is halfway through. Stage YOUR paths by name, never `-A`.",
        ]
    else:
        out.append("  3. Then declare the scope you actually needed, and carry on inside it.")
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
