"""SPEC-v2, driven over the real hook wire format. The trial (§11) has to be able to fail.

The two claims that matter here, and they are not the same claim:

  1. IT WORKS — two agents with disjoint scopes both write one checkout, concurrently, where v1
     refused them. That is v2's entire winnings, and both times contention has ever arisen on a real
     tape, this is the case it was (§10.1).
  2. IT CHANGES NOTHING FOR ANYONE WHO IGNORES IT — a session that declares no scope holds `**` and
     behaves exactly as under v1. That is the property that makes the trial safe to run on the write
     path of every session on a machine, and it is tested here as carefully as the feature is.
"""

import json
import os
import subprocess
import sys

from repolock import scope
from repolock.hooks import common

from test_adapters import CLAUDE, claude_edit, claude_shell, run_hook


def declare(repo, session, paths, intent="working"):
    return scope.declare(repo, session, paths, intent)


def dirs(repo, *names):
    """Create the directories AND COMMIT them, which is not a detail.

    `git status --porcelain` collapses a wholly-untracked directory to a single `?? api/` line — it
    does not name the files inside it. So against an untracked tree the witness reports the DIRECTORY
    a write landed in, not the file. Attribution is still right (the directory is in the victim's
    region), but every real repo has tracked directories, and that is the case worth testing.
    """
    for name in names:
        os.makedirs(os.path.join(repo, name), exist_ok=True)
        with open(os.path.join(repo, name, ".keep"), "w") as f:
            f.write("")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "scaffold"], cwd=repo, check=True)


# --- the overlap relation: everything rests on this being right -----------------------------------

def test_overlap_is_decidable_or_it_is_refused():
    """A scope system that is unsure whether two regions touch would hand the same region to two
    agents and tell them both they were alone. So the grammar is small, and anything outside it is
    REJECTED rather than guessed at."""
    assert scope.overlaps("**", "src/a.py")                 # silence is `**`, and `**` is v1
    assert scope.overlaps("src/**", "src/api/x.py")         # a file inside a subtree
    assert scope.overlaps("src/**", "src/api/**")           # nested subtrees
    assert scope.overlaps("git:index", "git:index")
    assert not scope.overlaps("src/**", "web/**")           # siblings
    assert not scope.overlaps("src/a.py", "src/b.py")
    assert not scope.overlaps("git:index", "src/**")        # named vs path: different namespaces

    assert scope.bad_resource("src/*.py"), "a general glob has no decidable overlap and must be refused"
    assert not scope.bad_resource("src/**")


# --- 1. the winnings ------------------------------------------------------------------------------

def test_two_agents_with_disjoint_scopes_both_write_one_checkout(repo):
    """v2's entire reason to exist. Under v1 the second of these is refused; here both proceed.

    And this is not a hypothetical: BOTH of the only two collisions in the recorded history of this
    library were exactly this shape — two sessions working different directories, refused by v1 for
    no reason at all (§10.1)."""
    dirs(repo, "api", "web")

    assert declare(repo, "A", ["api/**"])["status"] == "granted"
    assert declare(repo, "B", ["web/**"])["status"] == "granted"

    assert claude_edit(repo, "A", path="api/server.py").returncode == 0
    assert claude_edit(repo, "B", path="web/page.js").returncode == 0, (
        "two agents with disjoint scopes were not allowed to work concurrently — this is the "
        "whole point of v2")


def test_an_overlapping_scope_is_refused_and_told_where_to_go(repo):
    """A conflict must be an ANSWER, not a wall (§2). v1 tells you to wait; v2 tells you what is
    free, so you can work RIGHT NOW instead."""
    dirs(repo, "api", "web", "docs")

    assert declare(repo, "A", ["api/**"], intent="the rate limiter")["status"] == "granted"
    v = declare(repo, "B", ["api/handlers/**"])           # nested inside A's region

    assert v["status"] == "conflict"
    assert v["conflicts"][0]["session"] == "A"
    assert "the rate limiter" in v["conflicts"][0]["intent"]
    assert any("web" in f for f in v["free_hint"]), "a conflict that does not say where to go is a wall"


def test_a_declared_write_outside_your_scope_is_refused_before_it_lands(repo):
    """§6: the one place v2 still PREVENTS. Edit carries its path, so no guessing is needed — and
    the refusal is useful in a way v1's never was: it does not say 'wait', it says 'declare'."""
    dirs(repo, "api", "web")
    declare(repo, "A", ["api/**"])
    declare(repo, "B", ["web/**"])

    res = claude_edit(repo, "A", path="web/page.js")      # A reaches into B's region
    assert res.returncode == 2
    assert "SCOPE CONFLICT" in res.stderr
    assert "agent B" in res.stderr


def test_writing_into_unclaimed_ground_just_extends_your_scope(repo):
    """Nobody is there, so nobody is hurt. Refusing here would make the protocol a nuisance, and a
    protocol that is a nuisance gets switched off."""
    dirs(repo, "api")
    declare(repo, "A", ["api/**"])
    assert claude_edit(repo, "A", path="notes.md").returncode == 0
    assert scope.covers(scope.scope_of(repo, "A"), "notes.md")


# --- 2. the witness: what a shell and an MCP call get instead of a gate ---------------------------

def test_a_shell_that_writes_into_another_agents_region_is_caught_and_named(repo):
    """§7/§7a, and the honest cost of v2. A shell's target is not knowable before it runs (§7a is
    the proof), so this write is NOT prevented — it is witnessed, named, and handed a remedy. That
    is strictly worse than v1's exclusion, and it is the trade the trial exists to judge."""
    dirs(repo, "api", "web")
    declare(repo, "A", ["api/**"], intent="the rate limiter")
    declare(repo, "B", ["web/**"])

    res = claude_shell(repo, "B", "echo boom > api/server.py")   # B writes into A's region

    assert res.returncode == 0, "a shell is witnessed, not gated — v2 cannot prevent this"
    assert "SCOPE VIOLATION" in res.stdout
    assert "api/server.py" in res.stdout
    assert "agent A" in res.stdout


def test_a_commit_that_sweeps_another_agents_work_is_the_loudest_thing_v2_says(repo):
    """§1a — THE founding incident, and the reason a scope is not just a set of paths.

    A holds api/**, B holds web/**, both are mid-edit. A runs `git add -A && git commit`, which
    sweeps B's half-finished work into A's commit. Path scopes alone hand this straight back, and it
    cannot be prevented by inspection (that means reading the command; §7a).

    So it is witnessed — and because a commit is the ONE violation that is cleanly recoverable, the
    message must carry the remedy, not merely the accusation."""
    dirs(repo, "api", "web")
    declare(repo, "A", ["api/**"])
    declare(repo, "B", ["web/**"])

    with open(os.path.join(repo, "web", "page.js"), "w") as f:
        f.write("B's half-finished work")                 # B is mid-edit, uncommitted

    res = claude_shell(repo, "A", "echo x > api/server.py && git add -A && git commit -qm sweep")

    assert "SCOPE VIOLATION" in res.stdout
    assert "web/page.js" in res.stdout, "the commit swept B's file and nobody noticed"
    assert "agent B" in res.stdout
    assert "git reset --soft HEAD~1" in res.stdout, "a recoverable violation must carry its remedy"


def test_a_scoped_session_that_only_reads_is_charged_nothing(repo):
    dirs(repo, "api")
    declare(repo, "A", ["api/**"])
    res = claude_shell(repo, "A", "cat a.txt")
    assert res.returncode == 0
    assert "VIOLATION" not in res.stdout


# --- 3. THE SAFETY PROPERTY: silence is `**`, and `**` is v1 --------------------------------------

def test_a_session_that_declares_nothing_is_bit_for_bit_v1(repo):
    """The property the whole trial rests on (§4, §11a). If this breaks, v2 is not an experiment —
    it is a change to the write path of every session on the machine."""
    assert claude_edit(repo, "A").returncode == 0
    res = claude_edit(repo, "B")
    assert res.returncode == 2                            # the v1 whole-checkout mutex, unchanged
    assert "REPO LOCKED" in res.stderr
    assert "session A" in res.stderr


def test_an_undeclared_session_is_held_out_by_a_scope_and_TAUGHT_the_way_in(repo):
    """An agent that declares nothing holds `**`, so it overlaps everyone — that is what keeps
    silence safe. But the refusal is the migration path: the way out is not to wait, it is to say
    what you are going to touch. It has to teach the protocol at the one moment the agent has a
    reason to care."""
    dirs(repo, "api")
    declare(repo, "A", ["api/**"], intent="the rate limiter")

    res = claude_edit(repo, "B", path="web/page.js")      # B has declared nothing
    assert res.returncode == 2
    assert "declare_scope" in res.stderr, "the refusal must teach the way in, not just say no"
    assert "agent A" in res.stderr and "api/**" in res.stderr

    declare(repo, "B", ["web/**"])                        # ...so B does what it was told
    assert claude_edit(repo, "B", path="web/page.js").returncode == 0


def test_a_scoped_session_gives_its_region_back_on_a_clean_tree(repo):
    dirs(repo, "api")
    declare(repo, "A", ["api/**"])
    assert claude_edit(repo, "A", path="api/x.py").returncode == 0
    with open(os.path.join(repo, "api", "x.py"), "w") as f:
        f.write("the edit the hook approved")      # the hook gates the write; the harness does it

    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "x"], cwd=repo, check=True)
    run_hook(CLAUDE, {"hook_event_name": "Stop", "cwd": repo, "session_id": "A"})

    assert not scope.declared(repo, "A"), "a clean scoped session must let its region go"
    assert declare(repo, "B", ["api/**"])["status"] == "granted"


def test_the_kill_switch_still_stops_everything(repo, tmp_path):
    """A trial on the write path of every session must be stoppable in one call, from inside a
    session that is wedged (§11a). If this fails, the trial does not run."""
    dirs(repo, "api")
    declare(repo, "A", ["api/**"])

    env = dict(os.environ, REPOLOCK_DISABLED="1", REPOLOCK_DIR=str(tmp_path / "locks"))
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Edit",
               "tool_input": {"file_path": os.path.join(repo, "api", "x.py")},
               "cwd": repo, "session_id": "B"}
    res = subprocess.run([sys.executable, CLAUDE], input=json.dumps(payload), env=env,
                         capture_output=True, text=True)
    assert res.returncode == 0, "the off switch did not reach the scope path"
