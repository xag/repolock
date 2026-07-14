"""The off switch, held to the one promise that matters: it frees a session that is already wedged.

Every other property here is in service of that. A panic switch that needed a restart, or a shell,
or a settings.json edit, would be useless at the only moment anyone will ever reach for it — when
the lock is refusing the very session that has to turn it off.
"""

import json
import os
import subprocess
import sys

import pytest

from repolock import env, toggle
from repolock.hooks import claude_code

CLAUDE = os.path.join(os.path.dirname(__file__), "..", "repolock", "hooks", "claude_code.py")


@pytest.fixture
def switchable(repo, tmp_path, monkeypatch):
    """The `repo` fixture pins REPOLOCK_DISABLED=0 so the machine's own panic file cannot mute the
    suite. Here the panic FILE is the thing under test, so the override has to come off — otherwise
    we would be testing the env var and calling it the file."""
    monkeypatch.delenv("REPOLOCK_DISABLED", raising=False)
    monkeypatch.setenv("REPOLOCK_CLAUDE_SETTINGS", str(tmp_path / "settings.json"))
    return repo


def _edit(repo, session="B"):
    return subprocess.run(
        [sys.executable, CLAUDE],
        input=json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Edit",
                          "tool_input": {"file_path": os.path.join(repo, "a.txt")},
                          "cwd": repo, "session_id": session}),
        capture_output=True, text=True, timeout=60)


def test_the_switch_frees_a_session_that_is_ALREADY_blocked(switchable):
    """The whole point. A session refused the lock must be able to turn the lock off and proceed —
    without a restart, and without the shell it has just been refused.

    This is the property that makes the switch worth having: the harness snapshotted its hooks when
    the session started, so editing settings.json cannot reach it. Only a file, read on every hook
    call, can."""
    repo = switchable
    assert _edit(repo, "A").returncode == 0                 # A takes the lock
    assert _edit(repo, "B").returncode == 2, "B should be refused — the premise of the test"

    toggle.disable(reason="B is wedged and this is the only way out")

    assert _edit(repo, "B").returncode == 0, "the switch did not free the blocked session"


def test_on_means_on_even_when_the_hooks_were_removed(switchable, tmp_path):
    """The dangerous middle state: switch disarmed, hooks never wired. It reports itself ON and
    guards precisely nothing — which is worse than being off, because you would rely on it. So
    `enable()` re-wires as well as disarming."""
    settings = tmp_path / "settings.json"
    settings.write_text('{"model": "Opus"}', encoding="utf-8")
    assert not claude_code.wired()

    toggle.enable()

    assert claude_code.wired(), "enable() left the hooks unwired — it would guard nothing"
    assert not toggle.state()["armed"]
    wired = json.loads(settings.read_text(encoding="utf-8"))["hooks"]
    assert set(wired) == set(claude_code.EVENTS), "a partial install is the DEGRADED case, not an install"


def test_uninstalling_repolock_does_not_uninstall_anyone_elses_hooks(switchable, tmp_path):
    """We remove our entries. Only ours.

    The foreign command is `echo not-repolock` on purpose, and it is not a joke: the first cut of
    `_ours()` matched the bare substring `repolock`, so this hook — which merely mentions the word —
    was identified as ours and deleted. Identity is the script we install, not a word that appears
    in a command line."""
    settings = tmp_path / "settings.json"
    foreign = {"type": "command", "command": "echo not-repolock"}
    settings.write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": "Bash",
                                                              "hooks": [foreign]}]}}), encoding="utf-8")
    claude_code.install()
    claude_code.uninstall()

    left = json.loads(settings.read_text(encoding="utf-8")).get("hooks") or {}
    assert left == {"PreToolUse": [{"matcher": "Bash", "hooks": [foreign]}]}, (
        "uninstall touched a hook that was not ours")


def test_the_switch_says_who_turned_it_off_and_why(switchable):
    """A zero-byte panic file is a mystery in a fortnight. The reason travels with the switch."""
    toggle.disable(reason="the waiter never ran (#10)")
    s = toggle.state()
    assert s["armed"] and s["effective_disabled"]
    assert s["reason"] == "the waiter never ran (#10)"
    assert s["since"]
    assert "OFF" in toggle.render(s)


def test_disable_can_drop_the_locks_it_was_holding(switchable):
    """Turning it off makes held locks inert, not absent — they would resurrect on the way back in.
    `clear` is how you leave nothing behind."""
    repo = switchable
    assert _edit(repo, "A").returncode == 0
    assert toggle.held_locks(), "A's lock should be on disk"

    v = toggle.disable(reason="clearing", clear=True)

    assert v["cleared"] == [env.canonical(repo)]
    assert not toggle.held_locks(), "the lock survived a --clear"


def test_an_env_override_is_reported_because_it_beats_the_file(switchable, monkeypatch):
    """REPOLOCK_DISABLED wins over the panic file in BOTH directions. A session launched with
    `REPOLOCK_DISABLED=0` will happily ignore an armed switch — so the switch has to say so out
    loud, rather than claiming an authority it does not have."""
    toggle.disable(reason="armed")
    monkeypatch.setenv("REPOLOCK_DISABLED", "0")

    s = toggle.state()
    assert s["armed"] is True                     # the file is there...
    assert s["effective_disabled"] is False       # ...and is being ignored
    assert "OVERRIDES" in toggle.render(s)
