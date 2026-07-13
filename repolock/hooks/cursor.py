"""The Cursor hooks adapter — adapter #2, and the convention's first cross-tool test.

Same lockfile, same SPEC.md obligations, different wire format: Cursor hooks speak JSON on
stdin AND stdout (a blocking hook answers `{"permission": "allow"|"deny", ...}`), identity is
`conversation_id` (stable across turns), and there is no per-event cwd on lifecycle events —
those carry `workspace_roots` instead. One script serves every event; dispatch is on
`hook_event_name`.

Event mapping (SPEC.md §7):

  preToolUse            gate the file-editing tools. `deny` + agent_message when a live holder
                        is in the way; handoff and commit warnings ride `agent_message` on allow.
  beforeShellExecution  gate shell commands that write a working copy or its history.
  stop / sessionEnd     the idle boundary: release on a clean tree, hold-and-lapse on a dirty
                        one, for every workspace root.
  sessionStart          the read-side drift check, injected as `additional_context`.
  beforeSubmitPrompt    the same check on the way back in; drift surfaces as `user_message`
                        (this event's output has no agent channel), never blocks.

Install: `~/.cursor/hooks.json` (user scope), each event pointing at this script via a python
that can import `repolock`:

    {
      "version": 1,
      "hooks": {
        "preToolUse":           [{"command": "<python> <checkout>/repolock/hooks/cursor.py"}],
        "beforeShellExecution": [{"command": "<python> <checkout>/repolock/hooks/cursor.py"}],
        "stop":                 [{"command": "<python> <checkout>/repolock/hooks/cursor.py"}],
        "sessionEnd":           [{"command": "<python> <checkout>/repolock/hooks/cursor.py"}],
        "sessionStart":         [{"command": "<python> <checkout>/repolock/hooks/cursor.py"}],
        "beforeSubmitPrompt":   [{"command": "<python> <checkout>/repolock/hooks/cursor.py"}]
      }
    }
"""

from __future__ import annotations

import json
import os
import sys

try:
    from repolock import env, lock
    from repolock.hooks import common
except ImportError:                               # run straight from a checkout, uninstalled
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from repolock import env, lock
    from repolock.hooks import common

# Tool names that edit files. Substring match, deliberately over-inclusive (SPEC.md §7): Cursor's
# tool list is not a published contract, and a false positive is only a lock we'd have taken
# anyway. `Read`-shaped tools are excluded by not matching, not by a second list.
WRITING_TOOL_HINTS = ("write", "edit", "notebook", "replace")


def _out(obj: dict) -> None:
    print(json.dumps(obj))
    sys.exit(0)


def _session(p: dict) -> str:
    return p.get("conversation_id") or p.get("session_id") or "unknown"


def _repos(p: dict) -> list[str]:
    """Every distinct working copy this event could concern: cwd first, then workspace roots."""
    seen, out = set(), []
    for c in [p.get("cwd"), *(p.get("workspace_roots") or [])]:
        root = common.repo_root(c) if c else None
        if root and root not in seen:
            seen.add(root)
            out.append(root)
    return out


def _record() -> None:
    """Arm the recorder immediately before the first call into `lock` — never at the top of
    main(). The boundary being recorded IS the lock, so a hook call that takes no lock has
    nothing to record, and installing eagerly would tax every read with the import.

    A missing recorder must never cost the lock: `flight-recorder` is an optional extra, so a plain
    install has none, and an ImportError here used to travel into the fail-open handler and disable
    every hook. Run without a tape, not without a lock."""
    if not env.recording():
        return
    try:
        from repolock import flight
    except ImportError:
        return
    flight.install()


def _catch_up(p: dict) -> list[str]:
    """Settle the speculative lock taken before the LAST shell, on the way into this event.

    Adapter #1 settles at PostToolUse, the moment the command returns. Cursor's post-tool event I
    have NOT verified against the real client, and hand-rolling someone's wire format from memory is
    just relocated guessing — the same species of mistake as reading a command to guess its effect.
    So this adapter settles LAZILY, at the start of the next event.

    The consequence is real and must not be glossed: between a read-only shell and Cursor's next
    hook call, this session holds a lock it does not need. It is bounded by the lease and released
    the moment anything else happens, but it is a worse #4 risk than adapter #1 carries. Verify
    Cursor's post-tool event against the real client and settle there instead — see SPEC §7b.
    """
    _record()
    session = _session(p)
    return [n for repo in _repos(p) for n in common.settle_unknown(repo, session)]


def _gate(p: dict, repo: str, intent: str, notes: list[str]) -> None:
    denial, more = common.gate(repo, _session(p), intent)
    if denial:
        _out({"permission": "deny",
              "user_message": f"repo-lock: {repo} is held by another session",
              "agent_message": denial})
    out: dict = {"permission": "allow"}
    if notes + more:
        out["agent_message"] = "\n".join(notes + more)
    _out(out)


def pre_tool_use(p: dict) -> None:
    notes = _catch_up(p)
    tool = (p.get("tool_name") or "").lower()
    # Shell is handled by beforeShellExecution. Anything that is not a file-editing tool tells us
    # nothing about what it will do, so it is not gated here — it is observed, like a shell.
    if tool == "shell" or not any(h in tool for h in WRITING_TOOL_HINTS):
        _out({"permission": "allow", **({"agent_message": "\n".join(notes)} if notes else {})})

    # A file-editing tool names its target. Prefer the repo that owns the FILE over the session's
    # cwd (#8); fall back to cwd only when Cursor's payload does not carry a path.
    args = p.get("tool_input") or p.get("args") or {}
    path = args.get("file_path") or args.get("path") or args.get("target_file") or ""
    repo = common.repo_of(path) if path else None
    if not repo:
        repos = _repos(p)
        if not repos:
            _out({"permission": "allow", **({"agent_message": "\n".join(notes)} if notes else {})})
        repo = repos[0]
    _gate(p, repo, intent=p.get("tool_name") or "edit", notes=notes)


def before_shell(p: dict) -> None:
    """A shell's effect is not decidable from its text, so this no longer tries: it takes the lock
    on speculation and lets the fingerprint settle it afterwards (_catch_up)."""
    notes = _catch_up(p)
    session = _session(p)
    command = p.get("command") or ""
    for repo in _repos(p):
        # The waiter this gate minted for a blocked session: byte equality against our own token,
        # never a reading of the command. Without it a refused session cannot wait at all.
        if common.is_ticket(session, repo, command):
            _out({"permission": "allow"})
        denial, more = common.hold_unknown(repo, session, intent="shell")
        if denial:
            _out({"permission": "deny",
                  "user_message": f"repo-lock: {repo} is held by another session",
                  "agent_message": denial})
        notes += more
    _out({"permission": "allow", **({"agent_message": "\n".join(notes)} if notes else {})})


def go_idle(p: dict) -> None:
    notes = _catch_up(p)                          # a write by the last shell must not be missed
    session = _session(p)
    for repo in _repos(p):
        verdict = lock.go_idle(repo, session)
        if verdict["status"] == "idle_dirty":
            notes.append(verdict["message"])
    _out({"user_message": "\n".join(notes)} if notes else {})


def session_start(p: dict) -> None:
    _record()
    session = _session(p)
    notes = [n for repo in _repos(p) if (n := common.drift_note(session, repo))]
    _out({"additional_context": "\n".join(notes)} if notes else {})


def before_submit(p: dict) -> None:
    _record()
    session = _session(p)
    notes = [n for repo in _repos(p) if (n := common.drift_note(session, repo))]
    out: dict = {"continue": True}
    if notes:
        out["user_message"] = "\n".join(notes)
    _out(out)


HANDLERS = {
    "preToolUse": pre_tool_use,
    "beforeShellExecution": before_shell,
    "stop": go_idle,
    "sessionEnd": go_idle,
    "sessionStart": session_start,
    "beforeSubmitPrompt": before_submit,
}


def main() -> None:
    # No recorder here — see _record(): it is armed in the handlers that call `lock`, so a
    # read-only shell command never pays the flight-recorder import.
    if env.disabled():
        _out({"permission": "allow"})     # the panic switch — see env.disabled()

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        _out({"permission": "allow"})     # a hook that cannot parse its input must not block work

    handler = HANDLERS.get(payload.get("hook_event_name") or "")
    if not handler:
        _out({})

    try:
        handler(payload)
    except SystemExit:
        raise
    except Exception as e:                # noqa: BLE001
        # Fail OPEN, loudly (SPEC.md §7.6): an unguarded write is bad, a machine where nobody
        # can edit anything is worse — and silent is worst.
        print(f"repo-lock hook error ({type(e).__name__}: {e}) — proceeding unguarded",
              file=sys.stderr)
        _out({"permission": "allow"})


if __name__ == "__main__":
    main()
