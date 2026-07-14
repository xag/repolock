"""The trajectory oracle: the invariants, replayed against recordings of real lock calls.

A lock's two catastrophic bugs (two live holders; a lock nobody can reclaim) cannot be staged
by hand: they depend on a clock, a PID and an interleaving you don't control. So we plant each
bug INTO the code, replay the recording through it, and assert the oracle condemns it. A suite
that only ever runs correct code proves the code runs — not that it is right.

Needs the `flight` extra; skipped without it.
"""

import os

import pytest

fr = pytest.importorskip("flight_recorder")

from repolock import flight as lock_flight, invariants as lock_invariants, lock  # noqa: E402

DEAD_PID = 999_999
LIVE_PID = os.getpid()


@pytest.fixture
def recorded(repo, tmp_path, monkeypatch):
    """Record a lifecycle: a grant, a refusal, a release, a crashed holder, a takeover."""
    monkeypatch.setenv("REPOLOCK_FLIGHT", "1")
    flightdir = tmp_path / "flight"
    monkeypatch.setenv("REPOLOCK_FLIGHT_DIR", str(flightdir))
    lock_flight.install()
    try:
        lock.acquire(repo, "A", LIVE_PID, 600, "work")          # 0 acquired
        lock.acquire(repo, "B", LIVE_PID, 600, "work")          # 1 held
        lock.release(repo, "A")                                 # 2 released
        lock.acquire(repo, "CRASHED", DEAD_PID, 3600, "crash")  # 3 acquired
        lock.acquire(repo, "B", LIVE_PID, 600, "takeover")      # 4 takeover
    finally:
        fr.uninstall()
    name = sorted(os.listdir(flightdir))[0]
    return fr.Recording.load(flightdir / name)


def check(handle):
    return handle.check(lock_flight.Adapter(), lock_invariants)


def test_every_claim_holds_on_every_recorded_call(recorded):
    for i, call in enumerate(recorded.calls):
        report = check(recorded.call(i))
        assert report.ok, f"call {i} ({call['fn']}):\n{fr.format_invariant_report(report)}"


def test_the_oracle_condemns_a_stolen_live_lease(recorded):
    """NEGATIVE CONTROL. Plant the classic lock bug — a lease comparison the wrong way round, so
    every holder looks lapsed — and tell the boundary the holder is alive after all. The code
    then grants a lock over a living session. If the oracle stays green here, it is decoration.

    Note it must catch this WITHOUT trusting `prior_live`: that local is exactly what the bug
    corrupts. It recomputes liveness from the record, the clock and the PID answer instead.
    """
    real = lock._lapsed
    lock._lapsed = lambda lk, now: True
    try:
        handle = recorded.call(4)
        handle.effect("pid_alive").result = True
        report = check(handle)
    finally:
        lock._lapsed = real

    assert report.outcome == "violated"
    violated = " ".join(str(v) for v in report.violations)
    assert "two live sessions never hold the same working copy" in violated
    assert "never stolen" in violated


def test_the_oracle_condemns_a_released_dirty_tree(recorded):
    """NEGATIVE CONTROL. Stop enforcing "commit fast" and hand back a checkout with uncommitted
    work in it — the next session would walk straight into someone else's half-finished edits."""
    real = lock._may_release
    lock._may_release = lambda dirty_, force: True
    try:
        handle = recorded.call(2)
        handle.effect("git_dirty").result = [" M scratch.txt"]
        report = check(handle)
    finally:
        lock._may_release = real

    assert report.outcome == "violated"
    assert "dirty working tree is never handed" in " ".join(str(v) for v in report.violations)


# --- SPEC-v2: the scope oracle -------------------------------------------------------------------

@pytest.fixture
def recorded_scopes(repo, tmp_path, monkeypatch):
    """A scope lifecycle: a grant, a disjoint grant, and a grant that must be refused."""
    from repolock import scope

    monkeypatch.setenv("REPOLOCK_FLIGHT", "1")
    flightdir = tmp_path / "flight-scopes"
    monkeypatch.setenv("REPOLOCK_FLIGHT_DIR", str(flightdir))
    lock_flight.install()
    try:
        scope.declare(repo, "A", ["api/**"], "the rate limiter")   # 0 granted
        scope.declare(repo, "B", ["web/**"], "the page")           # 1 granted — disjoint
        scope.declare(repo, "C", ["api/handlers/**"], "nope")      # 2 CONFLICT — inside A's region
    finally:
        fr.uninstall()
    name = sorted(os.listdir(flightdir))[0]
    return fr.Recording.load(flightdir / name)


def test_every_scope_claim_holds_on_every_recorded_call(recorded_scopes):
    for i, call in enumerate(recorded_scopes.calls):
        report = check(recorded_scopes.call(i))
        assert report.ok, f"call {i} ({call['fn']}):\n{fr.format_invariant_report(report)}"


def test_the_oracle_condemns_a_scope_granted_over_a_live_claim(recorded_scopes):
    """NEGATIVE CONTROL, and the most important one in v2.

    Plant the bug that no crash and no test of the code's own output can catch: an overlap test that
    says two regions never touch. Nothing raises. Nothing is refused. Two agents are simply told they
    each own the region — and both go to work in it, each of them certain they are alone. That is
    strictly worse than either being blocked, and it surfaces days later as a diff nobody can explain.

    Built like the stolen-lease control above: keep the RECORDED outcome (a grant), and lie to the
    call about the world instead — the boundary now says another agent already holds the very region
    being handed out. The code, with `overlaps` broken, grants it anyway.

    The oracle must condemn that WITHOUT consulting `scope.overlaps`, because `scope.overlaps` is the
    thing that is broken. It recomputes the overlap itself (`invariants._touches`) from the claim
    records the boundary served. If this test ever goes green, the oracle has stopped being an oracle
    and become an echo.
    """
    import json as _json

    from repolock import scope

    handle = recorded_scopes.call(1)                     # B declares web/** — recorded as GRANTED

    rival = _json.dumps({"repo": "r", "session": "A", "scope": ["web/**"],   # ...but A holds it
                         "intent": "the page, actually", "acquired_at": 0,
                         "renewed_at": 0, "expires_at": 9e18, "lease_seconds": 900})
    handle.effect("read_claims").result = [rival]

    real = scope.overlaps
    scope.overlaps = lambda a, b: False                  # the silent bug
    try:
        report = check(handle)                           # ...so it still grants: outcome unchanged
    finally:
        scope.overlaps = real

    assert report.outcome == "violated", (
        "the oracle did not notice that two agents were handed the same region:\n"
        + fr.format_invariant_report(report))
    violated = " ".join(str(v) for v in report.violations)
    assert "two live claims never overlap" in violated
