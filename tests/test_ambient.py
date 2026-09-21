#!/usr/bin/env python3
"""Regression tests for the ambient frame and the power lamp. Host-only, no board.

    python3 tests/test_ambient.py

Two things about the resting panel that nothing else in the tree can see, and
both of which are only ever caught by a human looking at the wall:

  * the power lamp is drawn on EVERY ambient frame - clock or no clock. It is
    the one thing in AMBIENT that is not best-effort, and it is what makes "the
    unit has power" answerable without looking at the router.
  * nothing else draws in that corner. The lamp is a single pixel at (0,0) and
    the clock, the breathing status pixel and the big HELLO banner are all
    centred, so the two never meet - but that is a claim about three separate
    drawing routines, and it holds only as long as someone keeps it true. A
    collision erases the lamp (if the lamp is drawn first) or clips a glyph edge
    (if it is drawn last), and either way it is found by eye, at night, months
    later.

Also checks the frame survives its own clear(): the lamp is drawn AFTER
display.clear(), so a reordering that put it before would silently produce a
black panel with no other symptom.

`galactic` and `picographics` are frozen into the board's firmware, so they are
faked here - the point is the drawing ORDER and the geometry, not the hardware.
"""

import os
import sys
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

# MicroPython's ticks_ms has no host equivalent. MUST precede `import ambient`,
# which does `import time` and then calls it in _draw_clock.
_TICKS = [0]
time.ticks_ms = lambda: _TICKS[0]
time.ticks_diff = lambda now, then: now - then

WIDTH = 53
HEIGHT = 11
WHITE = (255, 255, 255)


class FakeGraphics:
    """A PicoGraphics that remembers what was drawn on it."""

    def __init__(self, display=None):
        self.pen = None
        self.pixels = []  # (x, y, rgb)
        self.texts = []  # (s, x, y, rgb)

    def create_pen(self, r, g, b):
        return (r, g, b)

    def set_pen(self, pen):
        self.pen = pen

    def clear(self):
        self.pixels = []
        self.texts = []

    def pixel(self, x, y):
        self.pixels.append((x, y, self.pen))

    def rectangle(self, x, y, w, h):
        for dx in range(w):
            for dy in range(h):
                self.pixels.append((x + dx, y + dy, self.pen))

    def text(self, s, x, y, scale=1):
        self.texts.append((s, x, y, self.pen))

    def measure_text(self, s, scale=1):
        return 6 * len(s) * scale


class FakeUnicorn:
    WIDTH = WIDTH
    HEIGHT = HEIGHT
    SWITCH_A = "A"
    SWITCH_B = "B"
    SWITCH_C = "C"
    SWITCH_D = "D"
    SWITCH_SLEEP = "SLEEP"
    SWITCH_VOLUME_UP = "VOL_UP"
    SWITCH_VOLUME_DOWN = "VOL_DOWN"
    SWITCH_BRIGHTNESS_UP = "BRIGHT_UP"
    SWITCH_BRIGHTNESS_DOWN = "BRIGHT_DOWN"

    def __init__(self):
        self.brightness = None
        self.frames = 0

    def set_brightness(self, value):
        self.brightness = value

    def light(self):
        return 4095  # a lit room: routine.py's dark-room rule is not under test

    def update(self, graphics):
        self.frames += 1


class FakeConfig:
    BRIGHTNESS_AMBIENT = 0.10
    BRIGHTNESS_MAX = 0.65
    LIGHT_DARK = 40
    LIGHT_DIM = 400
    UTC_OFFSET_S = 0


_galactic = types.ModuleType("galactic")
_galactic.GalacticUnicorn = FakeUnicorn
sys.modules["galactic"] = _galactic

_picographics = types.ModuleType("picographics")
_picographics.DISPLAY_GALACTIC_UNICORN = 0
_picographics.PicoGraphics = FakeGraphics
sys.modules["picographics"] = _picographics

import ambient
import display


def report(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL':<4} {name:<52} {detail}")
    return ok


def fresh(ntp_ok):
    """A Display + Ambient pair wired to the fakes."""
    d = display.Display(FakeConfig())
    a = ambient.Ambient(d, FakeConfig())
    a.ntp_ok = ntp_ok
    return d, a


def lamp_pixels(graphics):
    return [p for p in graphics.pixels if (p[0], p[1]) == (0, 0)]


def case_lamp_is_in_the_corner():
    """The lamp is one white pixel, in the corner the banner's glyphs miss."""

    def body():
        ok = (
            display.POWER_PIXEL == (0, 0)
            and display.POWER_RGB == display.WHITE == WHITE
        )
        return report(
            "POWER_PIXEL is one white pixel at (0, 0)",
            ok,
            f"pixel={display.POWER_PIXEL} rgb={display.POWER_RGB}",
        )

    return body()


def case_lamp_lit_with_clock():
    """A synced clock does not take the lamp's corner, and vice versa."""

    def body():
        d, a = fresh(ntp_ok=True)
        a.draw(0)
        g = d.graphics
        lamp = lamp_pixels(g)
        ok = lamp == [(0, 0, WHITE)]
        # And the clock is nowhere near it: real glyphs are 6 px wide by 8 tall
        # drawn from (x, y), so the box below is what must stay off the corner.
        clock = g.texts[0] if g.texts else (None, None, None, None)
        cleared = clock[1] is not None and clock[1] > 0 and clock[2] > 0
        return report(
            "with the clock, the lamp is lit and alone at (0, 0)",
            ok and cleared,
            f"lamp={lamp} clock_xy=({clock[1]}, {clock[2]})",
        )

    return body()


def case_lamp_lit_without_clock():
    """No NTP is the ambiguous state; the lamp is what makes it unambiguous."""

    def body():
        d, a = fresh(ntp_ok=False)
        a.draw(1000)
        g = d.graphics
        lamp = lamp_pixels(g)
        breath = [p for p in g.pixels if (p[0], p[1]) == (WIDTH // 2, HEIGHT // 2)]
        return report(
            "without NTP, the lamp is lit beside the breathing pixel",
            lamp == [(0, 0, WHITE)] and breath != [],
            f"lamp={lamp} breath={bool(breath)}",
        )

    return body()


def case_lamp_survives_every_frame():
    """Every frame, not just the first: this is a lamp, not a boot animation."""

    def body():
        d, a = fresh(ntp_ok=True)
        lit = []

        def draw_and_check(ms):
            a.draw(ms)
            lit.append(lamp_pixels(d.graphics) == [(0, 0, WHITE)])

        draw_and_check(0)
        a.ntp_ok = False
        draw_and_check(1000)
        a.ntp_ok = True
        draw_and_check(2000)
        return report(
            "the lamp is drawn on every ambient frame",
            all(lit),
            f"frames={lit}",
        )

    return body()


def case_lamp_is_drawn_after_the_clear():
    """Order matters: clear() wipes the panel, so the lamp must come after it."""

    def body():
        d, a = fresh(ntp_ok=True)
        order = []
        real_clear = d.graphics.clear
        real_pixel = d.graphics.pixel

        def spy_clear():
            order.append("clear")
            real_clear()

        def spy_pixel(x, y):
            order.append("pixel")
            real_pixel(x, y)

        d.graphics.clear = spy_clear
        d.graphics.pixel = spy_pixel
        a.draw(0)
        return report(
            "the lamp is drawn after display.clear()",
            order[:2] == ["clear", "pixel"],
            f"order={order[:3]}...",
        )

    return body()


def case_dark_room_keeps_the_lamp():
    """A dark room is when the lamp matters most - so it must survive it.

    This is the case the panel actually spends its evenings in (measured on the
    board: light()=17 against LIGHT_DARK=40). The clock and the breathing pixel
    go, because furniture should not light a bedroom; the lamp stays, because
    otherwise a powered board and a dead one look identical in the dark.
    """

    def body():
        d, a = fresh(ntp_ok=True)
        a.draw_dark()
        g = d.graphics
        return report(
            "a dark room keeps the lamp and drops everything else",
            g.pixels == [(0, 0, WHITE)] and g.texts == [],
            f"pixels={g.pixels} texts={g.texts} brightness={d.gu.brightness}",
        )

    return body()


def main():
    results = [
        case_lamp_is_in_the_corner(),
        case_lamp_lit_with_clock(),
        case_lamp_lit_without_clock(),
        case_lamp_survives_every_frame(),
        case_lamp_is_drawn_after_the_clear(),
        case_dark_room_keeps_the_lamp(),
    ]
    print()
    print(f"{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
