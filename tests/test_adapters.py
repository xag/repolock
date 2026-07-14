"""The harness adapters, driven over their real wire formats.

Each adapter is a subprocess fed the harness's own JSON — exactly what the harness does — and
judged on what the harness would see: exit codes and stderr for Claude Code, JSON verdicts on
stdout for Cursor.

The contract these tests hold the adapters to changed in v1, and the change is the point. They no
longer assert what a COMMAND was judged to be — that judgement is gone, because it could not be
made correctly. They assert what the REPO did. A shell that writes must be caught whatever it is
spelled; a shell that reads must cost nothing, whatever it is spelled; and neither claim depends on
anyone recognising the command.
"""

import json
import os
import shutil
import subprocess
import sys

import pytest

CLAUDE = os.path.join(os.path.dirname(__file__), "..", "repolock", "hooks", "claude_code.py")
CURSOR = os.path.join(os.path.dirname(__file__), "..", "repolock", "hooks", "cursor.py")


def run_hook(script, payload):
    res = subprocess.run([sys.executable, script], input=json.dumps(payload),
                         capture_output=True, text=True, timeout=60)
    return res


def claude_edit(repo, session, path="a.txt"):
    """A file-editing tool: it names the file it will write, so the lock is taken before it runs."""
    return run_hook(CLAUDE, {"hook_event_name": "PreToolUse", "tool_name": "Edit",
                             "tool_input": {"file_path": os.path.join(repo, path)},
                             "cwd": repo, "session_id": session})


def claude_shell(repo, session, command, tool="Bash"):
    """A shell, run the way the harness runs one: PreToolUse, then the command, then PostToolUse.

    The command is actually EXECUTED between the two hooks — which is the whole design. Nothing
    reads its text; the hooks look at the repo before and after and see what it did.
    """
    payload = {"tool_name": tool, "tool_input": {"command": command},
               "cwd": repo, "session_id": session}
    pre = run_hook(CLAUDE, {**payload, "hook_event_name": "PreToolUse"})
    if pre.returncode != 0:
        return pre                                  # blocked before it ran
    subprocess.run(command, cwd=repo, shell=True, capture_output=True)
    return run_hook(CLAUDE, {**payload, "hook_event_name": "PostToolUse"})


# --- write DETECTION: the repo is the witness, not the command (SPEC.md §7a) --------------------

@pytest.mark.parametrize("command", [
    "git commit --allow-empty -m x",     # the obvious one
    "sed -i s/one/two/ a.txt",           # a mutation with no git in it
    "echo hi > b.txt",                   # a redirect
    "rm a.txt",                          # a delete
    # ...and the ones NO classifier can catch, because their effect is not in their text. Each of
    # these was invisible to v0.1 and wrote the tree unguarded.
    "python -c \"open('gen.py','w').write('x')\"",       # codegen, arbitrary program
    "sh -c 'printf hi >> a.txt'",                        # a write one indirection away
    "git stash list && echo x > c.txt",                  # the write is in the second segment
])
def test_a_shell_that_writes_is_caught_whatever_it_is_called(repo, command):
    """The false-NEGATIVE half of the old bug, closed. No list, no regex, no guess: the tree moved,
    so the session is a writer, so it holds the lock."""
    assert claude_shell(repo, "A", command).returncode == 0
    res = claude_edit(repo, "B")
    assert res.returncode == 2, f"A wrote the repo with {command!r} and never took the lock"
    assert "session A" in res.stderr


@pytest.mark.parametrize("command", [
    # Every one of these was REFUSED, in a real session, on a repo it had every right to read (#4)
    "cat a.txt",
    "git status --porcelain",
    "git log --oneline",
    "ls -la && cat a.txt",
    # ...and #7: a `>` inside a string is not a redirect, an arrow is not a redirect, and no
    # amount of quoting-awareness was ever going to be the last bug in that parser.
    'echo "a -> b"',
    'grep -n "a > b" a.txt',
    "python -c \"print(1 > 0)\"",
])
def test_a_shell_that_only_reads_costs_nothing(repo, command):
    """The false-POSITIVE half, closed by the same mechanism. A read leaves no trace in the repo, so
    it takes no lock — and B is never locked out by a session that only looked."""
    assert claude_shell(repo, "A", command).returncode == 0
    res = claude_edit(repo, "B")
    assert res.returncode == 0, (
        f"a session that only read the repo took the lease and locked B out:\n{res.stderr}")


def test_the_same_text_is_a_read_in_one_shell_and_a_write_in_another(repo):
    """The last nail in the classifier's coffin, and it turned up by accident in this suite.

    `git log --format='%h -> %s'` is a read under a POSIX shell: the `>` is inside quotes. Under
    cmd.exe, single quotes do not quote, so the `>` redirects and the command CREATES A FILE. The
    same text, two shells, opposite effects — so no parser can be right about it, not even a
    quoting-aware one, because it would have to know which shell was going to run it and how that
    shell quotes. Observation does not have to know any of that."""
    res = claude_shell(repo, "A", "git log --format='%h -> %s'")
    assert res.returncode == 0
    wrote = subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                           capture_output=True, text=True).stdout.strip()
    if wrote:                                    # cmd.exe: it redirected, so it is a writer
        assert claude_edit(repo, "B").returncode == 2
    else:                                        # a POSIX shell: it read, so it took nothing
        assert claude_edit(repo, "B").returncode == 0


def test_a_shell_cannot_write_into_a_repo_someone_else_holds(repo):
    """The window, CLOSED — and this is the test that says so.

    An earlier draft of v1 detected shell writes only afterwards, which left a gap: within one tool
    call, two sessions could both write one checkout. That gap was written into the ledger as a
    'hypothesis' with a falsifier that would fire the first time it cost someone their work — which
    is not a hypothesis, it is a hole with an alibi. A shell now takes the lock BEFORE it runs, so
    the second session never gets to write at all."""
    assert claude_edit(repo, "A").returncode == 0                     # A holds the lock
    res = claude_shell(repo, "B", "echo hi > b.txt")                  # B tries to write
    assert res.returncode == 2                                        # ...and never runs
    assert "REPO LOCKED" in res.stderr
    assert not os.path.exists(os.path.join(repo, "b.txt")), "B's write was not prevented"


def test_the_refusal_tells_a_blocked_session_what_it_needs_to_act(repo):
    """A gate that stops you without saying what it is waiting for, what you may still do, or how to
    wait, leaves an agent rattling the handle of a door with no sign on it. The refusal must carry
    all three, or it is just a wall."""
    assert claude_shell(repo, "A", "echo one > b.txt").returncode == 0     # A becomes the writer
    res = run_hook(CLAUDE, {"hook_event_name": "PreToolUse", "tool_name": "Edit",
                            "tool_input": {"file_path": os.path.join(repo, "a.txt")},
                            "cwd": repo, "session_id": "B"})
    assert res.returncode == 2
    err = res.stderr

    assert "session A" in err                        # who
    assert "b.txt" in err                            # ...and what they have actually touched
    assert "Edit a.txt" in err                       # what of MINE was refused
    assert "frees in" in err                         # when it frees
    assert "Read / Grep / Glob" in err               # what I may still do
    assert "lock_wait" in err                        # ...and how to wait, since `sleep` is blocked
    assert "sleep" in err                            # named explicitly: it is the trap


def test_the_refusal_reports_what_the_holder_is_doing_NOW(repo):
    """A stale intent is worse than none: it is the field a refused session reads to decide whether
    to wait ten seconds or leave, so a holder still advertised as 'Edit server.py' an hour after it
    moved on to a test run is actively lying to the session it is blocking."""
    claude_edit(repo, "A", path="a.txt")                                # A takes the lock editing
    assert claude_shell(repo, "A", "git log --oneline").returncode == 0  # ...then does something else

    res = claude_edit(repo, "B")
    assert res.returncode == 2
    assert "git log" in res.stderr, "the refusal still advertises the holder's FIRST action"


def test_a_blocked_session_can_wait_because_sleep_is_not_available_to_it(repo):
    """The hole the pessimistic hold opened, closed. `sleep` is a shell; the shell is what was
    refused; so waiting has to be reachable through a channel the hook does not gate."""
    from repolock import lock

    assert claude_shell(repo, "A", "echo one > b.txt").returncode == 0
    assert claude_shell(repo, "B", "sleep 1").returncode == 2, "B cannot even wait via the shell"

    # ...so it waits through the ungated channel instead. A's lock is live, so this times out
    # rather than lying, and it says how much lease is left.
    v = lock.wait_until_free(repo, timeout_seconds=1, poll_seconds=0.2)
    assert v["status"] == "still_held"
    assert "left" in v["message"]

    run_hook(CLAUDE, {"hook_event_name": "Stop", "cwd": repo, "session_id": "A"})  # A commits/leaves
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "b"], cwd=repo, check=True)
    run_hook(CLAUDE, {"hook_event_name": "Stop", "cwd": repo, "session_id": "A"})

    v = lock.wait_until_free(repo, timeout_seconds=5, poll_seconds=0.2)
    assert v["status"] == "free"
    assert claude_edit(repo, "B").returncode == 0     # and B may now proceed


def test_a_backgrounded_task_keeps_the_lock_because_its_writes_have_not_happened_yet(repo):
    """The hole that observation-after-the-fact would otherwise have shipped.

    A backgrounded command returns IMMEDIATELY — the harness hands back a task id and PostToolUse
    fires at LAUNCH. The fingerprint has not moved, because the command has not done anything yet.
    Settling on that would release the lock and let `npm run dev` write the tree unguarded for the
    next hour. The harness declares `run_in_background`, so we hold instead of guessing."""
    pre = run_hook(CLAUDE, {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                            "tool_input": {"command": "npm run dev", "run_in_background": True},
                            "cwd": repo, "session_id": "A"})
    assert pre.returncode == 0
    post = run_hook(CLAUDE, {"hook_event_name": "PostToolUse", "tool_name": "Bash",
                             "tool_input": {"command": "npm run dev", "run_in_background": True},
                             "cwd": repo, "session_id": "A"})
    assert post.returncode == 0
    assert "background" in post.stdout.lower()

    res = claude_edit(repo, "B")
    assert res.returncode == 2, "the lock was released under a live background process"


def test_the_refusal_issues_a_ticket_that_lets_the_blocked_session_subscribe(repo):
    """A blocked session cannot run ANY shell here — including the background waiter that would let
    it get on with something else. So the gate mints the one command it will allow, and allows that
    string and nothing else."""
    from repolock.hooks import common

    assert claude_shell(repo, "A", "echo one > b.txt").returncode == 0     # A becomes the writer
    res = claude_edit(repo, "B")
    assert res.returncode == 2

    ticket = common.ticket_for("B", repo)
    assert ticket in res.stderr, "the refusal did not hand the session a way to subscribe"

    # The ticket runs, in the background, in the repo B is locked out of.
    ok = run_hook(CLAUDE, {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                           "tool_input": {"command": ticket, "run_in_background": True},
                           "cwd": repo, "session_id": "B"})
    assert ok.returncode == 0, "the session cannot run the waiter the gate itself issued"

    # ...and it is a capability, not a licence. One character more and it is a different string.
    for forged in (ticket + " && rm -rf src", ticket.replace("--ticket", "--x"),
                   common.ticket_for("SOMEONE-ELSE", repo)):
        bad = run_hook(CLAUDE, {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                "tool_input": {"command": forged}, "cwd": repo, "session_id": "B"})
        assert bad.returncode == 2, f"a forged ticket got through: {forged!r}"


def test_the_waiter_exits_when_the_lock_frees(repo):
    """What the harness wakes the session on: the process must actually die when the lock goes."""
    from repolock import waitfor

    assert claude_shell(repo, "A", "echo one > b.txt").returncode == 0
    assert waitfor.main([repo, "--timeout", "1"]) == 1        # still held: exits non-zero, says so

    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "b"], cwd=repo, check=True)
    run_hook(CLAUDE, {"hook_event_name": "Stop", "cwd": repo, "session_id": "A"})

    assert waitfor.main([repo, "--timeout", "5"]) == 0        # freed: exits 0 -> the harness wakes B


def _real_shell(kind: str) -> list[str] | None:
    """The shell a HARNESS actually spawns — which on Windows is not the one on PATH.

    `shutil.which("bash")` here finds `C:\\Windows\\System32\\bash.exe`: the WSL launcher, which on
    this machine has no distro and fails before it ever reaches python. Claude Code's Bash tool runs
    `/usr/bin/bash` — Git Bash, from Git for Windows. Testing the ticket against WSL would be testing
    a shell nobody uses, and would have let #10 through a second time.
    """
    if kind == "sh":
        for c in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"):
            if os.path.exists(c):
                return [c, "-c"]
        found = shutil.which("bash")
        return [found, "-c"] if found and "system32" not in found.lower() else None
    found = shutil.which("pwsh") or shutil.which("powershell")
    return [found, "-NoProfile", "-Command"] if found else None


@pytest.mark.parametrize("shell,spelling", [("sh", "sh"), ("pwsh", "pwsh")])
def test_the_minted_ticket_actually_RUNS_in_the_shell_it_is_minted_for(repo, shell, spelling):
    """The invariant this library shipped without, and paid for (xag/repolock#10).

    Two tests already covered the ticket. One asserted the gate RECOGNISES it; the other called
    `waitfor.main([repo])` in-process. Between them they never once put the minted string through a
    shell — and the minted string could not survive one. `sys.executable` went in unquoted, spelled
    with Windows backslashes, and bash ate every one of them:

        C:UserstransProjectsrepolock.venvScriptspython.exe: command not found   (exit 127)

    The waiter never ran, on any tape, ever. Two green tests and a feature that had never worked,
    because the only boundary that mattered — a real shell parsing a real string — was the one place
    neither test looked. A stub you never check against the real wire is not a test of the wire.

    So: mint it, hand it to the actual shell, and require it to reach the lock. The repo is FREE
    here, so a waiter that starts must print FREE and exit 0. A waiter that cannot start exits 127
    and says `command not found` — which is precisely, and only, what this catches.
    """
    from repolock.hooks import common

    launcher = _real_shell(shell)
    if not launcher:
        pytest.skip(f"no real {shell} on this machine")

    ticket = common.tickets_for("B", repo)[spelling]
    res = subprocess.run([*launcher, ticket], cwd=repo, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=120)
    out = (res.stdout or "") + (res.stderr or "")

    assert "command not found" not in out.lower() and res.returncode != 127, (
        f"the ticket the gate mints cannot be run by the shell it is minted for:\n{out}")
    assert res.returncode == 0 and "FREE" in out, (
        f"the waiter did not reach the lock (exit {res.returncode}):\n{out}")


def _stop(repo, session, already_asked=False):
    payload = {"hook_event_name": "Stop", "cwd": repo, "session_id": session}
    if already_asked:
        payload["stop_hook_active"] = True
    return run_hook(CLAUDE, payload)


def test_a_session_may_not_walk_away_holding_a_lock_on_a_dirty_tree(repo):
    """The #11 incident, at its root — and the fix is to refuse the premise, not manage it.

    A session parked on `chores` holding the lock, its tree "dirty" with one untracked artifact
    directory (`?? .devdata/`). The lock stayed for the full lease, and the next session was refused
    `ls && git log` — a pure read — for ten minutes. The old rule accepted the dirty handback and
    made everyone else pay for it.

    So the handback is refused instead: commit, ignore, or stash. The lock then releases itself
    against a clean tree, and there is no parked lock for anyone to trip over.
    """
    assert claude_shell(repo, "A", "echo wip > wip.txt").returncode == 0   # A writes, A holds
    res = _stop(repo, "A")

    assert res.returncode == 2, "a session was allowed to hand back holding a lock on a dirty tree"
    assert "commit" in res.stderr.lower() and "stash" in res.stderr.lower()
    assert ".gitignore" in res.stderr, "an artifact directory needs the ignore route, not a commit"
    assert claude_edit(repo, "B").returncode == 2, "the lock should still be A's while it cleans up"


def test_cleaning_the_tree_releases_the_lock_by_itself(repo):
    """The good path, and the whole point of refusing the handback: A commits, and the checkout is
    free. Nobody had to call release, and B walks in unblocked."""
    assert claude_shell(repo, "A", "echo wip > wip.txt").returncode == 0
    assert _stop(repo, "A").returncode == 2                      # refused: dirty

    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "wip"], cwd=repo, check=True)

    assert _stop(repo, "A").returncode == 0, "a clean tree must be allowed to hand back"
    assert claude_edit(repo, "B").returncode == 0, "the lock was not released against a clean tree"


def test_the_session_is_asked_once_and_then_left_alone(repo):
    """The escape, and it is not optional: a gate that will not let a session stop is a worse
    failure than any lock it could be protecting. `stop_hook_active` is the harness saying "you
    already blocked this once" — so we hold the lock, say so, and get out of the way."""
    assert claude_shell(repo, "A", "echo wip > wip.txt").returncode == 0
    assert _stop(repo, "A").returncode == 2                      # asked once

    res = _stop(repo, "A", already_asked=True)                   # ...and not twice

    assert res.returncode == 0, "the session was refused its own stop twice — that is a cage"
    assert "uncommitted" in res.stdout.lower()                   # held, and it says so


def test_an_ignored_artifact_directory_does_not_hold_the_lock(repo):
    """The permanent fix for the `.devdata/` class, and why the refusal names it explicitly.

    An untracked artifact directory makes the tree dirty FOREVER, so every session that ever stops
    in that repo parks a lock on it. Ignoring it is the cure — and git's own porcelain then stops
    reporting it, so the handback is clean and the lock lets go on its own."""
    os.makedirs(os.path.join(repo, ".devdata"), exist_ok=True)
    with open(os.path.join(repo, ".devdata", "cache.bin"), "w") as f:
        f.write("artifact")

    assert claude_shell(repo, "A", "echo hi > b.txt").returncode == 0
    assert _stop(repo, "A").returncode == 2                      # dirty: b.txt AND .devdata/

    with open(os.path.join(repo, ".gitignore"), "w") as f:       # the route the refusal advises
        f.write(".devdata/\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "ignore artifacts"], cwd=repo, check=True)

    assert _stop(repo, "A").returncode == 0
    assert claude_edit(repo, "B").returncode == 0, "an IGNORED artifact dir still held the lock"


def test_a_read_hands_the_speculative_lock_straight_back(repo):
    """What makes the pessimistic hold affordable, and keeps it from becoming #4.

    A shell takes the lock without anyone judging the command — but a shell that turns out to have
    read holds it for its own tool call and not one second longer."""
    assert claude_shell(repo, "A", "cat a.txt").returncode == 0
    assert claude_edit(repo, "B").returncode == 0, "a read kept the lock it took on speculation"


def test_a_read_gives_the_lock_back_even_when_the_tree_is_already_dirty(repo):
    """The release must not be refused by dirt this session did not make. `release` normally guards
    the idle boundary by refusing a dirty tree; here the fingerprint proves we changed nothing, so
    holding the repo hostage over someone else's uncommitted work would be #4 in a hat."""
    with open(os.path.join(repo, "a.txt"), "w") as f:
        f.write("someone else's half-finished work")
    assert claude_shell(repo, "A", "cat a.txt").returncode == 0
    assert claude_edit(repo, "B").returncode == 0


def test_a_write_is_locked_on_the_repo_that_owns_the_file_not_the_cwd(tmp_path, repo, monkeypatch):
    """#8: the session sits in one repo and edits another. The lock must land on the repo being
    WRITTEN, and a write to no repo at all must take no lock."""
    other = tmp_path / "other"
    other.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=other, check=True)
    (other / "b.txt").write_text("x")

    # session A, cwd=repo, edits a file in `other`
    res = run_hook(CLAUDE, {"hook_event_name": "PreToolUse", "tool_name": "Edit",
                            "tool_input": {"file_path": str(other / "b.txt")},
                            "cwd": str(repo), "session_id": "A"})
    assert res.returncode == 0

    assert claude_edit(repo, "B").returncode == 0            # `repo` was never locked...
    res = run_hook(CLAUDE, {"hook_event_name": "PreToolUse", "tool_name": "Edit",
                            "tool_input": {"file_path": str(other / "b.txt")},
                            "cwd": str(other), "session_id": "C"})
    assert res.returncode == 2                               # ...`other` was
    assert "session A" in res.stderr


def test_a_write_outside_every_repo_takes_no_lock(repo, tmp_path):
    """A scratch file under %TEMP% is not a working copy. It used to take the lock on whatever repo
    the session was sitting in, and could be refused one."""
    assert claude_edit(repo, "A").returncode == 0
    res = run_hook(CLAUDE, {"hook_event_name": "PreToolUse", "tool_name": "Write",
                            "tool_input": {"file_path": str(tmp_path / "scratch" / "notes.md")},
                            "cwd": str(tmp_path), "session_id": "B"})
    assert res.returncode == 0


# --- Claude Code (adapter #1) ---------------------------------------------------

def test_claude_hook_takes_the_lock_and_holds_out_a_second_session(repo):
    assert claude_edit(repo, "A").returncode == 0
    res = claude_edit(repo, "B")
    assert res.returncode == 2                      # exit 2 is the block
    assert "REPO LOCKED" in res.stderr
    assert "session A" in res.stderr


def test_claude_gates_a_powershell_write_like_any_other(repo):
    """#1: on Windows PowerShell is the shell that runs. It is a tool name like any other, and the
    hook must not care which one it was — nor read its command."""
    assert claude_shell(repo, "A", "echo hi > b.txt", tool="PowerShell").returncode == 0
    res = claude_edit(repo, "B")
    assert res.returncode == 2
    assert "session A" in res.stderr


def test_claude_stop_releases_a_clean_tree(repo):
    claude_edit(repo, "A")
    res = run_hook(CLAUDE, {"hook_event_name": "Stop", "cwd": repo, "session_id": "A"})
    assert res.returncode == 0
    assert claude_edit(repo, "B").returncode == 0   # free again


def test_claude_fails_open_on_garbage_input(repo):
    res = subprocess.run([sys.executable, CLAUDE], input="not json",
                         capture_output=True, text=True, timeout=60)
    assert res.returncode == 0


def test_without_the_settle_hook_it_degrades_instead_of_locking_every_read(repo):
    """A `PreToolUse`-only install — a config typo, or ANY session that snapshotted its hooks before
    PostToolUse was added — must not turn every read into a ten-minute lock.

    The pessimistic hold is affordable only because the lock comes straight back when a command
    turns out to have read. With no settle event, nothing hands it back, and a `cat` holds the repo
    for a full lease: bug #4, reproduced from a missing line of JSON. The hook detects it (a
    fingerprint memo that survived the last call proves settle never ran), gives back what it should
    not be holding, and degrades to unguarded shells — loudly."""
    def pre_only(session, command):                 # PreToolUse fires; PostToolUse never does
        return run_hook(CLAUDE, {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                 "tool_input": {"command": command}, "cwd": repo,
                                 "session_id": session})

    assert pre_only("A", "cat a.txt").returncode == 0        # 1st call: takes the speculative lock
    res = pre_only("A", "cat a.txt")                         # 2nd: the memo survived => no settle
    assert res.returncode == 0
    assert "DEGRADED" in res.stdout and "PostToolUse" in res.stdout

    other = claude_edit(repo, "B")
    assert other.returncode == 0, (
        "a reading session held the repo against B because settle was never wired — this is #4")


def test_a_missing_flight_recorder_costs_the_tape_not_the_lock(repo, tmp_path, monkeypatch):
    """`flight-recorder` is an OPTIONAL extra and recording is ON by default, so a plain
    `pip install .` has no recorder. The ImportError used to travel up into the fail-open handler
    and no-op every hook: the lock looked installed, printed one line to a stderr nobody reads, and
    guarded nothing. An optional dependency that silently disables the tool when absent is not
    optional — it is a hard dependency with a bug."""
    stub = tmp_path / "no_recorder"
    stub.mkdir()
    (stub / "flight_recorder.py").write_text("raise ImportError('not installed')")

    env = dict(os.environ, REPOLOCK_DISABLED="0", REPOLOCK_DIR=str(tmp_path / "locks"),
               PYTHONPATH=str(stub))
    def hook(session):
        p = {"hook_event_name": "PreToolUse", "tool_name": "Edit",
             "tool_input": {"file_path": os.path.join(repo, "a.txt")},
             "cwd": repo, "session_id": session}
        return subprocess.run([sys.executable, CLAUDE], input=json.dumps(p), env=env,
                              capture_output=True, text=True)

    assert hook("A").returncode == 0
    res = hook("B")
    assert res.returncode == 2, "with no recorder installed, the lock stopped locking entirely"


def test_the_kill_switch_stops_every_event(repo, tmp_path, monkeypatch):
    """A lock you cannot switch off is worse than no lock. The file reaches sessions that already
    snapshotted their hooks — the ones you most need to free."""
    env = dict(os.environ, REPOLOCK_DISABLED="1", REPOLOCK_DIR=str(tmp_path / "locks"))
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Edit",
               "tool_input": {"file_path": os.path.join(repo, "a.txt")},
               "cwd": repo, "session_id": "A"}
    assert subprocess.run([sys.executable, CLAUDE], input=json.dumps(payload), env=env,
                          capture_output=True, text=True).returncode == 0
    payload["session_id"] = "B"
    assert subprocess.run([sys.executable, CLAUDE], input=json.dumps(payload), env=env,
                          capture_output=True, text=True).returncode == 0   # nobody is ever blocked


# --- Cursor (adapter #2) --------------------------------------------------------

def cursor_write(repo, conversation, path="a.txt"):
    return run_hook(CURSOR, {"hook_event_name": "preToolUse", "tool_name": "Write",
                             "tool_input": {"file_path": os.path.join(repo, path)}, "cwd": repo,
                             "conversation_id": conversation, "workspace_roots": [repo]})


def test_cursor_hook_takes_the_lock_and_denies_a_second_conversation(repo):
    res = cursor_write(repo, "conv-1")
    assert res.returncode == 0
    assert json.loads(res.stdout)["permission"] == "allow"

    res = cursor_write(repo, "conv-2")
    verdict = json.loads(res.stdout)
    assert verdict["permission"] == "deny"
    assert "REPO LOCKED" in verdict["agent_message"]
    assert "conv-1" in verdict["agent_message"]


def test_cursor_detects_a_shell_write_at_the_next_event(repo):
    """Cursor's post-tool event is not verified against the real client, so this adapter reconciles
    lazily — the write is discovered on the way into the next hook call, not immediately."""
    before = {"hook_event_name": "beforeShellExecution", "cwd": repo,
              "conversation_id": "conv-1", "command": "echo hi > b.txt", "workspace_roots": [repo]}
    assert json.loads(run_hook(CURSOR, before).stdout)["permission"] == "allow"
    subprocess.run("echo hi > b.txt", cwd=repo, shell=True)

    run_hook(CURSOR, {**before, "command": "cat a.txt"})        # next event: catches up, locks
    verdict = json.loads(cursor_write(repo, "conv-2").stdout)
    assert verdict["permission"] == "deny"
    assert "conv-1" in verdict["agent_message"]


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
