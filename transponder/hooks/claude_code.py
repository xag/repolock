"""The Claude Code adapter: the courier and the witness, wired into the harness — adapter #1.

This hook used to be a gate. It refused tool calls, held a mutex through every shell, and took the
machine down four times doing it (#4, #7, #10, #11). It is now an information layer, and the rule
that replaced all of that fits in one line:

    **No tool call is ever refused.** Not for an undeclared session, not for a write into someone
    else's region, not for anything. exit 2 exists in exactly one place — the Stop boundary, where
    a DEPARTING agent is asked once not to leave a dirty tree behind. That blocks no other agent,
    ever.

Four events, two jobs:

  PreToolUse   the courier: introduce a shared checkout (once), warn when a declared write is about
               to land in another agent's region — information at its most valuable moment, and
               still not a gate. For shells and MCP calls, take the witness's before-picture.

  PostToolUse  the witness: diff the picture. Unmoved => it read, nothing is said. Moved into
               another agent's declared region => the loudest thing this library says, with the
               remedy attached — a violation is a fact, not a guess, and it must not be silent.

  Stop         release this session's claims here against a clean tree; ask ONCE about a dirty one.

  SessionStart the drift check: has history moved under what this session remembers?

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
    from transponder import env
    from transponder.hooks import common
except ImportError:                               # run straight from a checkout, uninstalled
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from transponder import env
    from transponder.hooks import common

# The tools whose input carries the path they will write. That fact no longer buys a warning before
# the write (the harness cannot deliver one without refusing the call — see common.py where
# heads_up stood); it buys the right ANSWER to which checkout is being touched, which is not the
# session's cwd often enough to matter (#8). Everything, these included, is observed after the fact.
WRITING_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

# Our OWN MCP tools, matched on the tool's name rather than its server's (the server can be
# registered under any name). Skipped entirely: they operate on ~/.transponder, never on a working
# copy, so there is nothing for the witness to see and no reason to spend two git calls looking.
OUR_MCP_TOOLS = {"lock_drift", "lock_disable", "lock_enable", "lock_switch",
                 "declare_scope", "extend_scope", "release_scope", "scopes"}


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


def _target_path(payload: dict) -> str:
    ti = payload.get("tool_input") or {}
    return ti.get("file_path") or ti.get("notebook_path") or ""


def _target(payload: dict) -> str | None:
    """The repo this call acts on. For a file-editing tool it comes from the tool's own file_path —
    not from cwd, which is a different repo often enough to matter (#8). WRITING_TOOLS earns its
    keep here and nowhere else now: it no longer selects a different KIND of handling, only a better
    answer to which checkout is being touched."""
    tool = payload.get("tool_name") or ""
    if tool in WRITING_TOOLS:
        return common.repo_of(_target_path(payload))
    return common.repo_root(payload.get("cwd") or os.getcwd())


def pre_tool_use(payload: dict) -> None:
    tool = payload.get("tool_name") or ""
    if _skipped_mcp(tool):
        return
    repo = _target(payload)
    if not repo:
        return                       # not a git checkout — nothing to witness, nobody to inform

    _record()
    session = payload.get("session_id") or "unknown"

    notes = []
    # Mail first, and before anything else this call might say: it is the only note that is about
    # THIS agent's own work having been touched, and it is stale the moment the agent writes again.
    notes += common.collect(session, repo)
    if note := common.shared_note(repo, session):
        notes.append(note)
    # Every tool gets the before-picture now, Edit/Write included. They used to be handled here
    # instead, by a warning the harness could not deliver in time (see common.py where heads_up
    # stood); without a snapshot taken here, settle() would have nothing to compare against.
    notes += common.watch(repo, session)         # the witness's before-picture; never a refusal
    for n in notes:
        _say(n)


def post_tool_use(payload: dict) -> None:
    tool = payload.get("tool_name") or ""
    if _skipped_mcp(tool):
        return
    repo = _target(payload)
    if not repo:
        return                       # not a git checkout — nothing to settle against

    _record()
    session = payload.get("session_id") or "unknown"
    for note in common.settle(repo, session, _intent(payload)):
        _say(note)


def stop(payload: dict) -> None:
    """The one exit 2 in the library. It blocks the STOP — not a tool, not another agent — to ask
    the departing session, exactly once (`stop_hook_active`), not to leave a dirty tree behind."""
    repo = common.repo_root(payload.get("cwd") or os.getcwd())
    if not repo:
        return
    _record()
    session = payload.get("session_id") or "unknown"
    block, notes = common.hand_back(
        repo, session, already_asked=bool(payload.get("stop_hook_active")))
    if block:
        _deny(block)
    for note in notes:
        _say(note)


def session_start(payload: dict) -> None:
    repo = common.repo_root(payload.get("cwd") or os.getcwd())
    if not repo:
        return
    _record()
    session = payload.get("session_id") or "unknown"
    # Also drained here, and this is the one moment it beats PreToolUse: stdout on SessionStart and
    # UserPromptSubmit reaches the model BEFORE it acts. An agent coming back to a region somebody
    # wrote in should learn it before its first tool call, not beside the result of one.
    for note in common.collect(session, repo):
        _say(note)
    if note := common.drift_note(session, repo):
        _say(note)


HANDLERS = {
    "PreToolUse": pre_tool_use,
    "PostToolUse": post_tool_use,
    "Stop": stop,
    "SessionStart": session_start,
    "UserPromptSubmit": session_start,   # same read-side check, on the way back in
}


# --- wiring this adapter into (and out of) the harness ------------------------------------------

# Matchers per event. Both shells (on Windows PowerShell is the one that runs) and `mcp__.*`, all
# for the WITNESS — nothing matched here is ever refused. PostToolUse missing = a blind witness,
# which watch() detects and says out loud rather than letting anyone believe they are covered.
# PreToolUse and PostToolUse now match the SAME set, and must: every tool that can move the tree
# needs both halves of the witness, a before-picture and an after. PostToolUse used to omit the
# writing tools because a declared write was handled by a warning before it ran — that warning is
# gone (see common.py), so omitting them here would leave Edit and Write observed by nobody, which
# is the failure watch() shouts about.
_WATCHED = "Edit|Write|MultiEdit|NotebookEdit|Bash|PowerShell|mcp__.*"

EVENTS = {
    "PreToolUse": _WATCHED,
    "PostToolUse": _WATCHED,
    "Stop": None,
    "SessionStart": None,
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
    """Wire the four events. Idempotent, and it preserves any hooks that are not ours."""
    path = path or settings_path()
    data = _load_settings(path)
    hooks = data.setdefault("hooks", {})
    for event, entries in hook_block().items():
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
