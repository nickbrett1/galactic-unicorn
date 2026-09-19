"""Walk the panel through every visual state - run this ON THE BOARD.

This is the script to run the moment you are standing in front of the display.
One command steps through every screen the firmware can draw, holding each one
long enough to actually look at it, and prints a label to the REPL so you know
what you are looking at:

    hello banner
      -> COUNTDOWN, layouts A and B, at 100% / 50% / 10% remaining
      -> PROMPT for each routine (so you hear each motif)
      -> HANDOFF for each routine (flash, then the green flood)
      -> NTP sync + the ambient clock
      -> a button-identity sweep (which physical cap is A/B/C/D)

It drives the REAL engine (`lib/routine.py`) through the real modules - this is
not a mock-up or a copy, so what you see is what the firmware does. It only
sets state the way `main.py` would and then calls `engine.tick()`.

DEPLOY FIRST, do not `mpremote mount .` - mounting serves files over the REPL
and fragments the heap until the PicoGraphics framebuffer cannot be allocated
(it fails with a MemoryError while `gc.mem_free()` still reports ~140 KB). See
`scripts/bench-smoke.py` for the long version. So:

    ./scripts/deploy.sh
    mpremote connect "$(scripts/find-board.sh)" run scripts/showcase.py

To run only one half of it (say, to re-check just the button sweep while
standing at the panel) flip the `RUN_TOUR` / `RUN_SWEEP` constants below. There
is deliberately no command-line argument: this MicroPython build has **no
`sys.argv` at all**, and `mpremote run` passes nothing, so a constant is the
only mechanism that actually works.

Two things it works around on purpose, both worth knowing:

* The bench is usually darker than `config.LIGHT_DARK`, and AMBIENT deliberately
  goes fully dark in a dark room - so the clock would show nothing. This script
  forces the room to "lit" and says so, otherwise the clock step is a blank
  panel that looks like a bug.
* The real HANDOFF lasts `config.HANDOFF_MS` (10 s). Showing all three in full
  is 30 s of standing around, so each is cut short here and labelled.

When it finishes, the firmware loop is NOT running - `mpremote run` interrupts
it - so the panel sits idle and dark. `mpremote soft-reset` hands it back.
"""

import gc
import os
import sys
import time

gc.collect()

# `mpremote mount .` exposes this tree at /remote but does not put it on
# sys.path, so make the mounted tree importable. On the board itself chdir
# fails harmlessly and the root is already importable.
try:
    os.chdir("/remote")
except OSError:
    pass
for _p in ("/remote", "/remote/lib"):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# What to run. Flip these to run only one half (e.g. just the button sweep).
RUN_TOUR = True
RUN_SWEEP = True

# How long to wait for a human to press the prompted button before deciding
# nobody is there and abandoning the sweep.
SWEEP_TIMEOUT_MS = 15000

# Each HANDOFF really runs for config.HANDOFF_MS; cut it here so the whole
# tour stays watchable.
HANDOFF_SHOW_MS = 6500


def log(message):
    print("showcase:", message)


def banner(title):
    print()
    print("=" * 52)
    print(f"  {title}")
    print("=" * 52)


import config
from ambient import Ambient
from buttons import Buttons
from display import Display
from main import ALL_SWITCHES, boot_banner, load_routines, sync_ntp
from routine import COUNTDOWN, Engine
from sound import Audio

display = Display(config)
routines, button_map = load_routines()
audio = Audio(display, config)
ambient = Ambient(display, config)
buttons = Buttons(
    display,
    ALL_SWITCHES,
    {
        "A": config.EXTEND_HOLD_MS,
        "B": config.EXTEND_HOLD_MS,
        "C": config.EXTEND_HOLD_MS,
        "D": config.D_CANCEL_HOLD_MS,
        "SLEEP": config.CANCEL_HOLD_MS,
        "VOL_DOWN": config.CANCEL_HOLD_MS,
    },
)
engine = Engine(display, buttons, audio, ambient, config, routines, button_map)

# See the module docstring: the room is dark, and a dark room means a dark
# panel in AMBIENT. Force it lit so the clock step actually shows the clock.
REAL_DARK = display.room_is_dark()
display.room_is_dark = lambda: False

BATHTIME = next(r for r in routines if r.get("id") == "bathtime")


def run(ms, poll_ms=20):
    """Tick the real engine for `ms` - this is the firmware loop."""
    started = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), started) < ms:
        engine.tick(time.ticks_ms())
        time.sleep_ms(poll_ms)


def show_countdown(fraction, layout, note, ms=4000):
    """Freeze a countdown at a known point through its run, in a given layout."""
    banner(f"COUNTDOWN - layout {layout} - {note}")
    config.COUNTDOWN_LAYOUT = layout
    engine.routine = BATHTIME
    engine.total_ms = int(BATHTIME.get("minutes", 5)) * 60 * 1000
    engine.state = COUNTDOWN
    engine.state_started = time.ticks_ms()
    engine.end_ticks = time.ticks_add(
        time.ticks_ms(), int(engine.total_ms * float(fraction))
    )
    log(f"layout {layout}, bathtime, {note} of a 5 min countdown")
    run(ms)
    # A beat of black between screens, so the next one reads as a fresh look
    # rather than a continuation of the one before it.
    display.clear()
    display.update()
    time.sleep_ms(700)


print()
print("galactic-unicorn showcase")
log(f"{len(routines)} routines, buttons={button_map}")
log("layout A vs B, then prompts, handoffs, clock, button sweep")
if REAL_DARK:
    log(
        f"note: the room reads dark (light()={display.light():.0f} < "
        f"LIGHT_DARK={config.LIGHT_DARK}) - AMBIENT would be OFF right now; "
        f"forcing it lit for this run"
    )

if RUN_TOUR:
    # -- 1. boot banner -----------------------------------------------------
    banner("BOOT / hello  (the phase-0 target, and what runs on every boot)")
    boot_banner(display, log)

    # -- 2. countdown, both layouts, three points through the run -----------
    banner("COUNTDOWN - the money screen, both candidate layouts")
    log("layout A: big digits left, label top-right, bar along the bottom 2 rows")
    log("layout B: the whole background is the bar, digits cut out of it")
    log("the thing to judge: with B the panel is a full-screen colour from the")
    log("brighter as it goes green. A is much quieter early - mostly dark,")
    log("one number, a thin bar. Decide which one you want living in the room.")
    log("also watch for this in B: the number is cut out of the bar in black")
    log("until the bar drains past it (~28% left), then flips to glowing in")
    log("front of it. Is that flip a nice moment or a lurch?")
    print()
    for layout in ("A", "B"):
        print(f"--- LAYOUT {layout} ---")
        show_countdown(1.0, layout, "100% left (5 min) - blue, easy to ignore")
        show_countdown(0.5, layout, "50% left (2 min 30 s) - teal, brightening")
        show_countdown(0.1, layout, "10% left (30 s) - full green, pulsing")

    # -- 3. PROMPT, one per routine (this is where you hear the motif) ------
    banner("PROMPT - ~3 s, the routine's tune plays (listen)")
    config.COUNTDOWN_LAYOUT = "A"
    for routine in routines:
        log(f"{routine.get('id')}: label={routine.get('label')!r} "
            f"tune={routine.get('tune')} button={routine.get('button')}")
        now = time.ticks_ms()
        engine.start_prompt(routine.get("id"), now)
        # Stop just short of the automatic hand-off to COUNTDOWN.
        run(config.PROMPT_MS - 400)
        engine.cancel(time.ticks_ms())
        time.sleep_ms(400)

    # -- 4. HANDOFF, one per routine ---------------------------------------
    banner("HANDOFF - the payoff screen: it flashes, then floods green")
    log(f"each one really lasts {config.HANDOFF_MS} ms; cut to "
        f"{HANDOFF_SHOW_MS} ms here")
    for routine in routines:
        log(f"{routine.get('id')}: end_message={routine.get('end_message')!r}")
        now = time.ticks_ms()
        engine.start_prompt(routine.get("id"), now)
        engine.start_countdown(now)
        # Two frames from zero, so we cross into HANDOFF immediately.
        engine.end_ticks = time.ticks_add(now, 300)
        engine.state_started = now
        run(HANDOFF_SHOW_MS)
        engine.cancel(time.ticks_ms())
        time.sleep_ms(300)

    # -- 5. NTP + the ambient clock ----------------------------------------
    banner("AMBIENT - the idle clock (this is what the panel does all day)")
    log("syncing NTP - may take ~10-20 s on a cold association")
    ambient.ntp_ok = sync_ntp(log)
    engine.cancel(time.ticks_ms())
    if ambient.ntp_ok:
        log("clock synced - this is the real time, dim, and it is the resting state")
    else:
        log("NTP failed - degrading to the slow-breathing status pixel "
            "(a network problem looks like quiet, never like broken)")
    run(6000)

# -- 6. button-identity sweep ----------------------------------------------
SWEEP_ORDER = (
    "A",
    "B",
    "C",
    "D",
    "SLEEP",
    "VOL_UP",
    "VOL_DOWN",
    "BRIGHT_UP",
    "BRIGHT_DOWN",
)
SWEEP_LABEL = {
    "VOL_UP": "VOL +",
    "VOL_DOWN": "VOL -",
    "BRIGHT_UP": "BRIGHT +",
    "BRIGHT_DOWN": "BRIGHT -",
}


def sweep_prompt(name):
    text = SWEEP_LABEL.get(name, name)
    display.clear()
    w = display.text_width(text, scale=2)
    display.text(text, max(0, (display.width - w) // 2), 1, rgb=(0, 150, 200), scale=2)
    display.update()


def sweep():
    banner("BUTTON IDENTITY - press the one the panel names")
    log(f"{SWEEP_TIMEOUT_MS // 1000} s to press each, in this order:")
    log(" ".join(SWEEP_LABEL.get(n, n) for n in SWEEP_ORDER))
    log("a mismatch tells us which pin that cap is really wired to")
    print()

    results = []
    for name in SWEEP_ORDER:
        sweep_prompt(name)
        log(f"PRESS {SWEEP_LABEL.get(name, name)}")
        started = time.ticks_ms()
        fired = None
        while time.ticks_diff(time.ticks_ms(), started) < SWEEP_TIMEOUT_MS:
            for kind, event_name in buttons.poll(time.ticks_ms()):
                if kind == "press":
                    fired = event_name
                    break
            if fired is not None:
                break
            time.sleep_ms(20)

        if fired is None:
            log("no press - nobody is there; stopping the sweep")
            break
        if fired == name:
            log(f"  prompted {name} -> fired {fired}: OK")
        else:
            log(f"  prompted {name} -> fired {fired}: MISMATCH "
                f"(that cap is wired as {fired})")
        results.append((name, fired))
        time.sleep_ms(300)

    print()
    log("--- sweep result ---")
    log(f"{len(results)}/{len(SWEEP_ORDER)} buttons checked")
    for prompted, fired in results:
        log(f"  prompted {prompted:<11} fired {fired}")
    return results


if RUN_SWEEP:
    sweep()

display.clear()
display.set_brightness(config.BRIGHTNESS_AMBIENT)
display.update()
print()
log("showcase complete")
log("NOTE: this script interrupted the firmware loop, so the panel is now")
log("idle and dark. `mpremote soft-reset` (or the board's reset button)")
log("hands the display back to main.py.")
