"""Run the ledger's rules, report what is red, and say what to do about it.

    uv run python -m ledger.check            the gate
    uv run python -m ledger.check --brief    one line per entry, fattest first

THREE OUTCOMES, THREE EXIT CODES, and the distinction is the point:

    0   green — every rule holds and the roll is written
    1   RED — something unsound is in front of the gate, or an entry left the
        record without saying so. The ledger is fine; what it records is not
    2   CANNOT CHECK — quern is missing, the file does not parse, the pinned
        package is not in the registry. Nothing was judged

2 used to be 1, and that cost an afternoon: CI failed with `ledger@0.5.0
(pinned) is not in the library` and read as a red gate for weeks, when the truth
was that the workflow cloned a registry that had been renamed. "Unsound" and
"unchecked" are different facts and a pipeline should be able to tell them apart
without reading a traceback.

`quern` is the substrate this is authored against. It is deliberately NOT a
dependency of this package — transponder is public and its adopters must not
need it — so the check runs where someone has put it on the path. The test
suite's structural rules `importorskip` past its absence; this module does not,
because a check that silently passes when it could not run is worse than one
that is missing.

The GATE is deliberately not a unit test: a red gate does not mean the code is
broken, it means an unsound thing is being carried and has not been paid for.
Those are different facts and they should fail in different places.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_ROLL = "ledger/roll.json"

# WHICH revision's roll to compare against, and it is not a detail. Locally the
# working tree holds the edit under judgement and HEAD is the last good state, so
# HEAD is right. In CI the commit under judgement IS HEAD - and carries the roll
# written beside it - so comparing against HEAD compares the tree with itself and
# passes whatever it is handed. CI names the base it is diffing from instead.
_REV = os.environ.get("LEDGER_ROLL_REV", "HEAD")

# Warn at this fraction of an entry's word budget. Not a rule — a rule that fires
# late is a rewrite under time pressure, and every entry that went over today went
# over while being written, not while being planned.
_WARN_AT = 0.85


class CannotCheck(Exception):
    """The check could not run. NOT the same as the gate being red."""


def _load():
    """Import the substrate and the ledger, turning every "could not run" into one
    exception with an actionable sentence attached."""
    try:
        from quern.roll import audit, write
        from quern.tree import run_rules, said_words
    except ModuleNotFoundError as e:
        raise CannotCheck(
            f"quern is not on this interpreter's path ({e.name}).\n"
            f"  The ledger is authored against it. Run through the project venv, or:\n"
            f"      uv pip install -e ../quern") from e
    try:
        from ledger import LEDGER
    except SyntaxError as e:
        raise CannotCheck(
            f"the ledger does not parse — {e.filename}:{e.lineno}: {e.msg}\n"
            f"  It is Python holding data, so an edit can break it syntactically. "
            f"Nothing was judged.") from e
    except ValueError as e:
        raise CannotCheck(
            f"the ledger could not be composed — {e}\n"
            f"  If that names a PINNED package, the registry does not carry it: check "
            f"QUERN_REGISTRY\n"
            f"  (default ../quern-registry, which is a checkout of xag/fleet-registry).") from e
    return LEDGER, run_rules, said_words, audit, write


def _limit(tree) -> int | None:
    """The word budget, read off the rules rather than restated here — a warning
    that disagrees with the rule it is warning about is worse than no warning."""
    for rule in tree.rules:
        if m := re.search(r"said_words\(self\)\s*<=\s*(\d+)", getattr(rule, "expr", "") or ""):
            return int(m.group(1))
    return None


def _crowded(tree, said_words, limit: int) -> list[tuple[str, int]]:
    """Entries close enough to the budget that the next edit will breach it."""
    out = []
    for path, _ in tree.walk(""):
        if "/" in path:
            continue
        words = said_words(tree, path)
        if words >= limit * _WARN_AT:
            out.append((path, words))
    return sorted(out, key=lambda e: -e[1])


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        LEDGER, run_rules, said_words, audit, write = _load()
    except CannotCheck as e:
        print(f"CANNOT CHECK — {e}")
        print("\nNothing was judged, so this is not a verdict on the ledger.")
        return 2

    if "--brief" in argv:
        from quern.brief import brief

        print(brief(LEDGER, fat=True))
        return 0

    results = run_rules(LEDGER)
    failures = [r for r in results if not r.ok]
    # A tombstone with no `was` excuses nothing - the right way round, because
    # forgetting it leaves the check red, never green.
    excused = {n.payload["was"] for _, n in LEDGER.walk("")
               if n.kind == "tombstone" and n.payload.get("was")}
    removals, looked = audit(LEDGER, _ROOT, _ROLL, _REV, excused)

    for r in failures:
        print(f"RED  {r.rule} @ {r.node}: {r.detail}")
    for line in removals:
        print(f"GONE {line}")
    if not looked:
        print(f"note: no roll at {_REV} - nothing was compared, so nothing was")
        print("      checked for removal. Honest on the first run of this check,")
        print("      and a problem on any other.")

    # The roll is written on a red run too, and for this ledger that is the whole
    # point: it ships red by design while a debt is carried, so gating the roll on
    # green would deny it removal protection permanently. A red rule is a debt; the
    # roll records what EXISTS. Only an unexplained removal makes it unsafe to
    # rewrite, because rewriting it then would launder what the check just caught.
    if not removals:
        write(LEDGER, _ROOT / _ROLL)

    if (limit := _limit(LEDGER)) and (crowded := _crowded(LEDGER, said_words, limit)):
        print()
        print(f"CROWDED — within {int(_WARN_AT * 100)}% of the {limit}-word budget. Tighten "
              f"before adding, not after:")
        for path, words in crowded:
            print(f"  {words:4d}/{limit}  {path}")
        print("  `--brief` sorts every entry by weight; the first line is the first to cut.")

    if not failures and not removals:
        print(f"\ngreen - {len(results)} rules, nothing unsound in front of the "
              "gate; roll written")
        return 0

    gate_red = any(r.rule == "nothing-unsound-passes-a-gate" for r in failures)
    print()
    if failures:
        print(f"{len(failures)} rule(s) red.")
    if gate_red:
        print("The gate is RED: an unsound thing is on the write path of every")
        print("session on the machine. Discharge the debt by doing the work its")
        print("`discharge` names - never by editing the ledger.")
    if removals:
        print(f"{len(removals)} entr(y/ies) left the record without saying so.")
        print("Reversed is superseded and the node STAYS; paid is discharged and")
        print("the node STAYS; only an entry that was never valid is retracted,")
        print("with a tombstone naming it. Each GONE line above carries the digest")
        print("to paste if only the wording moved: meta['amended'] = '<digest> <why>'.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
