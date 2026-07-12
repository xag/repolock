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
    from repolock import lock
    from repolock.hooks import common
except ImportError:                               # run straight from a checkout, uninstalled
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from repolock import lock
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


def _gate(p: dict, repo: str, intent: str) -> None:
    denial, notes = common.gate(repo, _session(p), intent)
    if denial:
        _out({"permission": "deny",
              "user_message": f"repo-lock: {repo} is held by another session",
              "agent_message": denial})
    out: dict = {"permission": "allow"}
    if notes:
        out["agent_message"] = "\n".join(notes)
    _out(out)


def pre_tool_use(p: dict) -> None:
    tool = (p.get("tool_name") or "").lower()
    # Shell is gated by beforeShellExecution with the actual command text; double-gating here
    # would lock on read-only shell commands too.
    if tool == "shell" or not any(h in tool for h in WRITING_TOOL_HINTS):
        _out({"permission": "allow"})
    repos = _repos(p)
    if not repos:
        _out({"permission": "allow"})
    _gate(p, repos[0], intent=p.get("tool_name") or "edit")


def before_shell(p: dict) -> None:
    if not common.git_writes(p.get("command") or ""):
        _out({"permission": "allow"})
    repos = _repos(p)
    if not repos:
        _out({"permission": "allow"})
    _gate(p, repos[0], intent="shell: git")


def go_idle(p: dict) -> None:
    session = _session(p)
    notes = []
    for repo in _repos(p):
        verdict = lock.go_idle(repo, session)
        if verdict["status"] == "idle_dirty":
            notes.append(verdict["message"])
    _out({"user_message": "\n".join(notes)} if notes else {})


def session_start(p: dict) -> None:
    session = _session(p)
    notes = [n for repo in _repos(p) if (n := common.drift_note(session, repo))]
    _out({"additional_context": "\n".join(notes)} if notes else {})


def before_submit(p: dict) -> None:
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
    if os.getenv("REPOLOCK_FLIGHT"):
        from repolock import flight
        flight.install()
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
