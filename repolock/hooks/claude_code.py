"""The Claude Code hook that makes the repo lock binding instead of advisory — adapter #1.

An MCP tool alone would be a suggestion. The session that rebases `main` underneath another
session would never have called `lock_repo` — nobody had told it to. What makes a lock mean
something is a gate the model cannot forget to walk through, so the enforcement lives here, in
the harness, and the model never has to remember anything.

Three events, three jobs:

  PreToolUse   before any tool that WRITES (Edit/Write/NotebookEdit, and the git commands that
               rewrite history), acquire-or-renew the lock on the repo under `cwd`. Another live
               session holding it => exit 2, which blocks the tool and hands the reason back to
               the model. This is also where renewal happens: a tool call IS activity, so the
               lease extends exactly while the session works and stops the moment it stops — no
               daemon, nothing to supervise.

  Stop         the model is handing control back to the human. Clean tree => release (a session
               waiting on someone at lunch must not starve every other session). Dirty tree =>
               hold, mark it idle, and let the declared lease run out; releasing a checkout full
               of half-finished edits is worse than making the next session wait.

  SessionStart the read-side check. Compare the HEAD this session last saw against the HEAD that
               is there now, and say so if history moved. No lock involved — and it is the one
               thing that catches the stale reader, where nothing is corrupted and a session
               merely reasons confidently about commits that no longer exist.

Install: a `hooks` block in ~/.claude/settings.json (user scope, so every repo on the machine
is guarded, not just one) wiring PreToolUse (matcher Edit|Write|MultiEdit|NotebookEdit|Bash),
Stop, and SessionStart to run this script via a python that can import `repolock`, by absolute
path — at user scope $CLAUDE_PROJECT_DIR points at whatever project the session is in.
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

# Tools that write. Read-only tools never take a lock — locking a `Read` would be pure friction.
WRITING_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def _writes(tool: str, tool_input: dict) -> bool:
    if tool in WRITING_TOOLS:
        return True
    if tool != "Bash":
        return False
    return common.git_writes(tool_input.get("command") or "")


def _deny(reason: str) -> None:
    """Exit 2 blocks the tool call and feeds stderr back to the model as the reason."""
    print(reason, file=sys.stderr)
    sys.exit(2)


def _say(msg: str) -> None:
    print(msg)


def pre_tool_use(payload: dict) -> None:
    tool = payload.get("tool_name") or ""
    if not _writes(tool, payload.get("tool_input") or {}):
        return

    cwd = payload.get("cwd") or os.getcwd()
    repo = common.repo_root(cwd)
    if not repo:
        return                       # not a git checkout — nothing to protect, nothing to lock

    session = payload.get("session_id") or "unknown"
    denial, notes = common.gate(repo, session, intent=f"{tool}")
    if denial:
        _deny(denial)
    for note in notes:
        _say(note)


def stop(payload: dict) -> None:
    cwd = payload.get("cwd") or os.getcwd()
    repo = common.repo_root(cwd)
    if not repo:
        return
    session = payload.get("session_id") or "unknown"
    verdict = lock.go_idle(repo, session)
    if verdict["status"] == "idle_dirty":
        # Not a block — just the truth, on the way out.
        _say(verdict["message"])


def session_start(payload: dict) -> None:
    cwd = payload.get("cwd") or os.getcwd()
    repo = common.repo_root(cwd)
    if not repo:
        return
    session = payload.get("session_id") or "unknown"
    note = common.drift_note(session, repo)
    if note:
        _say(note)


HANDLERS = {
    "PreToolUse": pre_tool_use,
    "Stop": stop,
    "SessionStart": session_start,
    "UserPromptSubmit": session_start,   # same read-side check, on the way back in
}


def main() -> None:
    # Recording off ⇒ zero flight-recorder imports. This hook runs before EVERY write, and a
    # heavyweight import on the hot path is a tax the session pays forever for nothing.
    if os.getenv("REPOLOCK_FLIGHT"):
        from repolock import flight
        flight.install()
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
        # A crashing hook must never wedge the session. Fail OPEN, loudly: an unguarded write is
        # bad, but a laptop where nobody can edit anything is worse — and silent is worst.
        print(f"repo-lock hook error ({type(e).__name__}: {e}) — proceeding unguarded",
              file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
