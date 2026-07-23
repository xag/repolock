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
NOTED_DIR = "noted"          # ...and whether the courier already introduced this shared checkout
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

def shared_note(session: str, cwd: str = "") -> str | None:
    """Introduce the machine, once per session. An agent cannot see the other agents from inside its
    own context — this is the fact it is missing, and it is delivered without anyone having to work
    out where the agent "is".

    It used to introduce ONE CHECKOUT, chosen from the session's cwd, which meant an agent was told
    about its neighbours only if it happened to be sitting in the same folder as them — and an agent
    editing across checkouts (the ordinary case for anyone with a lib and its client open) was told
    nothing at all. Guessing where an agent is was never necessary here: the thing it needs to know
    is that the machine is shared and that it should say what it will edit.

    IT ALSO USED TO REQUIRE SOMEBODY ELSE TO BE ON THE MAP ALREADY — `if not claims: return None` —
    and that is the one condition under which it cannot do its job. The map starts empty. The first
    agent of the day was therefore told nothing, so it declared nothing, so the map was STILL empty
    when the second agent arrived an hour later, so that one was told nothing either: two agents in
    one checkout, each invisible to the other, for a whole working day, until a human noticed and
    said use the transponder. The intro is what bootstraps the map, so it cannot be gated on the map
    being non-empty; the empty map is precisely when nobody has been told yet.

    So it speaks either way, and it says which way it is — an empty map is a real fact about the
    machine, not a reason for silence. At most twice per session: once on arrival, and once more if
    an agent that was introduced to an empty map is later joined by someone, because "you are alone"
    is the one thing this note can say that stops being true on its own.

    On an empty map it needs one thing back: `cwd`, and a checkout there. Somebody else's live claim
    is reason enough to introduce an agent wherever it is sitting (it may be editing across
    checkouts — that is the ordinary case). Nobody's claim and no checkout under it is a session
    with nothing to collide over, and telling it how to declare is spam.
    """
    if scope.declared(session):
        return None                  # a participant got the map back with its grant; the intro is
                                     # for the agent that has not spoken yet
    claims = [c for c in scope.live() if c["session"] != session]
    said = _recall(NOTED_DIR, session, "machine")
    if said and (said != "alone" or not claims):
        return None                  # already introduced — and if that intro was the empty-map one,
                                     # arrivals since then are news (a legacy "1" counts as done)
    if not claims and scope.why_not_a_checkout(cwd or os.getcwd()):
        return None                  # a filesystem walk, not a `git` call: this runs on every tool
                                     # call of a session that is never going to be introduced
    _remember(NOTED_DIR, session, "machine", "roster" if claims else "alone")

    if claims:
        out = ["YOU ARE NOT THE ONLY AGENT ON THIS MACHINE.", ""]
        for c in claims:
            out.append(f"  agent {c['session']} holds {', '.join(c['scope'])}"
                       + (f" — {c.get('intent')}" if c.get("intent") else ""))
    else:
        out = [
            "NOBODY HAS DECLARED ANYTHING ON THIS MACHINE — which is not the same as being alone.",
            "",
            "An empty map means only that nobody has spoken yet, and you cannot see the other",
            "agents from inside your own context. You are reading this because being first is",
            "exactly when declaring matters: the next agent to arrive is told about you if you",
            "declared, and told nothing if you did not.",
        ]
    out += [
        "",
        "Nothing here will ever block a tool call. But nothing watches for collisions either: if",
        "you write where somebody else is working, that work is simply lost, and neither of you",
        "finds out until it hurts. The agreement happens BEFORE the work, or it does not happen.",
        "",
        "BEFORE YOU EDIT ANYTHING IN A SHARED CHECKOUT:",
        "  1. channel(repo, session_id, path='what you mean to work on')",
        "       who is there, and everything waiting for you. Nothing is pushed reliably —",
        "       asking is how you find out.",
        "  2. declare_work(repo, session_id, paths, doing, minutes)",
        "       AND WAIT FOR THE GREEN LIGHT IT RETURNS. `paths` is what you will WRITE TO, in",
        "       the checkout you write to — not always the one you are sitting in.",
        "  3. NOT CLEAR? Do one of three things, and say out loud which: take different work",
        "       (the answer lists what is free), go back to your human, or wait — it tells you",
        "       how to wait in the background instead of spinning.",
        "  4. finish_work(...) THE MOMENT YOU ARE DONE. Somebody may be waiting on exactly that.",
        "",
        "Talk while you work. Your claim said what you are doing; the channel is for what changes",
        "after it, and for whoever is working near you on purpose:",
        "    send_message(repo, session_id, 'replacing the auth middleware return type this hour')",
        "That is what lets the agent beside you write its caller once, for the shape it is about",
        "to have, instead of writing it twice.",
        "",
        "Reserve `.git/index` around a commit and release it after: `git add -A` sweeps up every",
        "dirty file in the checkout, including a neighbour's half-finished work.",
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


# The witness stood here: watch() took a fingerprint of every checkout on the map before a tool
# call, settle() diffed it after, and anything that had moved inside somebody else's region was
# reported. It is deleted, and the reason is not that it was expensive — it was that it could not
# do the one thing it claimed. A fingerprint proves the TREE MOVED. It cannot prove who moved it,
# and with two agents running that is not a corner case, it is the normal case: a holder appending
# to its own declared file and a passer-by whose call merely lasted longer than the gap between
# two of those appends produce the same picture from outside. It said so out loud four times in one
# afternoon, naming a reader as the author of writes it never made, and telling the holder its work
# had been trampled by an agent that never wrote a byte.
#
# So detection is gone, and nothing replaces it. The agreement happens BEFORE the work, in
# declare_work(), which is the only moment anybody actually knows what they are about to do. An
# agent that suspects something changed under it asks the channel and can write to whoever it finds
# there. That is weaker, and it is honest, and it does not manufacture facts.

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
