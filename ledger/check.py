"""Run the ledger's rules, print what is red, and exit non-zero if the gate is.

This is the brake. `nothing-unsound-passes-a-gate` counts the ungrounded params on everything the
release gate admits, so while a debt is carried and undischarged, this exits 1 — and any pipeline
that runs it stops. That is the whole difference between a caveat and a gate: prose cannot fire.

    uv run --group ledger python -m ledger.check

The structural rules (a decision names what it rejected, a hypothesis is falsifiable, a debt states
how it is discharged) are also checked by the test suite, which stays green while they hold. The
GATE is deliberately not a unit test: a red gate does not mean the code is broken, it means an
unsound thing is being carried and has not been paid for. Those are different facts and they should
fail in different places.
"""

from __future__ import annotations

import sys

from bom.tree import run_rules

from ledger import LEDGER


def main() -> int:
    results = run_rules(LEDGER)
    failures = [r for r in results if not r.ok]

    for r in failures:
        print(f"RED  {r.rule} @ {r.node}: {r.detail}")
    if not failures:
        print(f"green — {len(results)} rules, nothing unsound in front of the gate")
        return 0

    gate_red = any(r.rule == "nothing-unsound-passes-a-gate" for r in failures)
    print(f"\n{len(failures)} rule(s) red.")
    if gate_red:
        print("The gate is RED: an unsound thing is on the write path of every session on the\n"
              "machine. Discharge the debt by doing the work its `discharge` names — never by\n"
              "editing the ledger.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
