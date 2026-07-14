"""The repo lock's flight-recorder boundary declaration and wiring.

The nondeterminism boundary of a lock operation is exactly repolock/env.py: the clock (leases
are the design, so `now` is the hottest effect here), process liveness (`pid_alive` — the
crashed-holder case), the lockfile on disk, and git. There is nothing else: repolock/lock.py is
pure logic over this membrane.

flight-recorder is an OPTIONAL dependency (extra `flight`), and this module is the only place
outside the invariants that imports it — callers must import repolock.flight lazily (see
repolock/hooks/claude_code.py and repolock/server.py), so an install without the extra still runs
a pure-stdlib lock.

**Recording is ON by default**, and switching it on was paid for the hard way. It used to be
opt-in behind REPOLOCK_FLIGHT, on the reasoning that the import is a per-write tax nobody should
pay for nothing. Then the gate starved a two-session fleet (xag/repolock#4) and there was no tape
— the incident had to be reconstructed from the harness's own transcripts, which happened to
exist and were never designed to answer this. An opt-in recorder is off precisely when you need
it, because you do not know in advance which hour is the interesting one. Set REPOLOCK_FLIGHT=0
to turn it off.

Sessions land in REPOLOCK_FLIGHT_DIR, default `~/.repolock/flight` — **absolute, and outside
every repo**, for the same reason the lockfile is (SPEC.md §1): the hook runs with cwd set to the
session's own checkout, so the old relative default (`flight/locks`) would have dropped a
recording directory inside every repo on the machine and shown up in `git status` as an edit of
its own. A recorder that dirties the tree it is watching is not an option.

Why it matters here more than usual: a lock bug is a heisenbug. "Two sessions held it at once"
and "it wouldn't let go after a crash" are both unreproducible by construction — they depend on
a clock, a PID, and an interleaving you cannot re-stage by hand. Recording the boundary is the
only way to ever debug one by reading a variable instead of re-deriving what must have happened.
"""

from __future__ import annotations

import inspect
import os

import flight_recorder as fr

from repolock import env, lock, scope


# `recording()` and `flight_dir()` live in env.py, NOT here: a caller has to be able to ask "is
# recording on?" WITHOUT importing this module, because importing this module is what pulls in
# flight_recorder. Asking the question must not be the thing that answers it.

BOUNDARY = fr.Boundary(
    effects=[
        (env, [
            "now",                 # leases: the clock is not incidental here, it is the design
            "pid_alive",           # crashed holder
            "lock_dir", "canonical", "record_path",
            "read_record", "write_record", "remove_record",
            "git_head", "git_dirty", "git_log_between", "git_commit_exists",
            "file_stat",           # the re-edit of an already-dirty file: its status line does not
                                   # move but its bytes do, so the fingerprint is blind without it
            # SPEC-v2's claim store. The trial's evidence IS the tape (§11a), so every effect a scope
            # decision rests on has to be on it — otherwise "did agents contain themselves?" is
            # answered by asking them, which is not an answer.
            "claims_dir", "claim_path", "read_claims", "write_claim", "remove_claim",
            "git_paths_between",   # the commit that swept another agent's work (§1a)
            "git_tracked_dirs",    # what is free, for the conflict message
        ]),
    ],
    constants=[(lock, "DEFAULT_LEASE_SECONDS"),
               (lock, "MAX_LEASE_SECONDS"),
               (lock, "WARN_BEFORE_SECONDS")],
)


def install() -> None:
    """On by default; REPOLOCK_FLIGHT=0 turns it off."""
    fr.install(BOUNDARY, lock,
               directory=env.flight_dir(),
               enabled=env.recording(),
               tool_skip_params=())
    _also_record(scope)


def _also_record(module) -> None:
    """Record a SECOND tools module — because repolock's operations live in two, and only one of
    them can be `fr.install`'s `tools_module`.

    `fr.install` wraps exactly one module and is idempotent by design (`_arm` returns False on the
    second call), so calling it again for `scope` is a silent no-op: the functions go unwrapped, and
    every `declare` / `extend` / `release` is absent from the tape. That is not a cosmetic gap here.
    **SPEC-v2 §11a says the trial's evidence is the tape**, and the trial's whole question is whether
    agents contain their work — so an unrecorded `declare` is a trial that cannot be judged, and the
    only fallback would be to ask the agents how they think they did.

    So the recorder's OWN wrapper is reused rather than a hand-rolled one. That is deliberate: this
    library's most expensive lesson is that an uninstrumented fake is relocated guessing (#10), and
    the rule bites hardest when the thing you are faking is the instrument.

    It reaches into `flight_recorder.record` privates, which is a wart, and the honest fix is
    upstream — `fr.install` should take several tool modules. Filed as xag/flight-recorder#28.
    """
    from flight_recorder import record as _rec

    if _rec.hook.mode != "record":
        return                        # recording is off, or the install rolled back: wrap nothing
    for name, fn in list(vars(module).items()):
        if (callable(fn) and not name.startswith("_") and not inspect.isclass(fn)
                and getattr(fn, "__module__", "") == module.__name__):
            _rec._patch(module, name, _rec._wrap_tool(fn, ()))


class Adapter(fr.ReplayAdapter):
    boundary = BOUNDARY
    trace_root = os.path.dirname(os.path.abspath(__file__))
    skip_files = frozenset({"flight.py", "replay.py"})

    def resolve(self, fn_name: str, feed: fr.Feed):
        module = lock if hasattr(lock, fn_name) else scope     # two tool modules, one tape
        fn = getattr(module, fn_name)
        return getattr(fn, "__flight_wrapped__", fn)
