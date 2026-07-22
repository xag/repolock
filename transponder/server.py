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
def declare_work(repo: str, session_id: str, paths: list[str], doing: str,
                 minutes: float = 0.0) -> str:
    """**Say where you will work, what you are doing, and for how long — then wait for the green
    light this returns. Do not edit a shared checkout before you have it.**

    This is not paperwork and it is not for anyone else's benefit. It is the only thing that stops
    two agents unknowingly rewriting the same file, and it is the only way the agent beside you can
    find out that your change is coming.

      paths    the files and folders you will WRITE TO, in the checkout you will write to — not
               necessarily the one you are sitting in. `src/api/**` a subtree, `src/api/x.py` one
               file, `**` the whole checkout, `.git/index` the staging area (reserve it around a
               commit, so `git add -A` cannot sweep up a neighbour's half-finished work).
      doing    what you are actually doing, in a line. "replacing the auth middleware return type"
               tells the agent next door to write its caller for the new shape. "work" tells it
               nothing.
      minutes  roughly how long you expect to need. Advisory — it is how a blocked agent knows
               when to come back instead of asking every ten seconds.

    RETURNS GREEN LIGHT, and then the region is yours and you can work.

    Or it returns NOT CLEAR, naming who holds the overlap, what they are doing, when they expect
    to be free, and which regions are open right now. Then do one of three things, and say which:
      1. take different work — the answer lists what is free;
      2. ask your human — they may know the other work is abandoned, or want you to go ahead;
      3. wait — say out loud that you are waiting, and poll rather than spin.
    Nothing stops you writing anyway. Nothing here ever blocks a tool call. But nobody is watching
    for collisions any more, so a write into somebody's declared region is simply lost work that
    neither of you will find out about until it hurts.

    Call finish_work() the moment you are done.
    """
    from transponder import scope as sc

    v = sc.declare(repo, session_id, paths, doing, minutes=minutes)
    if v["status"] == "granted":
        held = ", ".join(v["claim"]["scope"])
        return (f"GREEN LIGHT — {held} is yours. Go ahead.\n"
                f"extend_work() if it turns out to be bigger; finish_work() the moment you are "
                f"done, because somebody may be waiting on exactly this.\n\n{_fmt_scopes(repo)}")

    if v["status"] == "rejected":
        return f"NOT EXPRESSIBLE — {v['reason']}"

    out = ["NOT CLEAR — you cannot have all of that yet.", ""]
    soonest = 0
    for c in v["conflicts"]:
        out.append(f"  agent {c['session']} holds {', '.join(c['scope'])}"
                   + (f" — {c['intent']}" if c.get("intent") else ""))
        if c.get("intersection"):
            out.append(f"     you overlap at: {', '.join(c['intersection'])}")
        if c.get("free_in"):
            out.append(f"     they expect to finish in ~{max(1, c['free_in'] // 60)} min")
            soonest = max(soonest, c["free_in"])
    if v.get("free_hint"):
        out.append(f"\nFREE RIGHT NOW: {', '.join(v['free_hint'])}")
    _tell_the_holders(repo, session_id, doing, v["conflicts"])
    out += [
        "",
        "Nothing is registered, and nothing is blocked. The holders have been told you asked.",
        "Choose, and say which you chose:",
        "  * declare_work() for something narrower or elsewhere — see FREE RIGHT NOW above;",
        "  * go back to your human — they may know that work is stale, or want you to proceed;",
        "  * wait, and SAY you are waiting. Do not spin: poll in the background with",
        f"        python -m transponder.wait --repo \"{repo}\" --paths " +
        " ".join(f'\"{p}\"' for p in paths[:4]),
        "    launched as a background task — it exits when the region frees, and your harness will",
        "    tell you when it does. Then declare_work() again.",
    ]
    if soonest:
        out.append(f"    (expect roughly {max(1, soonest // 60)} minutes)")
    return "\n".join(out)


def _tell_the_holders(repo: str, asker: str, intent: str, conflicts: list[dict]) -> None:
    from transponder import env, messages

    for c in conflicts:
        holder = c.get("session")
        if not holder or holder == asker:
            continue
        recent = [m for m in messages.unread(holder, repo, kinds=("direct",), mark=False)
                  if m.get("from") == "transponder" and asker in m.get("body", "")
                  and env.now() - m.get("at", 0) < 600]
        if recent:
            continue                      # they already have this letter and have not read it yet
        messages.send(
            sender="transponder", kind="direct", repo=repo, to=holder,
            body=(f"SOMEONE WANTS YOUR REGION — agent {asker} asked for "
                  f"{', '.join(c.get('intersection') or c.get('scope') or [])}"
                  + (f", to: {intent}" if intent else "")
                  + ".\n  They were told it is yours and are working around it. Nothing is waiting "
                    "on you and nothing is blocked — but if you are done with that part, "
                    "release_scope(drop=[...]) frees it, and a one-line reply "
                    "(send_message) tells them when to expect it."))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True))
def extend_work(repo: str, add: list[str], session_id: str) -> str:
    """Widen what you declared, when the work turns out to be bigger than you said. Answers at
    once: a green light, or who is already there and exactly where you overlap."""
    from transponder import scope as sc

    v = sc.extend(repo, session_id, add)
    if v["status"] == "granted":
        return f"GREEN LIGHT — you now hold {', '.join(v['claim']['scope'])}."
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
def finish_work(repo: str, session_id: str, drop: list[str] | None = None) -> str:
    """**Say you are done, the moment you are.** Everything, or just the entries in `drop`.

    Somebody may be waiting on exactly this. A map that still says you are working where you are
    not is worse than no map at all — it makes the next agent wait for nothing, or give up and
    write anyway. Hold `.git/index` for the length of a commit and not one second more."""
    from transponder import scope as sc

    v = sc.release(session_id, drop, anchor=repo)
    left = ", ".join(v["scope"]) if v["scope"] else "nothing — you are off the map"
    return f"Released. You now hold: {left}.\n\n{_fmt_scopes(repo)}"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=False))
def channel(repo: str, session_id: str, path: str = "") -> str:
    """**CALL THIS FIRST, before you plan or edit anything in a shared checkout.**

    It answers the only question that matters before you start: is the work you are about to do
    already somebody's? Pass `path` — the file or folder you are thinking of working on — and the
    answer narrows to whoever overlaps it.

    You also get every message waiting for you here: what other agents have said they are doing,
    and anything addressed to you directly. Nothing is pushed reliably; this is how you find out.

    Then:
      * nothing in your way  -> declare_work(...) and start
      * somebody is there    -> pick different work, ask your human, or wait (declare_work tells
                                you when they expect to be done, and how to wait without spinning)
    """
    from transponder import messages as mail
    from transponder import scope as sc

    out = []
    got = mail.unread(session_id, repo, kinds=("direct", "channel", mail.BROADCAST))
    if got:
        out += [f"{len(got)} message(s) for you:", ""] + [mail.render(m) + "\n" for m in got]

    claims = sc.touching(repo)
    if path and (target := sc.resolve(path, repo)):
        near = [c for c in claims if sc.covers(c["scope"], target.rstrip("/*"))
                or any(sc.overlaps(target, r) for r in c["scope"])]
        if not near:
            out.append(f"NOBODY IS WORKING {path} — it is yours to declare.")
        else:
            out.append(f"{path} OVERLAPS work already declared:")
            for c in near:
                out.append(f"  agent {c['session']}: {', '.join(c['scope'])}"
                           + (f" — {c.get('intent')}" if c.get("intent") else "")
                           + _eta(c))
    out.append("")
    out.append(_fmt_scopes(repo))
    out.append("\nWhen you know what you will touch: declare_work(repo, session_id, paths, doing, "
               "minutes). Do not start editing a shared checkout without it.")
    return "\n".join(out)


def _eta(claim: dict) -> str:
    from transponder import env

    left = int((claim.get("until") or 0) - env.now())
    return f"  (expects to finish in ~{max(1, left // 60)} min)" if left > 0 else ""


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=False))
def send_message(repo: str, session_id: str, body: str, to: str = "", everyone: bool = False) -> str:
    """**Say what you are doing, in one or two lines, so the agent beside you can write code that
    survives it.**

    `declare_work` already said where you are writing and what you are doing. This is for everything
    after that, and for the agent who is not going to read your claim in time: an estimate that
    slipped, a shape that changed, a question, an answer. "I am replacing the auth middleware's
    return type this hour" lets the agent next door write the caller once, for the new shape,
    instead of twice.

    It matters most when somebody is working near you on purpose. NOT CLEAR is not a refusal — an
    agent can weigh it and declare overlapping work anyway, and sometimes that is right. Two agents
    who know they are close have every reason to be talkative, and to be listening.

    You are not rivals. Two agents in one checkout are building one app for one person.

    Addressing, and it is deliberate that only one of these interrupts anybody:

        (default)        the CHANNEL for this checkout — everyone working `repo` can read it
        to="<session>"   DIRECT to one agent. The only kind PUSHED into its context; use it when
                         a specific agent needs to know, e.g. answering someone who asked for your
                         region, or telling a holder when you will be done
        everyone=True    BROADCAST to every agent on this machine. Rare — cross-repo news only

    Channel and broadcast are PULL-ONLY: they wait until someone calls `messages()`. That is on
    purpose. Your note shares a delivery path with the scope-violation alarm, and an agent trained
    to skim the channel skims the alarm with it — so this stays a transponder, not a chat room.

    THERE IS NO WAKE-UP. Nothing can interrupt a running agent, so a reply arrives when the other
    side next fires a hook, or never, if it has finished. Write letters, not handshakes: "I will need
    api/** when you are done" is well carried; "reply before I continue" will hang forever.

    Your identity and the scope you currently hold are stamped on automatically, so a claim you make
    about the map can be checked against the map.
    """
    from transponder import messages

    kind = "direct" if to else ("broadcast" if everyone else "channel")
    v = messages.send(sender=session_id, body=body, kind=kind, repo=repo, to=to)
    if v["status"] == "empty":
        return "Nothing sent — the message was empty."
    if v["status"] != "sent":
        return "Could not send (the mail directory is not writable)."
    if kind == "direct":
        return (f"Sent to agent {to}. It lands in their context on their next tool call, or on "
                f"their human's next prompt — not before. Do not wait for a reply.")
    where = "every agent on this machine" if kind == "broadcast" else "agents working this checkout"
    return (f"Posted to {where}. They will see it when they call messages() — it is not pushed. "
            f"If one specific agent needs to know, send it direct as well.")


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
