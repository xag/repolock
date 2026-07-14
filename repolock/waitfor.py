"""`python -m repolock.waitfor <repo>` — block until a working copy frees, then exit.

This is the *background* half of waiting, and it exists because of how an agent session is shaped.
A session is turn-based: it cannot be interrupted, and nothing can push into it. The one thing that
CAN wake it is its own harness noticing that a background task it launched has exited. So the way to
let a blocked session go and do something else is to hand it a process it can launch in the
background, which sleeps on the lock and dies the moment the lock frees — at which point the harness
re-invokes the session and tells it. That is a subscription, built out of the only wake channel that
actually exists.

`lock_wait` (the MCP tool) is the other half: it blocks the turn. Use it when there is nothing else
to do. Use this when there is.

The command line is minted BY THE GATE, with a one-time ticket, and the hook allows exactly that
string and nothing else (see hooks/common.py::ticket_for). A blocked session is otherwise unable to
run any shell at all in the repo it is blocked on — including this one, which is the joke that made
the ticket necessary.
"""

from __future__ import annotations

import argparse
import sys

from repolock import lock

MAX_BACKGROUND_WAIT = 4 * 3600      # a background process may wait far longer than an MCP call


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):       # the harness reads UTF-8; Windows writes cp1252,
        try:                                      # so an em-dash reaches the model as U+FFFD
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass

    ap = argparse.ArgumentParser(prog="repolock.waitfor")
    ap.add_argument("repo")
    ap.add_argument("--ticket", default="", help="the one-time ticket the refusal issued")
    ap.add_argument("--timeout", type=float, default=MAX_BACKGROUND_WAIT)
    args = ap.parse_args(argv)

    v = lock.wait_until_free(args.repo, timeout_seconds=args.timeout, poll_seconds=2.0)
    if v["status"] == "free":
        print(f"{args.repo} is FREE — retry what you were blocked on; the hook will take the lock.")
        return 0
    print(v["message"])
    return 1        # still held when the timeout ran out: the session is told, not left guessing


if __name__ == "__main__":
    sys.exit(main())
