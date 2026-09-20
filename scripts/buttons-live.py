"""Live button monitor - run ON THE BOARD, then mash the buttons.

    mpremote connect "$(scripts/find-board.sh)" run scripts/buttons-live.py

Diagnostic for "the buttons do nothing": polls the same debouncer the firmware
uses, prints every press/release and a periodic raw snapshot of every switch,
and mirrors what it sees onto the panel so you get instant feedback.

If the panel never changes AND the raw snapshot never leaves all-False while a
button is held, the switches are not wired to the pins we expect (or the read
is broken) - not a firmware-logic problem.
"""

import time

import config
from buttons import Buttons
from display import Display
from main import ALL_SWITCHES

DURATION_S = 150
POLL_MS = 10
DRAW_MS = 60
REPORT_MS = 2000


def run():
    d = Display(config)
    buttons = Buttons(d, ALL_SWITCHES, {name: 0 for name in ALL_SWITCHES})
    d.set_brightness(0.6)

    print("buttons-live: watching", ", ".join(sorted(ALL_SWITCHES)),
          "for", DURATION_S, "s")
    print("buttons-live: MASH the buttons now")

    started = time.ticks_ms()
    last_draw = 0
    last_report = 0

    while time.ticks_diff(time.ticks_ms(), started) < DURATION_S * 1000:
        now = time.ticks_ms()
        for kind, name in buttons.poll(now):
            print("EVENT", kind, name, "@", time.ticks_diff(now, started), "ms")

        if time.ticks_diff(now, last_report) >= REPORT_MS:
            last_report = now
            print("raw @", time.ticks_diff(now, started), "ms",
                  {n: d.is_pressed(s) for n, s in ALL_SWITCHES.items()})

        if time.ticks_diff(now, last_draw) >= DRAW_MS:
            last_draw = now
            down = [n for n in ("A", "B", "C", "D") if buttons.is_stable_down(n)]
            d.clear()
            label = "+".join(down) if down else "press!"
            d.text(label, 0, 1, rgb=(0, 180, 255), scale=2 if down else 1)
            d.update()

        time.sleep_ms(POLL_MS)

    d.clear()
    d.update()
    print("buttons-live: done")


run()
