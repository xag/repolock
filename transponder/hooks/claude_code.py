"""The Claude Code adapter: the courier and the witness, wired into the harness — adapter #1.

This hook used to be a gate. It refused tool calls, held a mutex through every shell, and took the
machine down four times doing it (#4, #7, #10, #11). It is now an information layer, and the rule
that replaced all of that fits in one line:

    **No tool call is ever refused.** Not for an undeclared session, not for a write into someone
    else's region, not for anything. exit 2 exists in exactly one place — the Stop boundary, where
    a DEPARTING agent is asked once not to leave a dirty tree behind. That blocks no other agent,
    ever.

Four events, two jobs:

  PreToolUse   the courier: hand over whatever is waiting for this agent, and remember any write it
               makes that no claim of its own covers. Cheap on purpose — no git, no snapshots — so
               it can run on every call.

  Stop         release this session's claims here against a clean tree; ask ONCE about a dirty one.

  SessionStart the drift check: has history moved under what this session remembers?

  UserPromptSubmit
               THE ASK. The one event that is both before the model acts and AFTER the human has
               said what the work is — so it carries the shared-checkout intro (once) and, for a
               session that keeps writing without a claim, the list of what it has written.

Install: a `hooks` block in ~/.claude/settings.json (user scope, so every repo on the machine is
covered) wiring all four events — `python -m transponder.toggle on` writes it. The matchers include
`mcp__.*` so MCP calls are witnessed; they are never, under any circumstance, refused.

Kill switch: `~/.transponder/DISABLED` (see env.disabled) makes every event a no-op, including in
sessions that already snapshotted their hooks. An informer cannot wedge the machine the way the
lock could, but it can be wrong or noisy, and off must still mean off, everywhere, instantly.
"""

from __future__ import annotations

import json
import os
import sys

try:
    from transponder import env, scope
    from transponder.hooks import common
except ImportError:                               # run straight from a checkout, uninstalled
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from transponder import env, scope
    from transponder.hooks import common

# Our OWN MCP tools, matched on the tool's name rather than its server's (the server can be
# registered under any name). Skipped entirely: they operate on ~/.transponder, never on a working
# copy, so there is nothing for the witness to see and no reason to spend two git calls looking.
OUR_MCP_TOOLS = {"lock_drift", "lock_disable", "lock_enable", "lock_switch",
                 "declare_work", "extend_work", "finish_work", "channel",
                 "send_message"}


def _skipped_mcp(tool: str) -> bool:
    return tool.startswith("mcp__") and tool.rsplit("__", 1)[-1] in OUR_MCP_TOOLS


def _deny(reason: str) -> None:
    """Exit 2 blocks a call and feeds stderr back to the model. ONE caller: the Stop boundary."""
    print(reason, file=sys.stderr)
    sys.exit(2)


# The courier's notes, buffered until the end of the call. ONE JSON document goes to stdout, so
# they cannot be printed as they are produced: two objects on stdout do not parse as one.
_NOTES: list[str] = []


def _say(msg: str) -> None:
    """Queue a note for the agent. It is NOT printed.

    Plain stdout on PreToolUse/PostToolUse/Stop goes to Claude Code's DEBUG LOG — not the model,
    not even the transcript. This was `print(msg)` for the whole life of v2, which means the
    courier has been talking to a log file: no agent on this machine has ever received the
    shared-checkout intro, a heads-up, or a violation report. It was not a delivery that
    degraded — it never arrived, and nothing in the library could tell, because a hook's stdout
    looks identical whether someone reads it or not.

    `hookSpecificOutput.additionalContext` is the channel that reaches the model without blocking
    the call, which keeps the one rule this library will not break: nothing is ever refused.

    Its TIMING is a real cost, not a footnote. Context from PreToolUse is delivered next to the
    TOOL RESULT — after the write has landed. No non-blocking pre-execution channel exists in this
    harness; the only thing that reaches a model before its tool runs is exit 2, which refuses the
    call. The pre-write warning was deleted rather than reworded (see common.py where heads_up
    stood): every write is now witnessed after the fact, which is what this library could always
    honestly claim.
    """
    _NOTES.append(msg)


def _emit(event: str) -> None:
    """Hand the queued notes to the model. Silence when there is nothing to say — an information
    layer that speaks on every call teaches its reader to skim."""
    if not _NOTES:
        return
    json.dump({"hookSpecificOutput": {"hookEventName": event,
                                      "additionalContext": "\n\n".join(_NOTES)}}, sys.stdout)
    sys.stdout.write("\n")


def _record() -> None:
    """Arm the recorder immediately before the first call into scope/witness — never at the top of
    main(): a hook call that touches neither has nothing to record, and installing eagerly taxed
    every call with a ~110ms import. A MISSING recorder costs the tape, never the courier."""
    if not env.recording():
        return
    try:
        from transponder import flight
    except ImportError:
        return
    flight.install()


def _intent(payload: dict) -> str:
    """What this session is doing, in words another agent can act on. Recorded to be SHOWN —
    nothing branches on it; the moment anything decides by reading a command we are back in #7."""
    tool = payload.get("tool_name") or "tool"
    ti = payload.get("tool_input") or {}
    path = ti.get("file_path") or ti.get("notebook_path")
    if path:
        return f"{tool} {os.path.basename(path)}"
    command = ti.get("command")
    if isinstance(command, str) and command.strip():
        one = " ".join(command.split())
        return f"{tool}: {one[:70]}" + ("…" if len(one) > 70 else "")
    return tool


def pre_tool_use(payload: dict) -> None:
    """Deliver. That is the whole of it now.

    This used to fingerprint every checkout on the map before the call and diff it afterwards, to
    catch a write landing in somebody's region. That is gone: a fingerprint proves the tree moved
    and never who moved it, so with two agents running it produced accusations rather than facts.
    Nothing is detected here any more. The map is agreed BEFORE the work, by declare_work(), and an
    agent that suspects something moved under it asks the channel.

    What is left is opportunistic and makes no promises: a note reaches an agent when a hook happens
    to fire, or when it asks. It is cheap now — no git, no snapshots — so it can run on every call.
    """
    if _skipped_mcp(payload.get("tool_name") or ""):
        return
    _record()
    session = payload.get("session_id") or "unknown"
    # Remembered here, reported at the next prompt. It has to be RECORDED at the only event that
    # sees the write and REPORTED at the only event the model reads before acting; those are not
    # the same event, and trying to make them one is what produced a warning delivered after the
    # thing it warned about (see common.py, where heads_up stood).
    common.note_write(session, payload.get("tool_name") or "", payload.get("tool_input") or {})
    if note := common.shared_note(session, payload.get("cwd") or ""):
        _say(note)
    for repo in scope.in_play():
        for note in common.collect(session, repo):
            _say(note)


def stop(payload: dict) -> None:
    """The one exit 2 in the library. It blocks the STOP — not a tool, not another agent — to ask
    the departing session, exactly once (`stop_hook_active`), not to leave a dirty tree behind.

    Over the checkouts THIS session declared, not the one it happens to be sitting in: a session
    that declared a region in another repo was previously asked about its cwd and walked away still
    holding the region that mattered."""
    _record()
    session = payload.get("session_id") or "unknown"
    for repo in scope.in_play(session):
        block, notes = common.hand_back(
            repo, session, already_asked=bool(payload.get("stop_hook_active")))
        for note in notes:
            _say(note)
        if block:
            _deny(block)                 # exit 2 — so this asks about one checkout per stop


def _read_side(session: str) -> None:
    """Mail addressed to this agent, and history that moved under it. Drained at both read-side
    events, and this is the moment it beats PreToolUse: output from SessionStart and
    UserPromptSubmit reaches the model BEFORE it acts. An agent coming back to a region somebody
    wrote in should learn it before its first tool call, not beside the result of one."""
    for repo in scope.in_play():
        for note in common.collect(session, repo):
            _say(note)
        if note := common.drift_note(session, repo):
            _say(note)


def session_start(payload: dict) -> None:
    """Arrival, and NOT the ask.

    The intro used to be spent here, and being spent here is what made it useless. SessionStart
    fires before the human has typed anything, so it asked an agent to declare what it would write
    to at the one moment in the session when that question has no answer — and, being once-only,
    never asked again. A nine-hour session got a wall of text before it knew what it was for.

    What is left is the part that IS answerable on arrival: what moved while this session was away.
    """
    _record()
    _read_side(payload.get("session_id") or "unknown")


def user_prompt_submit(payload: dict) -> None:
    """The ask, at the only moment this harness offers where it can be answered.

    Both properties are needed and no other event has both: it is BEFORE the model acts (so the
    remedy can be run instead of regretted — PreToolUse context lands beside the tool result, after
    the write) and it is AFTER the human has said what the work is (so "what will you write to?"
    has an answer).

    It also reaches an agent that SessionStart and PreToolUse cannot: a session parked on a
    question makes no tool calls, so anything addressed to it waits until it writes again, which is
    one call too late.
    """
    _record()
    session = payload.get("session_id") or "unknown"
    if note := common.shared_note(session, payload.get("cwd") or ""):
        _say(note)
    _read_side(session)
    for repo in common.wrote_in(session):
        if note := common.undeclared_note(session, repo):
            _say(note)


HANDLERS = {
    "PreToolUse": pre_tool_use,
    "Stop": stop,
    "SessionStart": session_start,
    "UserPromptSubmit": user_prompt_submit,
}


# --- wiring this adapter into (and out of) the harness ------------------------------------------

# Matchers per event. Both shells (on Windows PowerShell is the one that runs) and `mcp__.*`, all
# for the WITNESS — nothing matched here is ever refused. PostToolUse missing = a blind witness,
# which watch() detects and says out loud rather than letting anyone believe they are covered.
# No matcher: the hook is a letterbox now, not a witness. PostToolUse is gone entirely — it existed
# to take the after-picture, and there is no picture to take. What is left costs no git call, so
# there is no reason to filter which tools it runs on, and every call is one more chance to hand an
# agent something waiting for it.
EVENTS = {
    "PreToolUse": None,
    "Stop": None,
    "SessionStart": None,
    # The most valuable event of the four, and it spent v2 unwired: HANDLERS routed it to
    # session_start — "the same read-side check, on the way back in" — but it was absent here, so
    # it never ran. Not once. It is now wired AND has a handler of its own, because routing it to
    # session_start left the ask where it could not be answered: whichever of the two fired first
    # spent the once-only intro, and SessionStart always fires first.
    "UserPromptSubmit": None,
}


def settings_path() -> str:
    """User scope, not project scope: the courier covers every checkout on the machine."""
    return os.getenv("TRANSPONDER_CLAUDE_SETTINGS") or os.path.join(
        os.path.expanduser("~"), ".claude", "settings.json")


def hook_command() -> str:
    """Quoted, and spelled with forward slashes: this string is handed to a shell (#10)."""
    py = sys.executable.replace("\\", "/")
    script = os.path.abspath(__file__).replace("\\", "/")
    return f'"{py}" "{script}"'


# The adapter script's own directory, which is what makes a hook entry OURS. Specific on purpose:
# a bare-substring test once identified `echo not-transponder` as ours and deleted it on uninstall.
MARKER = "transponder/hooks/"


def _ours(entry: dict) -> bool:
    return any(MARKER in (h.get("command") or "").replace("\\", "/").lower()
               for h in (entry.get("hooks") or []))


def hook_block() -> dict:
    cmd = hook_command()
    block: dict[str, list] = {}
    for event, matcher in EVENTS.items():
        hook = {"type": "command", "timeout": 20, "command": cmd}
        if event == "PreToolUse":
            hook["statusMessage"] = "checking the shared-checkout map"
        entry: dict = {"hooks": [hook]}
        if matcher:
            entry["matcher"] = matcher
        block[event] = [entry]
    return block


def _load_settings(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_settings(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)                 # atomic: a torn settings.json is a harness that won't start


def wired(path: str | None = None) -> bool:
    """Are ALL four of our events wired? Three out of four is a blind witness, not an install."""
    hooks = _load_settings(path or settings_path()).get("hooks") or {}
    return all(any(_ours(e) for e in (hooks.get(ev) or [])) for ev in EVENTS)


def install(path: str | None = None) -> bool:
    """Wire our events. Idempotent, and it preserves any hooks that are not ours.

    It also PRUNES: an event we used to wire and no longer do has our entry removed. Without that,
    an upgrade leaves the old wiring in place pointing at a script that no longer handles the event
    — which is how PostToolUse survived the witness being deleted, firing on every tool call to do
    nothing. Silent, harmless, and exactly the kind of stale truth this library exists to object to.
    """
    path = path or settings_path()
    data = _load_settings(path)
    hooks = data.setdefault("hooks", {})
    block = hook_block()
    for event in list(hooks):
        if event in block:
            continue
        keep = [e for e in (hooks.get(event) or []) if not _ours(e)]
        if keep:
            hooks[event] = keep
        else:
            hooks.pop(event, None)
    for event, entries in block.items():
        keep = [e for e in (hooks.get(event) or []) if not _ours(e)]
        hooks[event] = keep + entries
    _save_settings(path, data)
    return True


def uninstall(path: str | None = None) -> bool:
    """Remove only our entries, and drop an event key only if nothing else was using it."""
    path = path or settings_path()
    data = _load_settings(path)
    hooks = data.get("hooks") or {}
    if not hooks:
        return False
    for event in list(hooks):
        keep = [e for e in (hooks.get(event) or []) if not _ours(e)]
        if keep:
            hooks[event] = keep
        else:
            hooks.pop(event, None)
    if not hooks:
        data.pop("hooks", None)
    _save_settings(path, data)
    return True


def _utf8() -> None:
    """Python on Windows writes stderr in the ANSI code page and the harness decodes UTF-8; an
    em-dash arriving as U+FFFD is a message half-delivered (#10's tape)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass


def main() -> None:
    _utf8()
    if env.disabled():
        sys.exit(0)                       # the off switch — see env.disabled()

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)                       # a hook that cannot parse its input must not block work

    event = payload.get("hook_event_name") or ""
    handler = HANDLERS.get(event)
    if not handler:
        sys.exit(0)

    try:
        handler(payload)
    except SystemExit:
        raise                             # the Stop deny: exit 2 carries its reason on stderr
    except Exception as e:                # noqa: BLE001
        # A crashing hook must never wedge a session. Fail SILENT-to-the-flow, loud-to-the-eye:
        # the courier losing a note is an inconvenience; a hook error blocking work would be the
        # lock's old disease wearing the informer's coat. Notes already queued still go out — a
        # half-delivered warning beats a swallowed one.
        print(f"transponder hook error ({type(e).__name__}: {e}) — this call went unwitnessed",
              file=sys.stderr)
        _emit(event)
        sys.exit(0)
    _emit(event)


if __name__ == "__main__":
    main()
