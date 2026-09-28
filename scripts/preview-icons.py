"""Cycle every weather glyph on the REAL panel, so you can look at them.

This runs ON the board - it is not a host script. Push it and let it drive
the display directly:

    .venv/bin/mpremote connect /dev/tty.usbmodem83201 run scripts/preview-icons.py

Why it exists: there is no way to ask the board for "show me rain". The
condition comes from Open-Meteo, so seeing a specific glyph used to mean
waiting for the right weather - or editing the tree. This paints all seven
in turn with the same palettes and the same brightness the idle screen uses,
so what you see here is what you get in production.

Each condition is held for HOLD_MS with a label and a representative
temperature, composed exactly like ambient._draw_weather (icon and digits
centred together as one group). Rain animates, because that is the only
glyph that does. Progress is also printed to the REPL, so a headless run
still says which one is up.

It finishes with a soft reset, which hands the panel back to main.py - so
you do not have to remember to restart it.

Pace and a single condition are set by a global, because `mpremote run` takes
NO script arguments (anything after the path is the next mpremote command,
not sys.argv). Set the global with `exec` first, in the same invocation, and
this module reads it instead of its own default:

    mpremote connect <port> exec "PREVIEW_HOLD_MS=8000" run scripts/preview-icons.py
    mpremote connect <port> exec "PREVIEW_CONDITION='rain'" run scripts/preview-icons.py

MicroPython subset applies here (no f-strings): this file is compiled by the
board, not by CPython.
"""

import time

import machine

import ambient
import bigfont
import config
import display
import icons
import watchdog

# Long enough to walk over and look, short enough to sit through. Overridable
# from the REPL namespace - see the docstring.
HOLD_MS = globals().get("PREVIEW_HOLD_MS", 3000)

# None = the whole set, in order.
CONDITION = globals().get("PREVIEW_CONDITION", None)

# A temperature per condition, only so the frame is composed like a real one
# (the digits are part of what has to fit). The values are the ones the README
# uses as examples.
LABELS = {
    "sun": "28C",
    "partly": "21C",
    "cloud": "15C",
    "fog": "9C",
    "rain": "12C",
    "snow": "-3C",
    "thunder": "19C",
}

ORDER = ["sun", "partly", "cloud", "fog", "rain", "snow", "thunder"]


def draw(d, condition, label):
    """One idle frame: centred icon + digits, then the power lamp.

    Mirrors ambient.Ambient._draw_weather rather than calling it, so this can
    run without a weather instance (and therefore without a network).
    """
    icon_w = icons.weather_icon_width(condition) or icons.WEATHER_ICON_W
    span = icon_w + ambient.WEATHER_GAP + bigfont.text_width(label)
    x = max(0, (d.width - span) // 2)
    body, lit, shade, accent = ambient.WEATHER_PENS.get(
        condition, ambient.WEATHER_PENS["cloud"]
    )
    d.clear()
    d.power_pixel()
    icons.draw_weather_icon(
        d, condition, x, 0, age_ms=time.ticks_ms(),
        rgb=body, rim_rgb=accent, lit_rgb=lit, shade_rgb=shade,
    )
    bigfont.draw_text(
        d, x + icon_w + ambient.WEATHER_GAP, 0, label, rgb=ambient.WEATHER_RGB
    )
    d.update()


def hold(d, condition, ms):
    label = LABELS.get(condition, "15C")
    print("preview:", condition, label)
    end = time.ticks_add(time.ticks_ms(), ms)
    while time.ticks_diff(end, time.ticks_ms()) > 0:
        draw(d, condition, label)
        # main.py is the thing that normally feeds the fuse, and it is NOT
        # running while this does - so without this the board resets on the
        # ~8 s WDT partway through the set and the serial node disappears.
        watchdog.feed()
        time.sleep_ms(icons.WEATHER_FRAME_MS)


def main():
    d = display.Display(config)
    d.set_brightness(config.BRIGHTNESS_WEATHER)
    if CONDITION and CONDITION in icons.WEATHER_ICONS:
        hold(d, CONDITION, HOLD_MS * 3)
    elif CONDITION:
        print("preview: unknown condition", CONDITION, "- known:", ", ".join(ORDER))
    else:
        for condition in ORDER:
            hold(d, condition, HOLD_MS)
    print("preview: done - soft resetting so main.py takes the panel back")
    machine.soft_reset()


main()
