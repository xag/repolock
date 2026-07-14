"""Repo Scope — a local (stdio) MCP server: the negotiation channel for shared checkouts.

The hooks (transponder/hooks/claude_code.py) are the courier and the witness: they inform agents and
observe writes, and they never refuse a tool call. This server is the channel those notes point at
— where an agent says what it will write, sees what everyone else said, and where a human (or a
wedged session) turns the whole thing off.

Run (local only, extra `mcp`): `uv run python -m transponder.server`   (register in your client)

Identity: a claim is declared BY an agent about itself, so every scope tool takes the agent's own
harness `session_id` — the same id the hooks see. Passing a name for yourself is not the identity
problem the old lock server had (it refused to guess who it was acting for); saying who YOU are is
exactly the declaration the protocol wants.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from transponder import env, witness

mcp = FastMCP("transponder")

# Recording is ON by default (TRANSPONDER_FLIGHT=0 to disable): the tape has to exist before the
# incident, not after it.
if env.recording():
    from transponder import flight
    flight.install()


def _fmt_scopes(repo: str) -> str:
    from transponder import scope

    claims = scope.touching(repo)
    dirty = env.git_dirty(repo)
    head = (env.git_head(repo) or "?")[:12]
    tree = f"tree {'DIRTY, ' + str(len(dirty)) + ' change(s)' if dirty else 'clean'}, head {head}"
    if not claims:
        return f"{env.canonical(repo)}: nobody has declared anything ({tree})."
    out = [f"{env.canonical(repo)} — {len(claims)} agent(s) at work ({tree}):"]
    for c in claims:
        out.append(f"  agent {c['session']}: {', '.join(c['scope'])}"
                   + (f"  — {c['intent']}" if c.get("intent") else ""))
    return "\n".join(out)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True))
def declare_scope(repo: str, scope: list[str], session_id: str, intent: str = "") -> str:
    """**Say where you will write, so other agents working this checkout can stay out of your way
    — and you out of theirs.**

    Nothing is enforced and nothing is blocked: the map is information, the witness reports what
    actually happens against it, and the agents — who all work for the same human — keep out of
    each other's regions because breaking each other's work serves nobody.

    `scope` is a list of **filesystem paths** — one namespace, whose overlap is always computable,
    so a conflict names the exact intersection instead of guessing:

        "src/api/**"     a subtree (relative paths resolve against `repo`)
        "src/api/x.py"   one file
        ".git/index"     THE STAGING AREA — reserve it around a commit and release it after.
                         `git add -A` sweeps up every dirty file in the checkout, including the
                         half-finished work of the agent next door. Reserve the index, stage YOUR
                         paths by name, commit, release.
        "**"             the whole checkout

    Spelling does not matter — case, symlinks, `..` all resolve to one canonical path, so two
    agents cannot hold one file under two names.

    `session_id` is your harness session id — the same one the hooks see. Pass it exactly.

    Returns `granted`, or a `conflict` naming who holds what, the exact OVERLAP to subtract, and
    what is free right now. A conflicting scope is not registered (the map never double-books a
    region), but nothing stops your work — take a narrower scope, or split the work and file an
    issue for the part that is theirs. Do NOT just write into their region: the witness will see
    it, and both of you will be told.
    """
    from transponder import scope as sc

    v = sc.declare(repo, session_id, scope, intent)
    if v["status"] == "granted":
        return (f"ON THE MAP — {', '.join(v['claim']['scope'])}.\n"
                f"Other agents now see this is yours. extend_scope() if you find you need more, "
                f"release_scope() when you are done.\n\n{_fmt_scopes(repo)}")
    if v["status"] == "rejected":
        return f"NOT EXPRESSIBLE — {v['reason']}"

    out = ["CONFLICT — part of what you asked for is already someone's."]
    for c in v["conflicts"]:
        out.append(f"  {', '.join(c['scope'])} — agent {c['session']} "
                   f"({c['intent'] or 'no stated intent'}, {c['held_for']}s)")
        if c.get("intersection"):
            out.append(f"    overlap: {', '.join(c['intersection'])}   <- subtract exactly this")
    if v.get("free_hint"):
        out.append(f"\nFREE RIGHT NOW: {', '.join(v['free_hint'])}")
    out.append("\nNothing is blocking you, but their region is their half-finished work. Take a "
               "narrower scope and carry on, or file an issue for the part you cannot have.")
    return "\n".join(out)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True))
def extend_scope(repo: str, add: list[str], session_id: str) -> str:
    """Widen the scope you already hold — for when you discover mid-task that you need one more
    module. It answers immediately: granted, or who is there and exactly where you overlap."""
    from transponder import scope as sc

    v = sc.extend(repo, session_id, add)
    if v["status"] == "granted":
        return f"ON THE MAP — your scope is now {', '.join(v['claim']['scope'])}."
    if v["status"] == "rejected":
        return f"NOT EXPRESSIBLE — {v['reason']}"
    out = ["CONFLICT — that region is someone's."]
    for c in v["conflicts"]:
        out.append(f"  {', '.join(c['scope'])} — agent {c['session']} "
                   f"({c['intent'] or 'no stated intent'})")
        if c.get("intersection"):
            out.append(f"    overlap: {', '.join(c['intersection'])}")
    out.append("\nAsk them to release it (via your human, for now), or split the work off into an "
               "issue and keep going with what you can reach.")
    return "\n".join(out)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True))
def release_scope(repo: str, session_id: str, drop: list[str] | None = None) -> str:
    """Take yourself off the map — entirely, or narrow by dropping the entries in `drop` (resolved
    against `repo`). Do it the moment a region stops being yours: a map that says things that are
    no longer true is worse than no map, and `.git/index` in particular should be held for the
    length of a commit and not one second more."""
    from transponder import scope as sc

    v = sc.release(session_id, drop, anchor=repo)
    left = ", ".join(v["scope"]) if v["scope"] else "nothing — you are off the map"
    return f"Released. You now hold: {left}.\n\n{_fmt_scopes(repo)}"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def scopes(repo: str) -> str:
    """Who is working this checkout and where, plus the tree's state. Call it BEFORE you plan:
    it is how you pick work that will not land in the middle of someone else's."""
    return _fmt_scopes(repo)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def lock_drift(repo: str, seen_head: str) -> str:
    """Has this working copy moved under you since you last looked? `seen_head` is the commit sha
    you last saw. The failure it catches has no other detector: nothing is corrupted, and a session
    simply goes on reasoning about commits a concurrent rebase already destroyed. If it reports
    REWRITTEN, throw away what you remember about this repo and re-read it."""
    v = witness.drift(repo, seen_head)
    if v["status"] == "current":
        return f"{v['repo']} is unchanged at {(v.get('head_commit') or '?')[:12]}."
    out = [v["message"]]
    for c in v.get("commits_since") or []:
        out.append(f"  {c}")
    return "\n".join(out)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True))
def lock_disable(reason: str, clear_held_locks: bool = True) -> str:
    """**The off switch.** Turn the courier, the witness and the claims map off, on this machine,
    immediately — including in sessions already running.

    An information layer cannot wedge the machine the way the old lock could, but it can be wrong,
    noisy, or slow, and off must still mean off. It writes `~/.transponder/DISABLED`, which every hook
    checks on every call, so running sessions go quiet on their next tool use — no restart, no
    settings.json edit (a harness snapshots its hooks at startup and cannot see one anyway).

    `clear_held_locks` also drops the claim files, so the map is empty when it comes back on.
    `reason` is written into the switch file for whoever finds it later.
    """
    from transponder import toggle

    v = toggle.disable(reason=reason, clear=clear_held_locks)
    out = ["transponder is now OFF — every hook, in every session, running or not, is a no-op.",
           f"reason: {reason}"]
    if v["was_holding"]:
        out.append(f"\nthe map was carrying {len(v['was_holding'])} claim(s):")
        for h in v["was_holding"]:
            out.append(f"  {(h['session'] or '?')[:8]}  {', '.join(h['scope'])}  "
                       f"{(h['intent'] or '')[:50]}")
    out.append(f"\n{len(v['cleared'])} claim(s) cleared." if v["cleared"]
               else "\nclaims left in place (they are inert while it is off).")
    out.append("Re-enable with lock_enable when the cause is fixed.")
    return "\n".join(out)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True))
def lock_enable() -> str:
    """Turn it back on: disarm the switch AND re-wire the harness hooks. Both halves, because "on"
    has to mean on — removing the switch file while the hooks are missing from settings.json yields
    a map that reports itself live while nothing feeds it, which is worse than being off."""
    from transponder import toggle

    v = toggle.enable()
    out = [toggle.render(toggle.state())]
    if not v["wired"]:
        out.append("\nWARNING: the hooks could not be written to settings.json — nothing is feeding "
                   "the map or the witness.")
    if v["env_override"] is not None:
        out.append(f"\nWARNING: TRANSPONDER_DISABLED={v['env_override']!r} is set in this server's "
                   "environment and overrides the file.")
    return "\n".join(out)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def lock_switch() -> str:
    """Is it on? Is it wired? What is on the map right now? Distinguishes the three states that
    look alike from inside a session: ON, switched OFF, and the dangerous middle one where it
    believes it is on but its hooks were never wired."""
    from transponder import toggle

    return toggle.render(toggle.state())


def main() -> None:
    mcp.run()  # stdio


if __name__ == "__main__":
    main()
