"""The off switch — and the on switch. `python -m transponder.toggle off | on | status`.

The predecessor of this module switched off a LOCK that had taken the machine down four times
(#4, #7, #10, #11). The lock is gone — nothing here refuses a tool call any more — but the switch
stays, because an information layer can still be wrong, noisy, or slow, and off must mean off:

  It must reach a session that is ALREADY RUNNING. A harness snapshots its hooks when it starts, so
  removing them from settings.json does nothing for sessions already up. Only a file on disk,
  checked on every hook call, gets through: `~/.transponder/DISABLED` (env.disabled). Instant.

  The real surface is the MCP tools (`lock_disable` / `lock_enable` in transponder/server.py), so a
  running agent can reach it without a terminal. This CLI is the same two functions for a human.

The two dimensions are deliberately separate, and `status` reports both:

  ARMED    the panic file exists → every adapter no-ops. Reaches running sessions. Instant.
  WIRED    the hooks are in settings.json → new sessions will run the adapter at all.

`off` arms; `on` disarms AND re-wires, because a transponder that says it is on while its hooks are
missing is the worst of the three states — it guards nothing and tells you it is guarding.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from transponder import env
from transponder.hooks import claude_code


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

    override = os.getenv("TRANSPONDER_DISABLED")
    return {
        "armed": os.path.exists(path),    # the panic file: the lock is OFF
        "wired": claude_code.wired(),     # the hooks: new sessions will run it
        "effective_disabled": env.disabled(),
        "env_override": override,         # beats the file, in BOTH directions — see env.disabled()
        "since": note.get("since"),
        "reason": note.get("reason"),
        "held": held_claims(),
        "path": path,
        "settings": claude_code.settings_path(),
    }


def held_claims() -> list[dict]:
    """Every claim on the map, live or lapsed. Turning the switch off does not clear these — it
    makes them inert — and a stale one is the first thing to look at before turning it back on."""
    out = []
    for text in env.read_claims():
        try:
            rec = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            continue
        left = round(rec.get("expires_at", 0) - env.now())
        out.append({"session": rec.get("session"), "scope": rec.get("scope") or [],
                    "intent": rec.get("intent"), "expires_in": left, "lapsed": left <= 0})
    return out


def disable(reason: str = "", clear: bool = False, unwire: bool = False) -> dict:
    """Arm the panic file. Every adapter, in every session, running or not, becomes a no-op.

    `clear` also empties the claims map, so nothing stale greets you when it comes back on.
    `unwire` takes the hooks out of settings.json as well — belt and braces, and only that: the
    switch file alone is already sufficient and is the half that reaches running sessions.
    """
    before = held_claims()
    os.makedirs(os.path.dirname(env.disabled_path()), exist_ok=True)
    with open(env.disabled_path(), "w", encoding="utf-8") as f:
        json.dump({"since": time.strftime("%Y-%m-%d %H:%M:%S"), "reason": reason or "no reason given",
                   "by": f"transponder.toggle (pid {os.getpid()})"}, f, indent=2)

    cleared = []
    if clear:
        for held in before:
            if env.remove_claim(held["session"]):
                cleared.append(held["session"])
    if unwire:
        claude_code.uninstall()
    return {"armed": True, "was_holding": before, "cleared": cleared, "unwired": unwire}


def enable() -> dict:
    """Disarm, and re-wire. Both, because "on" has to mean on.

    Removing the panic file while the hooks are missing from settings.json produces a transponder that
    reports itself enabled and guards precisely nothing — the failure mode that is worse than being
    off, because you would be relying on it.
    """
    stale = [h for h in held_claims() if h["lapsed"]]
    try:
        os.remove(env.disabled_path())
    except FileNotFoundError:
        pass
    claude_code.install()
    return {"armed": False, "wired": claude_code.wired(), "stale_claims": stale,
            "env_override": os.getenv("TRANSPONDER_DISABLED")}


def render(s: dict) -> str:
    on = not s["effective_disabled"]
    out = [f"transponder is {'ON' if on else 'OFF'}",
           f"  switch  : {'ARMED (off)' if s['armed'] else 'not armed'}   {s['path']}",
           f"  hooks   : {'wired' if s['wired'] else 'NOT WIRED'}   {s['settings']}"]
    if s["reason"]:
        out.append(f"  why off : {s['reason']}  (since {s['since']})")
    if s["env_override"] is not None:
        out.append(f"  WARNING : TRANSPONDER_DISABLED={s['env_override']!r} is set and OVERRIDES the "
                   f"file, in both directions.\n            A session started with it will ignore "
                   f"the switch above.")
    if s["armed"] and s["wired"]:
        out.append("  note    : installed but switched off — the hooks run and immediately no-op.")
    if not s["armed"] and not s["wired"]:
        out.append("  WARNING : not armed, but not wired either — nothing is guarding anything.")

    held = s["held"]
    out.append(f"  the map : {len(held)} claim(s)" if held else "  the map : empty")
    for h in held:
        tag = "LAPSED" if h["lapsed"] else f"{h['expires_in']}s left"
        out.append(f"            {(h['session'] or '?')[:8]}  [{tag}]  {', '.join(h['scope'])[:60]}"
                   f"  {(h['intent'] or '')[:36]}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass

    ap = argparse.ArgumentParser(prog="transponder.toggle", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    off = sub.add_parser("off", help="switch it off, everywhere, now")
    off.add_argument("--reason", default="", help="why — written into the switch file")
    off.add_argument("--clear", action="store_true", help="also empty the claims map")
    off.add_argument("--unwire", action="store_true",
                     help="also remove the hooks from settings.json (the panic file alone suffices)")

    sub.add_parser("on", help="turn it back on, and re-wire the hooks")
    sub.add_parser("status", help="is it on? is it wired? what is it holding?")
    args = ap.parse_args(argv)

    if args.cmd == "off":
        v = disable(reason=args.reason, clear=args.clear, unwire=args.unwire)
        print(render(state()))
        if v["cleared"]:
            print(f"\ncleared {len(v['cleared'])} claim(s)")
        print("\nRunning sessions are freed immediately — the switch is read on every hook call.")
        return 0

    if args.cmd == "on":
        v = enable()
        print(render(state()))
        if v["stale_claims"]:
            print(f"\n{len(v['stale_claims'])} lapsed claim(s) are still on the map; they bind "
                  "nobody. `off --clear` drops them outright.")
        return 0

    print(render(state()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
