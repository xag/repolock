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

# The tools that declare what they will write — the input carries the path, so the heads-up can
# fire BEFORE the write. Everything else is observed after the fact, because v1 §7a's proof stands:
# what a shell will touch is not decidable from its text, and this library no longer guesses.
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


def _say(msg: str) -> None:
    print(msg)


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


def _target(payload: dict) -> tuple[str | None, bool]:
    """(repo, declared_write). For a file-editing tool the repo comes from its own file_path — not
    from cwd, which is a different repo often enough to matter (#8)."""
    tool = payload.get("tool_name") or ""
    if tool in WRITING_TOOLS:
        return common.repo_of(_target_path(payload)), True
    return common.repo_root(payload.get("cwd") or os.getcwd()), False


def pre_tool_use(payload: dict) -> None:
    tool = payload.get("tool_name") or ""
    if _skipped_mcp(tool):
        return
    repo, declared_write = _target(payload)
    if not repo:
        return                       # not a git checkout — nothing to witness, nobody to inform

    _record()
    session = payload.get("session_id") or "unknown"

    notes = []
    if note := common.shared_note(repo, session):
        notes.append(note)
    if declared_write:
        notes += common.heads_up(repo, session, _target_path(payload), _intent(payload))
    else:
        notes += common.watch(repo, session)     # the witness's before-picture; never a refusal
    for n in notes:
        _say(n)


def post_tool_use(payload: dict) -> None:
    tool = payload.get("tool_name") or ""
    if _skipped_mcp(tool):
        return
    repo, declared_write = _target(payload)
    if not repo or declared_write:
        return                       # a declared write was handled before it ran; nothing to settle

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
EVENTS = {
    "PreToolUse": "Edit|Write|MultiEdit|NotebookEdit|Bash|PowerShell|mcp__.*",
    "PostToolUse": "Bash|PowerShell|mcp__.*",
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

    handler = HANDLERS.get(payload.get("hook_event_name") or "")
    if not handler:
        sys.exit(0)

    try:
        handler(payload)
    except SystemExit:
        raise
    except Exception as e:                # noqa: BLE001
        # A crashing hook must never wedge a session. Fail SILENT-to-the-flow, loud-to-the-eye:
        # the courier losing a note is an inconvenience; a hook error blocking work would be the
        # lock's old disease wearing the informer's coat.
        print(f"transponder hook error ({type(e).__name__}: {e}) — this call went unwitnessed",
              file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
