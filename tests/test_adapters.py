"""The Claude Code adapter, driven over its real wire format — and held to the NEW contract.

The old suite asserted who got refused, when, and with what ticket in hand. All of that died with
the lock. What replaced it is one property, and it is the first test in this file because every
other behaviour is subordinate to it:

    **No tool call is ever refused.** The single exit 2 left in the library is the Stop boundary,
    where a DEPARTING session is asked once about its own dirty tree — and that blocks no other
    agent, ever.

Everything else here is the harness plumbing that has to survive for the courier and the witness
to be trustworthy: fail-open on garbage, the off switch reaching running sessions, the drift note,
and the blind-witness warning when half the install is missing.
"""

import json
import os
import subprocess
import sys

import pytest

from transponder import scope

CLAUDE = os.path.join(os.path.dirname(__file__), "..", "transponder", "hooks", "claude_code.py")


def run_hook(script, payload):
    res = subprocess.run([sys.executable, script], input=json.dumps(payload),
                         capture_output=True, text=True, timeout=60)
    return res


def claude_edit(repo, session, path="a.txt"):
    """The PreToolUse half of an Edit, on its own — for the checks that are about what the courier
    says on the way in (or, mostly, does not say)."""
    return run_hook(CLAUDE, {"hook_event_name": "PreToolUse", "tool_name": "Edit",
                             "tool_input": {"file_path": os.path.join(repo, path)},
                             "cwd": repo, "session_id": session})



def claude_shell(repo, session, command, tool="Bash"):
    """A shell, run the way the harness runs one: PreToolUse, the command, PostToolUse. Nothing
    reads its text; the witness looks at the repo before and after and sees what it did."""
    payload = {"tool_name": tool, "tool_input": {"command": command},
               "cwd": repo, "session_id": session}
    pre = run_hook(CLAUDE, {**payload, "hook_event_name": "PreToolUse"})
    assert pre.returncode == 0, f"a PreToolUse refused a shell: {pre.stderr}"
    subprocess.run(command, cwd=repo, shell=True, capture_output=True)
    post = run_hook(CLAUDE, {**payload, "hook_event_name": "PostToolUse"})
    post.pre_stdout = pre.stdout
    return post


# --- THE contract ---------------------------------------------------------------------------------

def test_no_tool_call_is_ever_refused(repo):
    """The property the project turned on. Its predecessor refused readers (#4), refused a
    `print("a -> b")` (#7), refused the waiter it had itself minted (#10), and refused `ls` for ten
    minutes because someone left an artifact dir behind (#11). Now: claims on the map, agents in
    each other's regions, undeclared sessions, MCP calls, shells — NOTHING is refused. If this test
    ever needs an exception added, the project has changed course and the spec must say so first."""
    os.makedirs(os.path.join(repo, "api"), exist_ok=True)
    scope.declare(repo, "A", ["api/**"], "the rate limiter")

    calls = [
        # an undeclared session, everywhere, with every kind of tool
        {"tool_name": "Edit", "tool_input": {"file_path": os.path.join(repo, "api", "x.py")},
         "cwd": repo, "session_id": "B"},                       # a write INTO A's region
        {"tool_name": "Bash", "tool_input": {"command": "echo hi > api/y.py"},
         "cwd": repo, "session_id": "B"},                       # a shell aimed at A's region
        {"tool_name": "PowerShell", "tool_input": {"command": "cat a.txt"},
         "cwd": repo, "session_id": "B"},
        {"tool_name": "mcp__claude_ai_Gmail__search_threads", "tool_input": {},
         "cwd": repo, "session_id": "B"},
        {"tool_name": "mcp__repo-scope__declare_scope", "tool_input": {},
         "cwd": repo, "session_id": "B"},
        # ...and a declared participant reaching outside its own scope
        {"tool_name": "Edit", "tool_input": {"file_path": os.path.join(repo, "api", "z.py")},
         "cwd": repo, "session_id": "C"},
    ]
    scope.declare(repo, "C", ["web/**"], "the page")
    for payload in calls:
        res = run_hook(CLAUDE, {**payload, "hook_event_name": "PreToolUse"})
        assert res.returncode == 0, (
            f"{payload['tool_name']} was REFUSED for {payload['session_id']}:\n{res.stderr}")


def test_the_one_exception_is_the_departing_sessions_own_stop(repo):
    """exit 2 exists in one place: a participant leaving a dirty tree is asked, once, to commit,
    ignore or stash — and never twice (`stop_hook_active`). It blocks no other agent anything."""
    scope.declare(repo, "A", ["**"], "working")
    with open(os.path.join(repo, "a.txt"), "w") as f:
        f.write("half-finished")

    first = run_hook(CLAUDE, {"hook_event_name": "Stop", "cwd": repo, "session_id": "A"})
    assert first.returncode == 2, "the ask-once at Stop is the one deliberate exception"
    assert "commit" in first.stderr.lower() and "stash" in first.stderr.lower()
    assert ".gitignore" in first.stderr, "an artifact needs the ignore route, not a commit"

    second = run_hook(CLAUDE, {"hook_event_name": "Stop", "cwd": repo, "session_id": "A",
                               "stop_hook_active": True})
    assert second.returncode == 0, "asked twice — that is a cage, not a question"

    # ...and an agent that was never a participant here is not even asked
    other = run_hook(CLAUDE, {"hook_event_name": "Stop", "cwd": repo, "session_id": "Z"})
    assert other.returncode == 0


def test_a_clean_stop_takes_the_session_off_the_map(repo):
    scope.declare(repo, "A", ["**"], "working")
    res = run_hook(CLAUDE, {"hook_event_name": "Stop", "cwd": repo, "session_id": "A"})
    assert res.returncode == 0
    assert not scope.declared("A"), "a departed session left stale information on the map"


# --- the courier, and the day it said nothing --------------------------------------------------

def test_the_intro_speaks_to_the_first_agent_of_the_day(repo):
    """The regression that cost a working day, and the reason it could never show up in a test that
    had two agents in it: the intro used to require somebody ELSE on the map, so it was silent in
    the only state the map is ever in at the start — empty. First agent told nothing, therefore
    declaring nothing, therefore the second agent an hour later also told nothing.

    The bootstrap has to run on an empty map or it never runs at all.

    It is driven from UserPromptSubmit rather than SessionStart because that is where the intro now
    lives: at arrival the human has not spoken yet, so "what will you write to?" is unanswerable,
    and spending the once-only note there is what left long sessions never declaring anything."""
    assert not scope.live(), "the fixture handed us a map that was already primed"
    res = claude_prompt(repo, "FIRST")
    assert res.returncode == 0
    assert "declare_work" in res.stdout, (
        "the first agent of the day was told nothing — so the map stays empty, and everyone who "
        "arrives after it is told nothing either")

    again = claude_prompt(repo, "FIRST")
    assert not again.stdout.strip(), "introduced twice — an informer that repeats itself is skimmed"


def test_an_agent_introduced_alone_is_told_when_somebody_arrives(repo):
    """"You are alone" is the one thing the intro can say that stops being true by itself. Said
    once on arrival, it would otherwise be the last word a session ever hears about the map.

    The priming call has to be the one that actually introduces, or this test passes while covering
    nothing: with the intro moved off SessionStart, priming there left FIRST un-introduced, so the
    prompt below delivered its FIRST intro (the roster one) and the alone -> joined transition this
    test is named for went unexercised."""
    assert claude_prompt(repo, "FIRST").stdout.count("NOBODY HAS DECLARED") == 1, (
        "FIRST was not introduced to an empty map, so there is no transition to observe")
    scope.declare(repo, "SECOND", ["api/**"], "the rate limiter")

    res = run_hook(CLAUDE, {"hook_event_name": "UserPromptSubmit", "cwd": repo,
                            "session_id": "FIRST"})
    assert "NOT THE ONLY AGENT" in res.stdout and "SECOND" in res.stdout, (
        "an agent introduced to an empty map never heard that the map filled up")
    assert "the rate limiter" in res.stdout, "told who, not told what they are doing"

    third = run_hook(CLAUDE, {"hook_event_name": "UserPromptSubmit", "cwd": repo,
                              "session_id": "FIRST"})
    assert "NOT THE ONLY AGENT" not in third.stdout, "the roster intro repeated"


# --- the plumbing the contract stands on -----------------------------------------------------------

def test_fails_open_on_garbage_input(repo):
    res = subprocess.run([sys.executable, CLAUDE], input="not json",
                         capture_output=True, text=True, timeout=60)
    assert res.returncode == 0


def test_the_kill_switch_silences_every_event(repo, tmp_path):
    """Off must mean off, everywhere, instantly — an informer that cannot be switched off is a
    spammer with tenure. The file reaches sessions that already snapshotted their hooks."""
    os.makedirs(os.path.join(repo, "api"), exist_ok=True)
    scope.declare(repo, "A", ["api/**"], "working")
    env_ = dict(os.environ, TRANSPONDER_DISABLED="1", TRANSPONDER_DIR=str(tmp_path / "locks"))
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Edit",
               "tool_input": {"file_path": os.path.join(repo, "api", "x.py")},
               "cwd": repo, "session_id": "B"}
    res = subprocess.run([sys.executable, CLAUDE], input=json.dumps(payload), env=env_,
                         capture_output=True, text=True)
    assert res.returncode == 0 and not res.stdout.strip(), "the switch is on and it still spoke"


def test_a_missing_flight_recorder_costs_the_tape_not_the_courier(repo, tmp_path):
    """flight-recorder is an OPTIONAL extra; recording is on by default. A plain install has no
    recorder, and the ImportError must not silence the notes — an optional dependency that disables
    the tool when absent is a hard dependency with a bug."""
    stub = tmp_path / "no_recorder"
    stub.mkdir()
    (stub / "flight_recorder.py").write_text("raise ImportError('not installed')")
    os.makedirs(os.path.join(repo, "api"), exist_ok=True)
    scope.declare(repo, "A", ["api/**"], "the rate limiter")

    env_ = dict(os.environ, TRANSPONDER_DISABLED="0", TRANSPONDER_DIR=os.environ["TRANSPONDER_DIR"],
                PYTHONPATH=str(stub))
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Edit",
               "tool_input": {"file_path": os.path.join(repo, "api", "x.py")},
               "cwd": repo, "session_id": "B"}
    res = subprocess.run([sys.executable, CLAUDE], input=json.dumps(payload), env=env_,
                         capture_output=True, text=True)
    assert res.returncode == 0
    assert "NOT THE ONLY AGENT" in res.stdout, (
        "with no recorder installed, the courier went quiet entirely")



def test_session_start_reports_drift(repo):
    """The read-side check — the one part of this library that was never wrong.

    It now runs over the checkouts on the MAP rather than the session's cwd, so the declare is what
    puts this repo in play. That is a real narrowing, recorded rather than hidden: a session that
    declares nothing gets no drift check, because there is no longer any guess about which checkout
    it means."""
    scope.declare(repo, "A", ["a.txt"], "working")
    assert run_hook(CLAUDE, {"hook_event_name": "SessionStart", "cwd": repo,
                             "session_id": "A"}).returncode == 0
    with open(os.path.join(repo, "b.txt"), "w") as f:
        f.write("two")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "two"], cwd=repo, check=True)

    res = run_hook(CLAUDE, {"hook_event_name": "SessionStart", "cwd": repo, "session_id": "A"})
    assert res.returncode == 0
    assert "moved" in res.stdout


def test_a_write_outside_every_repo_is_nobodys_business(repo, tmp_path):
    res = run_hook(CLAUDE, {"hook_event_name": "PreToolUse", "tool_name": "Write",
                            "tool_input": {"file_path": str(tmp_path / "scratch" / "notes.md")},
                            "cwd": str(tmp_path), "session_id": "B"})
    assert res.returncode == 0 and not res.stdout.strip()


# --- the ask, where it can be answered -------------------------------------------------------------
#
# The intro was delivered once, at SessionStart, which is before the human has said what the work
# is. So it asked "what will you write to?" at the one moment that question has no answer, and being
# once-only it never asked again: a session ran for hours, edited freely, and called nothing. These
# tests hold the two halves of the fix — the ask moved to where it is answerable, and a recurring
# note that reports what the agent has actually written.


def claude_prompt(repo, session, prompt="do the thing"):
    return run_hook(CLAUDE, {"hook_event_name": "UserPromptSubmit", "cwd": repo,
                             "session_id": session, "prompt": prompt})


def test_the_intro_waits_for_a_prompt_it_can_be_answered_from(repo):
    """SessionStart must NOT spend the once-only intro. It fires before the first human word."""
    start = run_hook(CLAUDE, {"hook_event_name": "SessionStart", "cwd": repo, "session_id": "A"})
    assert start.returncode == 0
    assert "NOBODY HAS DECLARED" not in start.stdout, (
        "the intro was spent at arrival again — asked before the work was known, never asked after")
    assert "NOBODY HAS DECLARED" in claude_prompt(repo, "A").stdout


def test_undeclared_writes_are_named_back_at_the_next_prompt(repo):
    claude_prompt(repo, "A")                                  # spend the intro
    claude_edit(repo, "A", "a.txt")
    res = claude_prompt(repo, "A")
    assert "DECLARED NOTHING" in res.stdout
    assert "a.txt" in res.stdout, "it said something was written but not what"
    assert "declare_work" in res.stdout


def test_the_remedy_carries_the_one_argument_an_agent_cannot_look_up(repo):
    """`session_id` is not visible from inside an agent's own context; it has to be told. A remedy
    whose first step is 'work out who you are' is a remedy that does not get run."""
    claude_prompt(repo, "A")
    claude_edit(repo, "A", "a.txt")
    out = claude_prompt(repo, "A").stdout
    assert "'A'" in out and "session_id" in out


def test_the_remedy_is_copy_pasteable(repo):
    """It carried `minutes=<how long>`, which is a syntax error. A remedy an agent has to repair
    before running is a remedy that mostly does not get run."""
    import ast

    claude_prompt(repo, "A")
    claude_edit(repo, "A", "a.txt")
    out = json.loads(claude_prompt(repo, "A").stdout)["hookSpecificOutput"]["additionalContext"]
    call = out[out.index("declare_work("):].strip()
    ast.parse(call)                                   # raises if the snippet does not parse

    assert "\\\\" not in call, (
        "the path came out backslash-escaped and lowercased; a remedy that looks mangled reads as "
        "a broken tool")


def test_the_note_speaks_on_a_doubling_schedule(repo):
    """Every prompt is wallpaper; once is the bug being fixed. 1, 2, 4, ... is neither."""
    claude_prompt(repo, "A")
    claude_edit(repo, "A", "a.txt")
    assert "DECLARED NOTHING" in claude_prompt(repo, "A").stdout          # 1st
    assert "DECLARED NOTHING" not in claude_prompt(repo, "A").stdout, (
        "it spoke with nothing new to say — this is how a reader is taught to skim")
    claude_edit(repo, "A", "b.txt")
    assert "DECLARED NOTHING" in claude_prompt(repo, "A").stdout          # 2
    claude_edit(repo, "A", "c.txt")
    assert "DECLARED NOTHING" not in claude_prompt(repo, "A").stdout      # 3 < 4
    claude_edit(repo, "A", "d.txt")
    assert "DECLARED NOTHING" in claude_prompt(repo, "A").stdout          # 4


def test_declaring_silences_it(repo):
    claude_prompt(repo, "A")
    claude_edit(repo, "A", "a.txt")
    assert "DECLARED NOTHING" in claude_prompt(repo, "A").stdout
    scope.declare(repo, "A", ["**"], "the whole checkout")
    claude_edit(repo, "A", "b.txt")
    out = claude_prompt(repo, "A").stdout
    assert "DECLARED NOTHING" not in out and "OUTSIDE WHAT YOU DECLARED" not in out, (
        "an agent doing exactly what it said it would was told off for it")


def test_a_write_outside_a_held_scope_asks_for_extend_not_declare(repo):
    os.makedirs(os.path.join(repo, "api"), exist_ok=True)
    scope.declare(repo, "A", ["api/**"], "the rate limiter")
    claude_prompt(repo, "A")
    claude_edit(repo, "A", os.path.join("api", "x.py"))
    assert "OUTSIDE WHAT YOU DECLARED" not in claude_prompt(repo, "A").stdout

    claude_edit(repo, "A", "a.txt")
    res = claude_prompt(repo, "A")
    assert "OUTSIDE WHAT YOU DECLARED" in res.stdout
    assert "extend_work" in res.stdout, "told to declare again while already holding a region"


def test_a_neighbour_on_the_map_does_not_change_which_remedy_is_offered(repo):
    """The two halves of the note are independent and were briefly not: rendering the roster bound
    the same name the remedy branch tests. Every other test here has an empty map, so the roster
    loop never ran and the shadow never showed.

    The failure needs BOTH: a session holding nothing (so the remedy should be declare_work) and a
    neighbour to render (so the name gets rebound to a non-empty scope, and the branch flips)."""
    os.makedirs(os.path.join(repo, "docs"), exist_ok=True)
    scope.declare(repo, "NEIGHBOUR", ["docs/**"], "the changelog")
    claude_prompt(repo, "A")
    claude_edit(repo, "A", "a.txt")

    out = claude_prompt(repo, "A").stdout
    assert "NEIGHBOUR" in out and "the changelog" in out
    assert "declare_work" in out and "extend_work" not in out, (
        "a session holding nothing was told to widen a region it does not have")


def test_it_names_the_overlap_between_what_you_wrote_and_what_they_declared(repo):
    """The one thing here worth interrupting for, and it invents nothing: the writes come from this
    session's own tool-call payloads, the region from a claim made before them. It is not the
    witness — it does not say the other agent's work was damaged, only where two intentions met."""
    os.makedirs(os.path.join(repo, "api"), exist_ok=True)
    scope.declare(repo, "SECOND", ["api/**"], "the rate limiter")
    claude_prompt(repo, "A")
    claude_edit(repo, "A", os.path.join("api", "x.py"))

    out = claude_prompt(repo, "A").stdout
    assert "INSIDE THAT REGION" in out
    assert "api/x.py" in out
    assert "send_message" in out and "'SECOND'" in out, "named the collision, offered no way to ask"
    assert "whose bytes are in the file now" in out, (
        "it must not imply it knows what happened to the other agent's work — it does not")


def test_a_write_that_misses_every_declared_region_says_so(repo):
    """The other half: a neighbour on the map is not by itself a collision, and saying so keeps the
    loud case loud."""
    os.makedirs(os.path.join(repo, "api"), exist_ok=True)
    scope.declare(repo, "SECOND", ["api/**"], "the rate limiter")
    claude_prompt(repo, "A")
    claude_edit(repo, "A", "a.txt")

    out = claude_prompt(repo, "A").stdout
    assert "INSIDE THAT REGION" not in out
    assert "Nothing you have written falls inside what they declared" in out


def test_a_shell_is_still_never_read_to_guess_what_it_wrote(repo):
    """#4 and #7 in one line. The note records tools that NAME their target; a command is text, and
    the moment anything decides by reading it we are back where the lock died."""
    claude_prompt(repo, "A")
    run_hook(CLAUDE, {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                      "tool_input": {"command": f"echo x > {os.path.join(repo, 'a.txt')}"},
                      "cwd": repo, "session_id": "A"})
    assert "DECLARED NOTHING" not in claude_prompt(repo, "A").stdout


def test_the_note_never_refuses_anything(repo):
    """The contract, restated over the new path: an agent that ignores every note keeps working."""
    claude_prompt(repo, "A")
    for name in ("a.txt", "b.txt", "c.txt", "d.txt", "e.txt"):
        assert claude_edit(repo, "A", name).returncode == 0
        assert claude_prompt(repo, "A").returncode == 0
