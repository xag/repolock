"""The harness adapters, driven over their real wire formats.

Each adapter is a subprocess fed the harness's own JSON — exactly what the harness does — and
judged on what the harness would see: exit codes and stderr for Claude Code, JSON verdicts on
stdout for Cursor. The last test is the one the README stakes its value on: a lock taken
through one vendor's hook holds out a session arriving through the other's.
"""

import json
import os
import subprocess
import sys

CLAUDE = os.path.join(os.path.dirname(__file__), "..", "repolock", "hooks", "claude_code.py")
CURSOR = os.path.join(os.path.dirname(__file__), "..", "repolock", "hooks", "cursor.py")


def run_hook(script, payload):
    res = subprocess.run([sys.executable, script], input=json.dumps(payload),
                         capture_output=True, text=True, timeout=60)
    return res


def claude_edit(repo, session):
    return run_hook(CLAUDE, {"hook_event_name": "PreToolUse", "tool_name": "Edit",
                             "tool_input": {}, "cwd": repo, "session_id": session})


def cursor_write(repo, conversation):
    return run_hook(CURSOR, {"hook_event_name": "preToolUse", "tool_name": "Write",
                             "tool_input": {}, "cwd": repo,
                             "conversation_id": conversation, "workspace_roots": [repo]})


# --- Claude Code (adapter #1) ---------------------------------------------------

def test_claude_hook_takes_the_lock_and_holds_out_a_second_session(repo):
    assert claude_edit(repo, "A").returncode == 0
    res = claude_edit(repo, "B")
    assert res.returncode == 2                      # exit 2 is the block
    assert "REPO LOCKED" in res.stderr
    assert "session A" in res.stderr


def test_claude_stop_releases_a_clean_tree(repo):
    claude_edit(repo, "A")
    res = run_hook(CLAUDE, {"hook_event_name": "Stop", "cwd": repo, "session_id": "A"})
    assert res.returncode == 0
    assert claude_edit(repo, "B").returncode == 0   # free again


# --- Cursor (adapter #2) --------------------------------------------------------

def test_cursor_hook_takes_the_lock_and_denies_a_second_conversation(repo):
    res = cursor_write(repo, "conv-1")
    assert res.returncode == 0
    assert json.loads(res.stdout)["permission"] == "allow"

    res = cursor_write(repo, "conv-2")
    verdict = json.loads(res.stdout)
    assert verdict["permission"] == "deny"
    assert "REPO LOCKED" in verdict["agent_message"]
    assert "conv-1" in verdict["agent_message"]


def test_cursor_gates_writing_git_shell_commands_but_not_reads(repo):
    res = run_hook(CURSOR, {"hook_event_name": "beforeShellExecution", "cwd": repo,
                            "conversation_id": "conv-1", "command": "git commit -m x"})
    assert json.loads(res.stdout)["permission"] == "allow"   # took the lock

    res = run_hook(CURSOR, {"hook_event_name": "beforeShellExecution", "cwd": repo,
                            "conversation_id": "conv-2", "command": "git log --oneline"})
    assert json.loads(res.stdout)["permission"] == "allow"   # read-only: never locked...

    res = cursor_write(repo, "conv-2")
    assert json.loads(res.stdout)["permission"] == "deny"    # ...so conv-1 still holds it


def test_cursor_stop_releases_every_clean_workspace_root(repo):
    cursor_write(repo, "conv-1")
    res = run_hook(CURSOR, {"hook_event_name": "stop", "conversation_id": "conv-1",
                            "workspace_roots": [repo], "status": "completed"})
    assert res.returncode == 0
    assert json.loads(cursor_write(repo, "conv-2").stdout)["permission"] == "allow"


def test_cursor_session_start_reports_drift_as_additional_context(repo):
    def start():
        return run_hook(CURSOR, {"hook_event_name": "sessionStart", "conversation_id": "conv-1",
                                 "workspace_roots": [repo]})

    assert json.loads(start().stdout) == {}          # first look: nothing to compare against
    with open(os.path.join(repo, "b.txt"), "w") as f:
        f.write("two")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "two"], cwd=repo, check=True)
    out = json.loads(start().stdout)
    assert "moved" in out["additional_context"]


def test_cursor_fails_open_on_garbage_input(repo):
    res = subprocess.run([sys.executable, CURSOR], input="not json",
                         capture_output=True, text=True, timeout=60)
    assert res.returncode == 0
    assert json.loads(res.stdout)["permission"] == "allow"


# --- the mixed fleet ------------------------------------------------------------

def test_a_lock_taken_in_one_harness_binds_the_other(repo):
    """The value proposition, executed: same lockfile, two vendors' hooks, one checkout."""
    assert claude_edit(repo, "claude-session").returncode == 0

    verdict = json.loads(cursor_write(repo, "cursor-conv").stdout)
    assert verdict["permission"] == "deny"
    assert "claude-session" in verdict["agent_message"]

    # and the other way round, after the Claude session lets go
    run_hook(CLAUDE, {"hook_event_name": "Stop", "cwd": repo, "session_id": "claude-session"})
    assert json.loads(cursor_write(repo, "cursor-conv").stdout)["permission"] == "allow"
    res = claude_edit(repo, "claude-session")
    assert res.returncode == 2
    assert "cursor-conv" in res.stderr
