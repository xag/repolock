"""The repo lock's membrane: every effect the lock depends on, and nothing else.

`repolock/lock.py` is pure logic over this module. Everything nondeterministic lives here —
the clock, process liveness, the lockfile on disk, and git — so that the boundary is one
import away and a lock trajectory can be recorded and replayed (see repolock/flight.py).

Keep this module dumb. No policy, no decisions: read the world, write the world, and let
lock.py judge. A function that branches on what it read belongs on the other side.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import time


def now() -> float:
    """Epoch seconds. The lock's only clock — leases are the whole design, so this is hot."""
    return time.time()


def sleep(seconds: float) -> None:
    """Wait. On the boundary because it is nondeterministic, and because a wait that is not on the
    tape is a wait nobody can replay: 'it hung' and 'it waited exactly as told' look identical
    afterwards unless the sleep itself was recorded."""
    time.sleep(max(0.0, seconds))


def pid_alive(pid: int) -> bool:
    """Is that process still running? Answers the crashed-holder case.

    pid <= 0 means "no PID was recorded", and the answer is True — *cannot disprove liveness*.
    That default is load-bearing, not lazy. Locks taken by a harness hook have no usable PID:
    the hook process exits the instant it returns, and its parent may be a short-lived shell
    rather than the agent itself. Recording either would make every hook-taken lock look dead
    within seconds, and a lock that looks dead gets stolen — silently destroying the one
    property this whole module exists to provide. So a PID-less lock degrades to lease-only
    liveness (a crashed holder waits out a short lease), and only a genuinely long-lived
    process supplies a PID it can vouch for.

    On Windows there's no signal-0 trick; ask the OS for the task. On POSIX, os.kill(pid, 0)
    raises ProcessLookupError when it's gone and PermissionError when it exists but isn't ours
    (which still means alive).
    """
    if pid <= 0:
        return True
    if os.name == "nt":
        out = _run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], cwd=None)
        # tasklist prints an "INFO: No tasks..." line rather than failing when there's no match.
        return str(pid) in (out or "")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def lock_dir() -> str:
    """Where lock records live (SPEC.md "Where the lockfile lives"). Deliberately outside the
    repo: a lock must work for a checkout that isn't a git repo yet, and must never show up in
    `git status` as an edit of its own."""
    d = os.getenv("REPOLOCK_DIR") or os.path.join(os.path.expanduser("~"), ".repolock", "locks")
    os.makedirs(d, exist_ok=True)
    return d


def disabled_path() -> str:
    """The panic file itself. Its PRESENCE is the switch; its contents are a note to whoever finds
    it — who turned the lock off, when, and why. A zero-byte file is a mystery in a fortnight."""
    return os.path.join(os.path.dirname(lock_dir()), "DISABLED")


def disabled() -> bool:
    """The panic switch: `~/.repolock/DISABLED` (or REPOLOCK_DISABLED=1) and every adapter becomes
    a no-op that blocks nothing.

    A file rather than only an env var, and checked on every hook call rather than at install time,
    because the sessions you most need to free are the ones already running: a harness snapshots
    its hooks at session start, so editing settings.json cannot reach them and neither can an env
    var they were launched without. Touching a file can. Uninstalling should never require a
    machine-wide restart of the work it is holding up.
    """
    value = os.getenv("REPOLOCK_DISABLED")
    if value is not None:                 # an explicit setting wins, in BOTH directions: a test (or
        return value.strip().lower() in ("1", "true", "on", "yes")   # a session) must be able to
                                          # turn the lock back on without deleting the machine's
                                          # panic file out from under everyone else.
    return os.path.exists(disabled_path())


def recording() -> bool:
    """Is flight recording on? ON by default — `REPOLOCK_FLIGHT=0` (or `false`/`off`/`no`) is the
    only way to switch it off.

    It lives here, in the stdlib-only module, so that a caller can ask the question without
    importing repolock.flight — importing that module is what pulls in flight_recorder, so asking
    must not be the thing that answers.
    """
    value = os.getenv("REPOLOCK_FLIGHT")
    if value is None:
        return True                       # unset is the default, and the default is ON
    return value.strip().lower() not in ("0", "false", "off", "no", "")


def flight_dir() -> str:
    """Where recordings land: absolute, and outside every repo, for the same reason lock_dir is.
    The hook runs with cwd set to the session's own checkout, so a relative default would drop a
    recording directory into every repo on the machine and dirty the tree it is watching."""
    return os.getenv("REPOLOCK_FLIGHT_DIR") or os.path.join(
        os.path.expanduser("~"), ".repolock", "flight")


def canonical(path: str) -> str:
    """The one true spelling of a working copy's path — so `C:\\x` and `c:/x/.` are one lock."""
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def record_path(repo: str) -> str:
    """Lockfile for a canonical repo path. The basename is only there to make the directory
    readable by a human; the hash is what makes it unique."""
    key = hashlib.sha256(repo.encode("utf-8")).hexdigest()[:16]
    name = os.path.basename(repo.rstrip("/\\")) or "repo"
    return os.path.join(lock_dir(), f"{name}-{key}.json")


def read_record(repo: str) -> str | None:
    """The raw lockfile text, or None when the repo is unlocked."""
    try:
        with open(record_path(repo), encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None
    except OSError:
        return None


def write_record(repo: str, text: str) -> str:
    """Write the lockfile atomically — a torn read here would be a lock held by nobody.

    Returns the path written. Not cosmetic: the caller binds it to a local, so the mutation
    lands on the flight-recorder tape and `the_verdict_matches_the_lockfile` can assert that a
    call which *claimed* to grant a lock actually wrote one. A write that returned None would
    be invisible to the oracle.
    """
    path = record_path(repo)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return path


def remove_record(repo: str) -> bool:
    """Drop the lockfile. Absent is success — release is idempotent.

    Returns whether there was actually a lockfile to remove, which is both the evidence the
    oracle reads and the honest answer to "did this release free anything?".
    """
    try:
        os.remove(record_path(repo))
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def _run(args: list[str], cwd: str | None) -> str | None:
    try:
        res = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return res.stdout if res.returncode == 0 else None


def git_head(repo: str) -> str | None:
    """The commit the working copy is on. None when it isn't a git repo — the lock still works,
    it just can't offer a handoff base."""
    out = _run(["git", "rev-parse", "HEAD"], cwd=repo)
    return out.strip() if out else None


def git_dirty(repo: str) -> list[str]:
    """Porcelain lines for the working tree. Empty means clean, which is what release demands."""
    out = _run(["git", "status", "--porcelain"], cwd=repo)
    return [ln for ln in (out or "").splitlines() if ln.strip()]


def file_stat(path: str) -> tuple[int, int] | None:
    """(size, mtime_ns) for one path, or None if it is gone.

    The reason the porcelain alone is not enough: a file that is already ` M` stays ` M` when it is
    edited again, so the status line is identical across a write and the write goes unseen. The
    stat moves every time the bytes do.
    """
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_size, st.st_mtime_ns)


def git_log_between(repo: str, base: str, head: str) -> list[str]:
    """One line per commit in base..head — the "what changed while I was away" of a handoff.

    Returns [] when base is unreachable (the usual reason: the predecessor rebased it out of
    existence, which is precisely the interesting case), so callers must treat [] as "can't
    say", not as "nothing changed" — see lock.py, which cross-checks base != head.
    """
    if not base or not head or base == head:
        return []
    out = _run(["git", "log", "--oneline", "--no-decorate", f"{base}..{head}"], cwd=repo)
    return [ln for ln in (out or "").splitlines() if ln.strip()]


def git_commit_exists(repo: str, sha: str) -> bool:
    """Does this commit still exist in the object graph? A `False` for a base we recorded
    ourselves means history was rewritten under us — the incident class that motivated the
    drift check."""
    if not sha:
        return False
    return _run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=repo) is not None
