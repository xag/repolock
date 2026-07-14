"""The repo lock's trajectory invariants — claims that hold on EVERY lock call (extra `flight`).

Recordings answer "same?"; invariants answer "right?". That distinction earns its keep here more
than anywhere else, because a lock's two catastrophic bugs are both unreproducible by
construction:

  - two sessions holding the same working copy at once
  - a lock nobody can take back after its holder crashed

Neither can be re-staged by hand — each depends on a clock, a PID and an interleaving you do not
control. But both are *claims about a single trajectory*, and a claim can be checked against any
recording, including the one that first caught the bug. A pinned recording of a lock that was
wrongly granted would reproduce faithfully and prove nothing; these condemn it.

RULE, learned the hard way while writing these: an invariant must judge from what the BOUNDARY
said, never from what the code concluded. The first draft of `exclusion_holds` read acquire's own
`prior_live` local — so a bug in the liveness logic would have been read straight out of the
trace, agreed with, and passed. An oracle that trusts the reasoning it exists to audit cannot
condemn that reasoning. So liveness is *recomputed* below from the three recorded facts (the
record on disk, the clock, the PID answer), and the code's opinion is ignored.

Write each claim as the property, not the check; guard result-readers with `t.raised`.
"""

from __future__ import annotations

import json

import flight_recorder as fr

from repolock.lock import Lock


# --- boundary evidence, straight off the tape --------------------------------
# Each of these reads a local that a repolock.env call BOUND — i.e. the boundary's own answer,
# not a conclusion lock.py drew from it.

def _prior(t: fr.Trajectory) -> Lock | None:
    """The lock record on disk when this call decided. `text` is bound by env.read_record."""
    seen = t.trace.values("text")
    if not seen:
        return None
    raw = seen[0].value
    return Lock.from_json(raw) if isinstance(raw, str) and raw else None


def _clock(t: fr.Trajectory) -> float | None:
    """`now` is bound by env.now — the lease clock, as the boundary served it."""
    seen = t.trace.values("now")
    return float(seen[0].value) if seen else None


def _holder_alive(t: fr.Trajectory) -> bool | None:
    """`alive` is bound by env.pid_alive. Deliberately not short-circuited in lock._live,
    so it is always on the tape when there was a holder to ask about."""
    seen = t.trace.values("alive")
    return bool(seen[0].value) if seen else None


def _prior_was_live(t: fr.Trajectory) -> bool | None:
    """Recompute, from evidence alone, whether the previous holder still had a claim.

    A lock binds only while its lease is unexpired AND its holder exists. Returns None when the
    tape cannot say (no prior record, or the call never asked the clock/the OS).
    """
    prior = _prior(t)
    now = _clock(t)
    alive = _holder_alive(t)
    if prior is None or now is None or alive is None:
        return None
    return (now < prior.expires_at) and alive


def _dirty(t: fr.Trajectory) -> list | None:
    """`dirty` is bound by env.git_dirty — the working tree as git reported it."""
    seen = t.trace.values("dirty")
    return list(seen[0].value) if seen else None


def _removed(t: fr.Trajectory) -> bool:
    """`removed` is bound by env.remove_record — True when a lockfile was actually there.

    Read the LOCAL, not `t.trace.calls("remove_record")`: boundary effects are served from the
    feed on replay and never appear as traced calls, so a calls()-based accessor is silently
    always-empty — which is how a vacuously-green invariant suite happens.
    """
    return any(bool(v.value) for v in t.trace.values("removed"))


def _wrote(t: fr.Trajectory) -> bool:
    """`wrote_path` is bound by env.write_record — the path it wrote. Same reasoning."""
    return any(bool(v.value) for v in t.trace.values("wrote_path"))


# --- the claims ---------------------------------------------------------------

@fr.invariant("a lock call never raises — every outcome is a verdict", judges_raise=True)
def lock_calls_return_verdicts(t: fr.Trajectory) -> None:
    # Harness hooks and MCP servers call straight into these. An exception in a hook is a
    # session that cannot edit anything; an exception in acquire is a lock in an unknown state.
    assert not t.raised, f"raised {t.error}"
    assert isinstance(t.result, dict) and t.result.get("status"), "no status in the verdict"


@fr.invariant("two live sessions never hold the same working copy")
def exclusion_holds(t: fr.Trajectory) -> None:
    """THE claim, and the reason any of this exists. Judged against the boundary's account of the
    world — the record that was on disk, the clock, and whether that PID still answered — not
    against acquire's opinion of it."""
    if t.fn != "acquire" or t.raised:
        return
    if (t.result or {}).get("status") not in ("acquired", "renewed"):
        return

    if not _prior_was_live(t):
        return                                  # nobody live was in the way — nothing to violate

    prior = _prior(t)
    mine = (t.kwargs or {}).get("session")
    assert prior.session == mine, (
        f"granted the lock over a LIVE lease held by {prior.session!r} "
        f"(expires {prior.expires_at}, holder alive) while asking as {mine!r}")


def _touches(a: str, b: str) -> bool:
    """Do two resources overlap? **Deliberately a SECOND implementation, and not scope.overlaps.**

    The rule at the top of this file says an invariant judges from the boundary and never from the
    code under test — and nowhere does that bite harder than here. `scope.overlaps` IS the thing most
    likely to be wrong, and it fails *silently*: a broken overlap test does not raise, it hands the
    same region to two agents and tells each of them they are alone. An oracle that asked
    `scope.conflicts` whether `scope` had just conflicted would be fooled by the identical bug and
    would report green — which is not an oracle, it is an echo.

    So the judge computes it again, from the claim records the boundary actually read. Twenty lines
    of duplication, and they are the twenty that would catch it.
    """
    a, b = a.strip().replace("\\", "/"), b.strip().replace("\\", "/")
    if "**" in (a, b) or a == b:
        return True

    def named(r):
        return ":" in r.split("/")[0]

    def pre(r):
        return r[:-3].rstrip("/") + "/" if r.endswith("/**") else (r if r.endswith("/") else None)

    if named(a) or named(b):
        return False                            # named resources: equal-or-disjoint, nothing else
    pa, pb = pre(a), pre(b)
    if pa and pb:
        return pa.startswith(pb) or pb.startswith(pa)
    if pa:
        return b.startswith(pa)
    if pb:
        return a.startswith(pb)
    return False


@fr.invariant("two live claims never overlap — a granted scope was free at the moment it was granted")
def scopes_never_overlap(t: fr.Trajectory) -> None:
    """SPEC-v2's exclusion claim, and the one that matters most — because it is the one that fails
    SILENTLY. A lock that wrongly refuses is annoying and obvious. A scope that is wrongly granted is
    two agents editing one region, each of them certain they are alone, and nobody finds out until
    the diff.

    Judged from the boundary: the claim files that were actually on disk at the moment of the grant,
    and an overlap test of its own (`_touches`) that does not share a line with the code it judges.
    """
    if t.fn != "declare" or t.raised:
        return
    if (t.result or {}).get("status") != "granted":
        return

    granted = ((t.result or {}).get("claim") or {}).get("scope") or []
    mine = (t.kwargs or {}).get("session")
    now = _clock(t)

    # `records` is bound by env.read_claims — the claim files that were ACTUALLY on disk when this
    # grant was decided. Read the local, not the return value of a fresh call: on replay the boundary
    # is served from the feed, so re-asking would be asking the code under test.
    seen = t.trace.values("records")
    for text in (seen[0].value if seen else []) or []:
        try:
            other = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            continue                            # a torn claim is no claim (SPEC §2)
        if other.get("session") == mine or not other.get("scope"):
            continue
        if now is not None and other.get("expires_at", 0) <= now:
            continue                            # lapsed: it binds nobody

        clash = [(m, o) for m in granted for o in other["scope"] if _touches(m, o)]
        assert not clash, (
            f"granted {granted} to {mine!r} while {other['session']!r} held {other['scope']} "
            f"— overlapping at {clash}. Two agents have just been told they own the same region.")


@fr.invariant("a live lease is never stolen — a takeover requires a dead or lapsed holder")
def no_theft_from_the_living(t: fr.Trajectory) -> None:
    """The complement of exclusion, stated from the victim's side. `acquired` over an existing
    record IS a takeover, and it is only ever allowed against a holder the boundary said was
    gone. It must also come with a handoff: taking a lock without telling the next writer what
    it inherited is how a stale reader is born."""
    if t.fn != "acquire" or t.raised:
        return
    if (t.result or {}).get("status") != "acquired":
        return
    prior = _prior(t)
    if not prior:
        return                                  # the repo was simply free

    assert _prior_was_live(t) is False, (
        f"took the lock from session {prior.session} without the boundary establishing "
        "that the holder had lapsed or died")
    assert (t.result or {}).get("handoff"), (
        "took over a lock without a handoff — the next writer inherits a surprise")


@fr.invariant("a dirty working tree is never handed to the next session")
def never_release_a_dirty_tree(t: fr.Trajectory) -> None:
    """"Commit fast", made checkable. `dirty` is git's own answer and remove_record's presence in
    the trace IS the release. Dropping the lock with uncommitted edits in the checkout would hand
    the next writer someone else's half-finished work — strictly worse than making it wait."""
    if t.fn not in ("release", "go_idle") or t.raised:
        return
    if not _removed(t):
        return                                  # nothing was handed over
    if t.fn == "release" and (t.kwargs or {}).get("force"):
        return                                  # force is the deliberate, asked-for override

    dirty = _dirty(t)
    if dirty is None:
        return                                  # released a repo that was never locked
    assert not dirty, f"{t.fn} dropped the lock with {len(dirty)} uncommitted change(s)"


@fr.invariant("a granted lock is on disk, and a released one is not")
def the_verdict_matches_the_lockfile(t: fr.Trajectory) -> None:
    """The lock IS the file. A call that says `acquired` without writing it has granted nothing;
    one that says `released` without removing it has freed nothing. This is also what keeps the
    other claims honest — it is the invariant that proves the mutation accessors can see."""
    if t.raised:
        return
    status = (t.result or {}).get("status")
    if t.fn == "acquire" and status in ("acquired", "renewed"):
        assert _wrote(t), f"reported {status!r} but never wrote the lockfile"
    if t.fn in ("release", "go_idle") and status == "released" and _prior(t):
        assert _removed(t), "reported 'released' but the lockfile is still there"


@fr.invariant("release is idempotent — an unheld lock is released, not an error")
def release_is_idempotent(t: fr.Trajectory) -> None:
    """A hook, a tool call and a crash-recovery path all release without coordinating. If
    releasing twice were an error, the second caller would have to care who went first."""
    if t.fn != "release" or t.raised:
        return
    if _prior(t):
        return
    assert (t.result or {}).get("status") == "released", (
        "releasing an unlocked repo must succeed quietly")
    assert not _removed(t), "removed a lockfile that was not there"


@fr.invariant("a granted lock is anchored to the commit it was taken at")
def a_grant_carries_its_anchor(t: fr.Trajectory) -> None:
    """The handoff is only as good as its anchor: with no base commit the next writer can diff
    nothing, and the stale-reader failure this feature exists to catch goes uncaught. A repo with
    no HEAD (not a git checkout) may have no anchor — nothing else may."""
    if t.fn != "acquire" or t.raised:
        return
    if (t.result or {}).get("status") != "acquired":
        return
    heads = t.trace.values("head") or []
    if not any(h.value for h in heads):
        return                                  # not a git checkout; nothing to anchor to
    assert ((t.result or {}).get("lock") or {}).get("base_commit"), (
        "granted a lock with no base commit to hand off from")


@fr.invariant("a refusal mutates nothing")
def refusals_are_inert(t: fr.Trajectory) -> None:
    """`held`, `denied` and `dirty` are the three ways a lock call says no. A no that still wrote
    to the lockfile would be a lock that changed hands while reporting that it hadn't."""
    if t.raised:
        return
    if (t.result or {}).get("status") not in ("held", "denied", "dirty"):
        return
    assert not _wrote(t) and not _removed(t), (
        f"{t.fn} refused ({t.result.get('status')}) but still touched the lockfile")
