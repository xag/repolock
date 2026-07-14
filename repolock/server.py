"""Repo Lock — a local (stdio) MCP server for seeing and overriding the working-copy lock.

The lock itself is taken and released automatically by a harness hook (for Claude Code:
repolock/hooks/claude_code.py); a model never has to remember to call anything, which is the
only reason the guarantee is worth having. This server is the *visibility and override* surface
on top of it — the questions a session actually needs answered when it walks into a locked repo.

Run (local only, extra `mcp`): `uv run python -m repolock.server`   (register in your client)

A note on identity, deliberately conservative. A stdio MCP server has no reliable way to learn
the harness session id that owns it, and the hook keys locks on exactly that id. Rather than
invent a second, mismatched notion of "who I am" — which would let this server release a lock it
does not own, or take one that fights the hook's — the tools here are either read-only or an
explicit, human-asked-for override. Acquiring is the hook's job alone. If a harness later
exposes the session id to MCP servers, `lock_repo`/`unlock_repo` become a five-line addition;
until then, a wrong identity model would be worse than no tool.
"""

from __future__ import annotations

import json
import os

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from repolock import env
from repolock import lock as repolock

mcp = FastMCP("repo-lock")

# Recording is ON by default (REPOLOCK_FLIGHT=0 to disable): the tape has to exist before the
# incident, not after it.
if env.recording():
    from repolock import flight
    flight.install()


def _fmt_lock(v: dict) -> str:
    lk = v.get("lock") or {}
    lines = [
        f"repo    : {v['repo']}",
        f"state   : {v['status']}" + (f" ({v['reason']})" if v.get("reason") else ""),
        f"holder  : session {lk.get('session')} (pid {lk.get('pid')})"
        + (f" — {lk['intent']}" if lk.get("intent") else ""),
        f"frees in: ~{int(v['expires_in'])}s" if v.get("expires_in") is not None else "",
        f"base    : {(lk.get('base_commit') or '?')[:12]}",
        f"head    : {(v.get('head_commit') or '?')[:12]}",
    ]
    if lk.get("idle_since"):
        lines.append("idle    : yes — the holder is waiting on its human")
    if v.get("dirty"):
        lines.append(f"tree    : DIRTY, {len(v['dirty'])} uncommitted change(s)")
    if v.get("takeable"):
        lines.append("takeable: yes — the next write will take it over, with a handoff")
    return "\n".join(x for x in lines if x)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def lock_status(repo: str) -> str:
    """Who holds the lock on a local working copy, since when, on what base commit, and when it
    frees. `repo` is a local path (e.g. `~/Projects/myrepo`).

    Returns IMMEDIATELY — it never waits. Waiting inside a tool call does not work: MCP kills a
    silent call at a hard idle timeout (we watched one die at exactly 300s), so a blocking
    "wait for the lock" would abort rather than wait, and would look like a server hang.

    Use the bounded answer it gives you to decide:
      - the work is BLOCKING (you cannot proceed without this repo) → wait for the lease to
        lapse, then retry; the next write takes the lock automatically.
      - the work is NOT blocking → file an issue with what you were about to do, and move on.
    """
    v = repolock.status(repo)
    if v["status"] == "unlocked":
        dirty = v.get("dirty") or []
        tail = f" (tree has {len(dirty)} uncommitted change(s))" if dirty else " (tree clean)"
        return f"{v['repo']} is UNLOCKED{tail}\nhead: {(v.get('head_commit') or '?')[:12]}"
    return _fmt_lock(v)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def lock_wait(repo: str, timeout_seconds: int = 240) -> str:
    """Wait for a locked working copy to free up, and return the moment it does.

    **This is how a blocked session waits, and it is not optional.** You cannot wait by yourself:
    waiting means running `sleep`, `sleep` is a shell command, and the shell is exactly what the
    lock refused you. This tool is the escape, because the hook does not gate MCP tools.

    It returns as soon as the lock frees (or lapses — a lapsed lock is yours: the next write takes
    it over with a handoff). If the holder is still working when the timeout runs out, it says so
    and tells you how much lease is left, so you can wait again or go and do something else.

    `timeout_seconds` is capped at 240 — MCP kills a silent call at a hard idle timeout (we watched
    one die at exactly 300s), so a longer wait would abort rather than wait and would look like a
    server hang.

    **This blocks your turn.** If you have other work to get on with, do not use it: the refusal
    hands you a one-time command that waits in the BACKGROUND and lets your harness wake you when
    the lock frees. Take that instead, and keep working.

    After it returns `free`, just retry the tool you were blocked on. The hook takes the lock.
    """
    v = repolock.wait_until_free(
        repo, timeout_seconds=min(float(timeout_seconds), repolock.MCP_MAX_WAIT_SECONDS))
    if v["status"] == "free":
        return v["message"] + "\nRetry the tool you were blocked on — the hook will take the lock."
    return _fmt_lock(repolock.status(repo)) + f"\n\n{v['message']}"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def lock_drift(repo: str, seen_head: str) -> str:
    """Has this working copy moved under you since you last looked? `seen_head` is the commit sha
    you last saw. No lock involved — this is the read-side check.

    The failure it catches has no other detector: nothing is corrupted, and a session simply goes
    on reasoning about commits that a concurrent rebase has already destroyed. If it reports
    `REWRITTEN`, throw away what you remember about this repo and re-read it."""
    v = repolock.drift(repo, seen_head)
    if v["status"] == "current":
        return f"{v['repo']} is unchanged at {(v.get('head_commit') or '?')[:12]}."
    out = [v["message"]]
    for c in v.get("commits_since") or []:
        out.append(f"  {c}")
    return "\n".join(out)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True,
                                      idempotentHint=True))
def force_unlock(repo: str, confirm_session: str) -> str:
    """Break a lock you do not own. The deliberate override, for when a holder is wedged.

    `confirm_session` must be the exact session id currently holding it — call `lock_status`
    first, show the user who they are about to interrupt, and get an explicit yes. Breaking a
    live lock lets two sessions write the same checkout at once, which is the precise thing this
    machinery exists to prevent, so it is never the routine answer: a lapsed lock is taken over
    automatically by the next write, with a handoff, and needs no force at all.
    """
    v = repolock.status(repo)
    if v["status"] == "unlocked":
        return f"{v['repo']} is not locked — nothing to break."

    holder = (v.get("lock") or {}).get("session")
    if confirm_session != holder:
        return (f"Refusing to force: {v['repo']} is held by session {holder!r}, not "
                f"{confirm_session!r}. Call lock_status and pass the exact holder.")

    if v.get("takeable"):
        return (f"No force needed — that lock is already {v.get('reason')}. The next write "
                f"takes it over automatically, with a handoff describing what changed.")

    out = repolock.release(repo, holder, force=True)
    dirty = v.get("dirty") or []
    warn = (f"\nWARNING: the tree has {len(dirty)} uncommitted change(s) belonging to the "
            f"session you just interrupted. Review them before you write.") if dirty else ""
    return f"Forced the lock on {out['repo']} away from session {holder}.{warn}"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def lock_debug(repo: str) -> str:
    """The raw lock record, as JSON. For when the prose above isn't enough."""
    return json.dumps(repolock.status(repo), indent=2, sort_keys=True, default=str)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True))
def lock_disable(reason: str, clear_held_locks: bool = True) -> str:
    """**The off switch.** Turn the working-copy lock off, on this machine, immediately — including
    in sessions that are already running and already wedged.

    Reach for this the moment the lock is doing damage: refusing work it should not refuse, holding
    a checkout nobody is using, or handing out a waiter that does not run. It is not a big red
    button to be afraid of — the lock is an optimisation on a convention, and a machine where nobody
    can edit anything is strictly worse than one where two sessions might collide.

    **This tool exists because the shell does not.** When the lock refuses a session, it refuses its
    shell — so an off switch spelled as a shell command is unreachable exactly when it is needed. The
    hook does not gate MCP tools, so this one always gets through. Never tell a blocked user to go
    and run something in a terminal; call this.

    It writes `~/.repolock/DISABLED`, which every adapter checks on every single call, so running
    sessions are freed on their very next tool use — no restart, no settings.json edit (a harness
    snapshots its hooks at startup and cannot see one anyway).

    `clear_held_locks` also drops the lockfiles, so nothing stale is left to resurrect when it goes
    back on. `reason` is written into the switch file for whoever finds it later — say what it did.

    Turn it back on with `lock_enable`.
    """
    from repolock import toggle

    v = toggle.disable(reason=reason, clear=clear_held_locks)
    out = ["repo-lock is now OFF — every hook, in every session, running or not, is a no-op.",
           f"reason: {reason}"]
    if v["was_holding"]:
        out.append(f"\nit was holding {len(v['was_holding'])} lock(s):")
        for h in v["was_holding"]:
            out.append(f"  {h['repo']}  session {(h['session'] or '?')[:8]}  {(h['intent'] or '')[:50]}")
    out.append(f"\n{len(v['cleared'])} lockfile(s) cleared." if v["cleared"]
               else "\nlockfiles left in place (they are inert while it is off).")
    out.append("Re-enable with lock_enable when the cause is fixed.")
    return "\n".join(out)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True))
def lock_enable() -> str:
    """Turn the working-copy lock back on: disarm the panic switch AND re-wire the harness hooks.

    Both halves, because "on" has to mean on. Removing the switch file while the hooks are missing
    from settings.json yields a repolock that reports itself enabled and guards nothing — worse than
    being off, because you would be relying on it.

    New sessions pick the hooks up at startup; sessions already running snapshotted their hooks when
    they started, so if the hooks had been removed those sessions stay unguarded until restarted.
    """
    from repolock import toggle

    v = toggle.enable()
    out = [toggle.render(toggle.state())]
    if not v["wired"]:
        out.append("\nWARNING: the hooks could not be written to settings.json — it is NOT guarding.")
    if v["stale_locks"]:
        out.append(f"\n{len(v['stale_locks'])} lapsed lock(s) remain on disk; the next write takes "
                   "them over with a handoff.")
    if v["env_override"] is not None:
        out.append(f"\nWARNING: REPOLOCK_DISABLED={v['env_override']!r} is set in this server's "
                   "environment and overrides the file.")
    return "\n".join(out)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def lock_switch() -> str:
    """Is the lock on? Is it wired into the harness? What is it holding right now?

    The one call that answers "why is/isn't this thing doing anything" — it distinguishes the three
    states that look alike from inside a session: ON, switched OFF, and the dangerous middle one
    where it believes it is on but its hooks were never wired.
    """
    from repolock import toggle

    return toggle.render(toggle.state())


# --- SPEC-v2: the channel (TRIAL) ---------------------------------------------------------------
#
# These are MCP tools, and that is not incidental — it is the whole reason v2 works. The hook never
# gates an MCP call (SPEC §7c), so an agent can ALWAYS reach the channel: including one that is
# currently held out of a checkout, which is precisely the agent that needs to negotiate.
#
# The identity caveat at the top of this file does NOT apply here. Acquiring the v1 lock needs the
# harness session id, which a stdio server cannot know — but a scope is declared BY the agent, so
# the agent passes its own session id, and it is passing a name for itself, not claiming one.

def _fmt_scopes(repo: str) -> str:
    from repolock import scope

    claims = scope.live(repo)
    if not claims:
        return f"{env.canonical(repo)}: nobody has reserved anything. It is all yours."
    out = [f"{env.canonical(repo)} — {len(claims)} agent(s) at work:"]
    for c in claims:
        out.append(f"  agent {c['session']}: {', '.join(c['scope'])}"
                   + (f"  — {c['intent']}" if c.get("intent") else ""))
    return "\n".join(out)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True))
def declare_scope(repo: str, scope: list[str], session_id: str, intent: str = "") -> str:
    """**Say what you are going to write, and you may work alongside other agents in one checkout.**

    This is the deal, and it is worth understanding before you use it. If you declare nothing, you
    are treated as claiming the ENTIRE checkout — so any other agent working it holds you out, and
    you hold them out. Declare a region and you get that region, exclusively, while everyone else
    gets on with theirs.

    `scope` is a list of resources. Only these forms exist, because they are the ones whose overlap
    can be computed exactly, and a scope system that is unsure whether two regions touch is worse
    than none:

        "src/api/**"     a subtree
        "src/api/x.py"   one file
        "git:index"      THE STAGING AREA — take this before you commit. `git add -A` sweeps up
                         every dirty file in the checkout, including the half-finished work of the
                         agent next door, and commits it as yours. Reserve the index, stage YOUR
                         paths by name, commit, release it.
        "git:HEAD"       commits, rebases, checkouts
        "port:3000"      a dev server, a debugger — anything a second agent would fight over
        "**"             everything (the default, and what you get by saying nothing)

    `session_id` is your harness session id — the same one the hook sees. Pass it exactly.

    Returns `granted`, or a `conflict` naming who holds what AND what is free right now. A conflict
    is an answer, not a wall: take a narrower scope and carry on, or split the work and file an issue
    for the part you cannot have. **Declaring is all-or-nothing** — you get the whole scope or none
    of it, which is what keeps two agents from deadlocking on each other's regions.

    Writes INSIDE your scope are yours. Writes outside it are watched, and if they land in someone
    else's region, both of you are told. Nothing stops you — this is a channel, not a cage — but do
    not do it: it is somebody's work.
    """
    from repolock import scope as sc

    v = sc.declare(repo, session_id, scope, intent)
    if v["status"] == "granted":
        return (f"GRANTED — {', '.join(v['claim']['scope'])} on {v['repo']}.\n"
                f"Write freely inside it. extend_scope() if you need more (it never blocks), and "
                f"release_scope() when you are done.\n\n{_fmt_scopes(repo)}")
    if v["status"] == "rejected":
        return f"REJECTED — {v['reason']}"

    out = ["CONFLICT — part of what you asked for is taken."]
    for c in v["conflicts"]:
        out.append(f"  {', '.join(c['scope'])} — agent {c['session']} "
                   f"({c['intent'] or 'no stated intent'}, {c['held_for']}s)")
    if v.get("free_hint"):
        out.append(f"\nFREE RIGHT NOW: {', '.join(v['free_hint'])}")
    out.append("\nTake a narrower scope and carry on, or file an issue for the part you cannot have "
               "and do the rest. Do not wait: there is almost always work you can do.")
    return "\n".join(out)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True))
def extend_scope(repo: str, add: list[str], session_id: str) -> str:
    """Widen the scope you already hold — for when you discover mid-task that you need one more
    module. **It never blocks.** It grants, or it tells you who is there.

    If it conflicts, do NOT sit and wait for them while holding what they want: that is the one way
    two agents can deadlock here. Commit what you have, release, and re-declare from a clean tree —
    or split the work off into an issue and carry on with what you can reach.
    """
    from repolock import scope as sc

    v = sc.extend(repo, session_id, add)
    if v["status"] == "granted":
        return f"GRANTED — your scope is now {', '.join(v['claim']['scope'])} on {v['repo']}."
    if v["status"] == "rejected":
        return f"REJECTED — {v['reason']}"
    out = ["CONFLICT — you cannot have that; it is somebody's."]
    for c in v["conflicts"]:
        out.append(f"  {', '.join(c['scope'])} — agent {c['session']} "
                   f"({c['intent'] or 'no stated intent'})")
    out.append("\nDo not block on this. Commit and re-declare, or split the work off into an issue.")
    return "\n".join(out)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True))
def release_scope(repo: str, session_id: str, drop: list[str] | None = None) -> str:
    """Give back your whole scope, or narrow it by dropping part (`drop`). Releasing what you are no
    longer using is how the next agent gets to work — and `git:index` in particular should be held
    for the length of a commit and not one second more."""
    from repolock import scope as sc

    v = sc.release(repo, session_id, drop)
    left = ", ".join(v["scope"]) if v["scope"] else "nothing (you are back to the default, `**`)"
    return f"Released. You now hold: {left}.\n\n{_fmt_scopes(repo)}"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def scopes(repo: str) -> str:
    """Who is working this checkout, and on what. Call it BEFORE you plan: it is how you find work
    that will not collide with anyone, instead of discovering the collision afterwards."""
    return _fmt_scopes(repo)


def main() -> None:
    mcp.run()  # stdio


if __name__ == "__main__":
    main()
