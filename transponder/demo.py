"""Prove the transponder works, on this machine, with two real sessions — `python -m transponder.demo`.

This exists because the library's central claim cannot be checked from inside one session. An agent
cannot see another agent; that is the whole premise. So every internal signal can look healthy while
the thing does nothing — which is exactly what happened: for the whole life of v2 the courier wrote
its notes to a debug log, the map was accurate, the witness was watching, the tapes were faithful,
and no agent was ever told anything. It took two sessions deliberately colliding to notice.

So this is a two-party test, and it needs a human for thirty seconds:

    this process   holds a region of a throwaway checkout and writes to it, as a second agent would
    you           open ANOTHER session and edit the same file
    the transponder  should introduce the checkout, and then name the violation after the write

It never touches a real repo: the checkout is a fresh temp directory unless you pass --repo.

THE ONE GOTCHA, and it will cost you the run if you miss it: a harness snapshots its hooks when the
session STARTS. A session that was already open when the hooks were last wired is running the old
ones. Start the other session fresh.
"""

from __future__ import annotations

import argparse
import collections
import os
import subprocess
import sys
import tempfile
import time

from transponder import env, scope
from transponder.hooks import common

TICK_SECONDS = 10
HEADER = "shared.txt — held by the transponder demo\n"


def _git(repo: str, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)


def make_checkout(path: str) -> str:
    """A real git repo, because the witness reads `git status --porcelain` and a directory that is
    not a checkout is invisible to it — the demo would 'pass' by saying nothing at all."""
    os.makedirs(path, exist_ok=True)
    _git(path, "init", "-q")
    with open(os.path.join(path, "shared.txt"), "w", encoding="utf-8") as f:
        f.write(HEADER)
    _git(path, "add", "-A")
    _git(path, "-c", "user.email=demo@demo", "-c", "user.name=demo", "commit", "-qm", "demo")
    return path


def preflight() -> list[str]:
    """Say what is off BEFORE the human goes and opens a session for nothing."""
    from transponder import toggle

    out = []
    st = toggle.state()
    if env.disabled():
        out.append("transponder is switched OFF — `python -m transponder.toggle on` first.")
    if not st.get("wired"):
        out.append("the hooks are NOT wired into settings.json — `python -m transponder.toggle on`.")
    return out


def report(path: str, mine: list[str], mail: list[str]) -> None:
    """What this side saw. The point of the demo is to compare it with what the other session was
    TOLD: a violation the witness recorded but nobody delivered is the exact failure this library
    shipped with, and only two accounts of one event can catch it.

    The FILE alone is not enough evidence, and the first run of this demo proved it: the other
    session wrote, was told, and politely reverted — so the file came back clean and this report
    announced that nothing had happened. A collision that was witnessed twice, reported to both
    parties, and recorded on the tape, summarised as silence. So the mail is the primary evidence
    now and the file is corroboration: mail survives an undo, because being written into is a fact
    about the past and the file only ever shows the present.
    """
    try:
        with open(path, encoding="utf-8") as f:
            now = f.readlines()
    except OSError as e:
        print(f"\n  could not re-read the file: {e}")
        now = []

    left = collections.Counter(now) - collections.Counter(mine)
    lost = collections.Counter(mine) - collections.Counter(now)

    print("\n" + "=" * 72)
    if mail:
        print(f"  THE TRANSPONDER TOLD ME — {len(mail)} note(s) delivered to this agent:\n")
        for note in mail:
            for line in note.splitlines():
                print(f"    {line}")
            print()
        print("  That is the victim's half of the protocol, and it is the half that was missing:")
        print("  the agent whose region was written is now told, on its own next call.")
        if not left:
            print("\n  Note the file itself is clean — they undid it. Without this mail the demo")
            print("  would be reporting that nothing happened.")
    if left:
        print(f"  ANOTHER AGENT WROTE IN MY REGION — {sum(left.values())} line(s) I did not write:")
        for line in list(left)[:8]:
            print(f"    {line.rstrip()}")
        print("\n  That session should have been told, on its own screen, in this order:")
        print("    1. THIS CHECKOUT IS SHARED …  (before it wrote — once per session)")
        print("    2. SCOPE VIOLATION — you just wrote inside another agent's reserved region")
        print("       (after the write landed, with the three-step remedy)")
        print("\n  If it saw NEITHER, the courier is not reaching agents — which is the failure")
        print("  this demo exists to catch, and it looks identical to everything working.")
    elif not mail:
        print("  Nothing reached me: no mail, and no lines in the file that I did not write.")
        print("  Either no other session tried, or it was pointed at a different path — or the")
        print("  courier is silent again. Those look the same from here, which is the point:")
        print("  check the tape (python -m transponder.replay) before concluding anything.")
    if lost:
        print(f"\n  {sum(lost.values())} line(s) of MINE are gone — the other write destroyed them.")
        print("  Worth sitting with: the report is not a recovery. Uncommitted bytes do not come")
        print("  back, which is why the map matters more than the alarm.")
    print("=" * 72)


def _utf8() -> None:
    """Same reason the hook does it (#10's tape): Python on Windows writes the ANSI code page, and
    an em-dash arriving as U+FFFD is a message half-delivered. A demo is read by a human or it is
    nothing."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(prog="python -m transponder.demo", description=__doc__.split("\n")[0])
    ap.add_argument("--minutes", type=float, default=5.0, help="how long to hold the file (default 5)")
    ap.add_argument("--repo", default=None, help="use this checkout instead of a temp one")
    args = ap.parse_args(argv)

    for problem in preflight():
        print(f"WARNING: {problem}")

    repo = make_checkout(args.repo or tempfile.mkdtemp(prefix="transponder-demo-"))
    path = os.path.join(repo, "shared.txt")
    session = f"demo-holder-{os.getpid()}"
    intent = "the demo: appending a tick every 10s, testing whether the other session is told"

    v = scope.declare(repo, session, [path], intent)
    if v["status"] != "granted":
        print(f"could not take the region: {v}")
        return 1

    ticks = max(1, int(args.minutes * 60 / TICK_SECONDS))
    print("=" * 72)
    print("  EDIT THIS FILE FROM ANOTHER SESSION:\n")
    print(f"    {path}\n")
    print(f"  I hold it as agent {session} for the next {args.minutes:g} minute(s).")
    print("  Open a NEW session (hooks are snapshotted at session start — an already-open one")
    print("  is running the old ones), point it at that path, and edit it. Your write will NOT")
    print("  be blocked; nothing here ever blocks. Watch what that session is told.")
    print("=" * 72 + "\n")

    mine, mail = [HEADER], []
    try:
        for i in range(1, ticks + 1):
            line = f"tick {i:03d}  {time.strftime('%H:%M:%S')}\n"
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
            mine.append(line)
            scope.renew(session)          # no hook fires for this process; the claim would lapse
            # Drain our own inbox, exactly as an agent's hook does on its next tool call. This is
            # the demo being a real participant rather than a prop: it is the victim, so it should
            # receive what a victim receives — live, while you are watching.
            if fresh := common.collect(session, repo):
                mail += fresh
                print(" " * 30, end="\r")
                for note in fresh:
                    print("\n  >>> " + note.splitlines()[0])
                print(f"  >>> ({len(fresh)} note(s) — full text at the end)\n")
            print(f"  held {i:3d}/{ticks}", end="\r", flush=True)
            if i < ticks:
                time.sleep(TICK_SECONDS)
    except KeyboardInterrupt:
        print("\n  stopped early.")
    finally:
        scope.release(session, anchor=repo)
        report(path, mine, mail)
        print(f"\n  released. the checkout is still there if you want to look: {repo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
