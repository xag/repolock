"""The off switch — and the on switch. `python -m repolock.toggle off | on | status`.

A lock that guards every checkout on the machine is a lock that can take the machine down, and this
one has now done so four times (#4, #7, #10, #11). The thing you need at that moment is not a README
section about editing settings.json; it is one command, and it has to work *from inside the session
that is currently locked out*. Two consequences shape this module:

  It must reach a session that is ALREADY RUNNING. A harness snapshots its hooks when it starts, so
  removing them from settings.json does nothing for the sessions currently wedged — which are
  precisely the ones you are trying to free. Only a file on disk, checked on every hook call, gets
  through: `~/.repolock/DISABLED` (env.disabled). That is the switch, and it is instant.

  It must not require a shell. When the lock misfires it refuses your shell — that is what a
  refusal IS — so an off switch spelled as a shell command is unreachable exactly when it is needed.
  So the real surface is the MCP tools (`lock_disable` / `lock_enable` in repolock/server.py), which
  the hook does not gate. This CLI is the same two functions for a human at a terminal.

The two dimensions are deliberately separate, and `status` reports both:

  ARMED    the panic file exists → every adapter no-ops. Reaches running sessions. Instant.
  WIRED    the hooks are in settings.json → new sessions will run the adapter at all.

`off` arms; `on` disarms AND re-wires, because a repolock that says it is on while its hooks are
missing is the worst of the three states — it guards nothing and tells you it is guarding.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from repolock import env, lock
from repolock.hooks import claude_code


def state() -> dict:
    """Everything you need to answer "is it on, and is it holding anything?" — and nothing else."""
    path = env.disabled_path()
    note: dict = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                note = json.loads(f.read() or "{}")
        except (OSError, json.JSONDecodeError):
            note = {}                     # a hand-touched file is still a valid switch

    override = os.getenv("REPOLOCK_DISABLED")
    return {
        "armed": os.path.exists(path),    # the panic file: the lock is OFF
        "wired": claude_code.wired(),     # the hooks: new sessions will run it
        "effective_disabled": env.disabled(),
        "env_override": override,         # beats the file, in BOTH directions — see env.disabled()
        "since": note.get("since"),
        "reason": note.get("reason"),
        "held": held_locks(),
        "path": path,
        "settings": claude_code.settings_path(),
    }


def held_locks() -> list[dict]:
    """Every lock currently on disk. Turning the switch off does not release these — it makes them
    inert — and a stale one is the first thing you want to see when you come to turn it back on."""
    out = []
    try:
        names = os.listdir(env.lock_dir())
    except OSError:
        return out
    for name in sorted(names):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(env.lock_dir(), name), encoding="utf-8") as f:
                rec = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        left = round(rec.get("expires_at", 0) - env.now())
        out.append({"repo": rec.get("repo"), "session": rec.get("session"),
                    "intent": rec.get("intent"), "expires_in": left,
                    "idle": rec.get("idle_since") is not None, "lapsed": left <= 0})
    return out


def disable(reason: str = "", clear: bool = False, unwire: bool = False) -> dict:
    """Arm the panic file. Every adapter, in every session, running or not, becomes a no-op.

    `clear` also drops the lockfiles, so nothing is held when you turn it back on. `unwire` takes
    the hooks out of settings.json as well — belt and braces, and only that: the panic file alone
    is already sufficient and is the half that reaches running sessions.
    """
    before = held_locks()
    os.makedirs(os.path.dirname(env.disabled_path()), exist_ok=True)
    with open(env.disabled_path(), "w", encoding="utf-8") as f:
        json.dump({"since": time.strftime("%Y-%m-%d %H:%M:%S"), "reason": reason or "no reason given",
                   "by": f"repolock.toggle (pid {os.getpid()})"}, f, indent=2)

    cleared = []
    if clear:
        for held in before:
            if lock.release(held["repo"], held["session"], force=True).get("freed"):
                cleared.append(held["repo"])
    if unwire:
        claude_code.uninstall()
    return {"armed": True, "was_holding": before, "cleared": cleared, "unwired": unwire}


def enable() -> dict:
    """Disarm, and re-wire. Both, because "on" has to mean on.

    Removing the panic file while the hooks are missing from settings.json produces a repolock that
    reports itself enabled and guards precisely nothing — the failure mode that is worse than being
    off, because you would be relying on it.
    """
    stale = [h for h in held_locks() if h["lapsed"]]
    try:
        os.remove(env.disabled_path())
    except FileNotFoundError:
        pass
    claude_code.install()
    return {"armed": False, "wired": claude_code.wired(), "stale_locks": stale,
            "env_override": os.getenv("REPOLOCK_DISABLED")}


def render(s: dict) -> str:
    on = not s["effective_disabled"]
    out = [f"repo-lock is {'ON' if on else 'OFF'}",
           f"  switch  : {'ARMED (off)' if s['armed'] else 'not armed'}   {s['path']}",
           f"  hooks   : {'wired' if s['wired'] else 'NOT WIRED'}   {s['settings']}"]
    if s["reason"]:
        out.append(f"  why off : {s['reason']}  (since {s['since']})")
    if s["env_override"] is not None:
        out.append(f"  WARNING : REPOLOCK_DISABLED={s['env_override']!r} is set and OVERRIDES the "
                   f"file, in both directions.\n            A session started with it will ignore "
                   f"the switch above.")
    if s["armed"] and s["wired"]:
        out.append("  note    : installed but switched off — the hooks run and immediately no-op.")
    if not s["armed"] and not s["wired"]:
        out.append("  WARNING : not armed, but not wired either — nothing is guarding anything.")

    held = s["held"]
    out.append(f"  holding : {len(held)} lock(s)" if held else "  holding : nothing")
    for h in held:
        tag = "LAPSED" if h["lapsed"] else (f"idle, {h['expires_in']}s left" if h["idle"]
                                            else f"{h['expires_in']}s left")
        out.append(f"            {h['repo']}  [{tag}]  session {(h['session'] or '?')[:8]}"
                   f"  {(h['intent'] or '')[:40]}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass

    ap = argparse.ArgumentParser(prog="repolock.toggle", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    off = sub.add_parser("off", help="turn the lock off, everywhere, now")
    off.add_argument("--reason", default="", help="why — written into the switch file")
    off.add_argument("--clear", action="store_true", help="also drop every lock currently held")
    off.add_argument("--unwire", action="store_true",
                     help="also remove the hooks from settings.json (the panic file alone suffices)")

    sub.add_parser("on", help="turn it back on, and re-wire the hooks")
    sub.add_parser("status", help="is it on? is it wired? what is it holding?")
    args = ap.parse_args(argv)

    if args.cmd == "off":
        v = disable(reason=args.reason, clear=args.clear, unwire=args.unwire)
        print(render(state()))
        if v["cleared"]:
            print(f"\ncleared {len(v['cleared'])} lock(s): {', '.join(v['cleared'])}")
        print("\nRunning sessions are freed immediately — the switch is read on every hook call.")
        return 0

    if args.cmd == "on":
        v = enable()
        print(render(state()))
        if v["stale_locks"]:
            print(f"\n{len(v['stale_locks'])} lapsed lock(s) are still on disk; the next write takes "
                  "them over with a handoff. `off --clear` drops them outright.")
        return 0

    print(render(state()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
