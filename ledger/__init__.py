"""The design ledger, as an importable package: `from ledger import LEDGER`, `python -m ledger.check`.

LEDGER is resolved LAZILY, and that is load-bearing rather than tidy. Composing it imports quern,
reads the pinned packages and parses this package's own source — any of which can fail — and while
this module did it at import time, `python -m ledger.check` could never reach its own error handling:
the package was imported first, so a missing substrate, a syntax error or an unreachable registry all
came out as a traceback with exit 1, indistinguishable from the gate being red. Which is exactly how
a renamed registry read as an unsound release for weeks.

Attribute access still gives the tree, so `from ledger import LEDGER` is unchanged for every caller.
"""

from __future__ import annotations

__all__ = ["LEDGER", "build"]


def __getattr__(name: str):
    if name in __all__:
        from . import ledger as _mod

        return getattr(_mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
