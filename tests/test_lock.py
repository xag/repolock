"""The working-copy lock: behaviour, against a real git checkout and a real lockfile.

Exclusion, reentrancy, the crashed holder, the dirty tree, the idle boundary, the stale
reader. The trajectory oracle — the half of the suite that can condemn a bug the behaviour
tests cannot stage — lives in test_oracle.py and needs the `flight` extra; this file is
pure stdlib, like the lock itself.
"""

import os
import subprocess
import sys

from repolock import env, lock

DEAD_PID = 999_999          # a pid nothing owns
LIVE_PID = os.getpid()


def dirty(repo, name="scratch.txt"):
    with open(os.path.join(repo, name), "w") as f:
        f.write("uncommitted")


def commit(repo, msg="more"):
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", msg], cwd=repo, check=True)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                          capture_output=True, text=True).stdout.strip()


# --- the zero-import promise ----------------------------------------------------

def test_the_core_imports_no_optional_dependency():
    """Importing the core pulls in nothing optional (README's promise, kept by a test). The core
    and the hook must be usable on a machine that installed nothing but this package — the
    flight-recorder import is lazy, inside main(), and recording-on must not change that."""
    code = (
        "import sys\n"
        "import repolock\n"
        "import repolock.hooks.claude_code\n"
        "hit = {'flight_recorder', 'mcp'} & set(sys.modules)\n"
        "sys.exit(', '.join(hit) if hit else 0)\n"
    )
    clean_env = {k: v for k, v in os.environ.items() if k != "REPOLOCK_FLIGHT"}
    res = subprocess.run([sys.executable, "-c", code], env=clean_env,
                         capture_output=True, text=True)
    assert res.returncode == 0, f"core import pulled in: {res.stderr.strip()}"


# --- recording is on by default, and lands outside every repo -------------------

def test_recording_is_on_unless_switched_off(monkeypatch):
    """The default that #4 paid for: a tape you have to remember to switch on is a tape you do
    not have when it matters."""
    monkeypatch.delenv("REPOLOCK_FLIGHT", raising=False)
    assert env.recording() is True
    for off in ("0", "false", "off", "no", ""):
        monkeypatch.setenv("REPOLOCK_FLIGHT", off)
        assert env.recording() is False, off


def test_recordings_never_land_inside_a_repo(repo, monkeypatch):
    """The recorder must not dirty the tree it is watching. The hook runs with cwd set to the
    session's checkout, so a RELATIVE default would write `flight/` straight into every repo on
    the machine — and show up in `git status` as an edit of its own (SPEC.md §1)."""
    monkeypatch.delenv("REPOLOCK_FLIGHT_DIR", raising=False)
    monkeypatch.chdir(repo)

    assert os.path.isabs(env.flight_dir()), "a relative recording dir writes into the repo"

    lock.acquire(repo, "A", 0, 600, intent="Edit")
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                           capture_output=True, text=True).stdout.strip()
    assert not dirty, f"recording dirtied the working tree it was watching:\n{dirty}"


# --- exclusion ----------------------------------------------------------------

def test_a_second_session_is_held_out(repo):
    assert lock.acquire(repo, "A", LIVE_PID, 600)["status"] == "acquired"
    v = lock.acquire(repo, "B", LIVE_PID, 600)
    assert v["status"] == "held"
    assert v["lock"]["session"] == "A"
    assert v["expires_in"] > 0


def test_acquire_is_reentrant_so_a_session_never_deadlocks_against_itself(repo):
    """Load-bearing, and for a reason that is not obvious from here: THE SUBAGENT.

    A session spawns subagents, and a subagent's tool calls reach the hook with the PARENT's
    session_id — verified against a real headless run, not assumed. So when a parent holds the lock
    and its child writes, this is the code path that runs, and it must renew rather than refuse.

    If a subagent ever got an id of its own, a parent holding the lock would refuse its own child
    while blocking on that child's result: not contention, a deadlock, and one that neither the
    ticket nor lock_wait can break because the holder is the very thing being waited for. That
    would be fatal, and this single line is what stands between us and it.

    The price is stated in SPEC (Non-goals): sharing the id means the lock does NOT arbitrate
    between two PARALLEL subagents of one session. They are one holder, by construction.
    """
    lock.acquire(repo, "A", LIVE_PID, 600)
    assert lock.acquire(repo, "A", LIVE_PID, 600)["status"] == "renewed"


def test_a_lapsed_lease_is_taken_over(repo):
    lock.acquire(repo, "A", LIVE_PID, lease_seconds=1)
    v = lock.acquire(repo, "B", LIVE_PID, 600)              # 1s lease, already gone
    assert v["status"] == "held"                            # ...not yet: still within the second
    cur = lock._load(env.canonical(repo))
    cur.expires_at = env.now() - 1                          # wind the lease past its end
    env.write_record(env.canonical(repo), cur.to_json())
    v = lock.acquire(repo, "B", LIVE_PID, 600)
    assert v["status"] == "acquired"
    assert v["handoff"]["reason"] == "lease lapsed"


def test_a_crashed_holder_does_not_block_forever(repo):
    """The holder's process is gone. Its lease has hours left, and it must NOT hold the repo."""
    lock.acquire(repo, "CRASHED", DEAD_PID, lease_seconds=3600)
    v = lock.acquire(repo, "B", LIVE_PID, 600)
    assert v["status"] == "acquired"
    assert v["handoff"]["reason"] == "holder process is gone"


def test_a_pidless_lock_is_guarded_by_its_lease_alone(repo):
    """Hook-taken locks record pid=0 (the hook process dies instantly). Such a lock must still
    exclude — if pid=0 read as 'dead', every hook-taken lock would be stolen on sight."""
    lock.acquire(repo, "HOOK", pid=0, lease_seconds=600)
    assert lock.acquire(repo, "B", LIVE_PID, 600)["status"] == "held"


# --- the dirty tree, and the idle boundary ------------------------------------

def test_release_refuses_a_dirty_tree(repo):
    lock.acquire(repo, "A", LIVE_PID, 600)
    dirty(repo)
    v = lock.release(repo, "A")
    assert v["status"] == "dirty"
    assert lock.status(repo)["status"] == "locked"          # still held: nothing was handed over


def test_release_is_idempotent(repo):
    lock.acquire(repo, "A", LIVE_PID, 600)
    assert lock.release(repo, "A")["status"] == "released"
    assert lock.release(repo, "A")["status"] == "released"


def test_going_idle_with_a_clean_tree_releases(repo):
    """The agent stopped to ask its human something. Nothing is in flight; holding is
    obstruction."""
    lock.acquire(repo, "A", LIVE_PID, 600)
    assert lock.go_idle(repo, "A")["status"] == "released"
    assert lock.status(repo)["status"] == "unlocked"


def test_going_idle_with_a_dirty_tree_holds_until_the_lease_lapses(repo):
    """The other half of the same question: half-finished edits are in the checkout, so handing
    it over is worse than making the next session wait. Hold, and say so."""
    lock.acquire(repo, "A", LIVE_PID, 600)
    dirty(repo)
    v = lock.go_idle(repo, "A")
    assert v["status"] == "idle_dirty"
    assert v["uncommitted"]
    st = lock.status(repo)
    assert st["status"] == "locked" and st["idle"] is True


def test_the_commit_warning_fires_only_when_work_would_be_stranded(repo):
    lock.acquire(repo, "A", LIVE_PID, lease_seconds=600)
    assert lock.needs_commit_warning(repo, "A") is None          # plenty of lease, clean tree
    dirty(repo)
    assert lock.needs_commit_warning(repo, "A") is None          # dirty, but not near the edge
    cur = lock._load(env.canonical(repo))
    cur.expires_at = env.now() + 30                              # now it's about to lapse
    env.write_record(env.canonical(repo), cur.to_json())
    warn = lock.needs_commit_warning(repo, "A")
    assert warn and "uncommitted" in warn["message"]


# --- the handoff, and the stale reader ----------------------------------------

def test_the_takeover_reports_what_landed_while_the_holder_was_away(repo):
    lock.acquire(repo, "CRASHED", DEAD_PID, 3600)
    dirty(repo, "theirs.txt")
    commit(repo, "landed while they held it")
    v = lock.acquire(repo, "B", LIVE_PID, 600)
    assert v["status"] == "acquired"
    assert len(v["handoff"]["commits_since"]) == 1
    assert "landed while they held it" in v["handoff"]["commits_since"][0]


def test_drift_says_nothing_when_nothing_moved(repo):
    head = env.git_head(repo)
    assert lock.drift(repo, head)["status"] == "current"


def test_drift_sees_a_plain_move(repo):
    head = env.git_head(repo)
    dirty(repo)
    commit(repo, "two")
    v = lock.drift(repo, head)
    assert v["status"] == "moved"
    assert len(v["commits_since"]) == 1


def test_drift_sees_a_rewrite_which_is_the_incident_that_started_this(repo):
    """The founding incident: a session read main, another rebased it, and the first went on
    reasoning about commits that no longer existed. Nothing was corrupted — that is exactly why
    nothing caught it."""
    head = env.git_head(repo)
    dirty(repo)
    commit(repo, "two")
    subprocess.run(["git", "commit", "-q", "--amend", "-m", "two, rewritten"],
                   cwd=repo, check=True)          # the old sha is now unreachable
    v = lock.drift(repo, head)
    # the ORIGINAL head still exists here (amend only rewrote its child), so plant the harder case
    assert v["status"] in ("moved", "rewritten")

    stale = subprocess.run(["git", "rev-parse", "HEAD@{1}"], cwd=repo,
                           capture_output=True, text=True).stdout.strip()
    subprocess.run(["git", "reflog", "expire", "--expire=now", "--all"], cwd=repo, check=True)
    subprocess.run(["git", "gc", "--prune=now", "-q"], cwd=repo, check=True)
    v = lock.drift(repo, stale)
    assert v["status"] == "rewritten"
    assert "REWRITTEN" in v["message"]
