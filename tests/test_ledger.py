"""The design ledger is checkable, and checked.

Two different facts, failing in two different places — which is itself the lesson of the entry these
tests now guard.

**The structural rules** (a decision names what it rejected; a hypothesis carries a falsification; a
debt carries a discharge) say the ledger is *well-formed*. They belong here, and they must be green.

**The gate** says something unsound is on the write path and has not been paid for. That is not a
broken test — it is a true statement about the project — and it belongs in `ledger.check`, which
exits 1 while the gate is red so a pipeline stops. Asserting it green here would leave exactly two
ways out the day a debt is taken on: lie in the ledger, or leave the suite permanently red. Both end
with the gate quietly becoming a caveat again, which is the failure this ledger exists to prevent.

Skips where `bom` (private) is not installed — CI and outside contributors lose nothing but this
audit; machines with the substrate run it.
"""

import os
import sys

import pytest

pytest.importorskip("bom")

GATE_RULE = "nothing-unsound-passes-a-gate"


def _results():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from bom.tree import run_rules

    from ledger import LEDGER

    return run_rules(LEDGER)


def test_the_ledger_is_well_formed():
    """No decision without a rejected alternative, no hypothesis without a falsification, no debt
    without a discharge. The vocabulary is pinned from `bom.ledger`, so these are its rules and not
    a local restatement of them — the local restatement is what left a known hole nowhere to live
    but a fake hypothesis."""
    results = _results()
    assert results, "the ledger declares rules but none ran"
    failures = [r for r in results if not r.ok and r.rule != GATE_RULE]
    assert not failures, "\n".join(f"{r.rule} @ {r.node}: {r.detail}" for r in failures)


def test_the_gate_can_actually_stop_a_release():
    """A gate that cannot fail is a comment. This one is red right now — the Cursor adapter carries
    an undischarged debt — and `ledger.check` is what turns that into a non-zero exit."""
    results = _results()
    gate = [r for r in results if r.rule == GATE_RULE]
    assert gate, "the release gate declares no rule — nothing is being braked"

    from ledger.check import main

    assert main() == (0 if all(r.ok for r in gate) else 1)
