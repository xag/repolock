"""The switch, within reach: a system-tray icon that shows and flips the transponder.

A FACE on `transponder.toggle`, never a second authority. The tray owns no state of its own — it
reads the same facts `toggle.state()` reads (the panic file, the wiring, the claims map) and flips
the switch through the same two functions every other caller uses (`toggle.enable` /
`toggle.disable`). An agent calling `lock_disable` over MCP and a human clicking this icon are the
same event to the rest of the system, which is why the icon POLLS: the state it displays can be
changed by hands that are not the user's, and an icon that only knows what it did itself would
show ON over a machine an agent just switched off.

Three colours, because `toggle` says there are three states worth telling apart:

  GREEN   on and wired — guarding.
  GREY    off (the panic file is armed) — every adapter no-ops, by request.
  AMBER   the state toggle.render() calls the worst one: claiming to guard while the hooks are
          missing, or an env override making the file a liar. Never silently green.

Left-click flips it. `pystray` and `Pillow` arrive via the `tray` extra — the core stays stdlib.
Run it headless with `pythonw -m transponder.tray`; a second launch bows out (named mutex) rather
than stacking icons.
"""

from __future__ import annotations

import ctypes
import sys
import threading

import pystray
from PIL import Image, ImageDraw

from transponder import env, toggle

POLL_SECONDS = 4.0
_stop = threading.Event()

GREEN, GREY, AMBER = "#2f9e44", "#868e96", "#e8890c"


def already_running() -> bool:
    """One icon per machine. A stacked pair of tray icons showing different states is exactly the
    kind of ambiguity this whole project exists to remove."""
    try:
        ctypes.windll.kernel32.CreateMutexW(None, False, "transponder-tray")
        return ctypes.windll.kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS
    except AttributeError:                # not Windows — no guard, but no harm either
        return False


def colour(s: dict) -> str:
    on = not s["effective_disabled"]
    honest = s["wired"] and s["env_override"] is None
    if on and honest:
        return GREEN
    if not on:
        return GREY
    return AMBER                          # says ON, but unwired or overridden — the liar state


def image(fill: str) -> Image.Image:
    """A dot inside a ring — a transponder blip. Drawn, not shipped: an asset file is one more
    thing to install and the icon is twelve lines of geometry."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, size - 4, size - 4), outline=fill, width=6)
    d.ellipse((22, 22, size - 22, size - 22), fill=fill)
    return img


def title(s: dict) -> str:
    on = not s["effective_disabled"]
    held = s["held"]
    live = sum(1 for h in held if not h["lapsed"])
    parts = [f"transponder — {'ON' if on else 'OFF'}"]
    if s["env_override"] is not None:
        parts.append(f"env override {s['env_override']!r}")
    elif on and not s["wired"]:
        parts.append("hooks NOT wired")
    parts.append(f"{live} live claim(s)" if held else "map empty")
    if not on and s["reason"]:
        parts.append(s["reason"])
    return ", ".join(parts)[:127]         # Windows truncates tray tooltips at 128 chars


def refresh(icon: pystray.Icon) -> None:
    s = toggle.state()
    icon.icon = image(colour(s))
    icon.title = title(s)


def flip(icon: pystray.Icon, _item) -> None:
    if env.disabled():
        toggle.enable()                   # disarms AND re-wires — on has to mean on
    else:
        toggle.disable(reason="switched off from the tray")
    refresh(icon)


def watch(icon: pystray.Icon) -> None:
    icon.visible = True
    while not _stop.wait(POLL_SECONDS):
        refresh(icon)


def quit_(icon: pystray.Icon, _item) -> None:
    _stop.set()
    icon.stop()


def main() -> int:
    if already_running():
        return 0
    s = toggle.state()
    icon = pystray.Icon(
        "transponder", image(colour(s)), title(s),
        pystray.Menu(
            pystray.MenuItem("Active",
                             flip, checked=lambda _: not toggle.state()["effective_disabled"],
                             default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(lambda _: title(toggle.state()), None, enabled=False),
            pystray.MenuItem("Quit tray (transponder keeps its state)", quit_),
        ))
    icon.run(setup=watch)
    return 0


if __name__ == "__main__":
    sys.exit(main())
