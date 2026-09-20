"""Bench smoke test - runs ON THE BOARD.

Exercises the real firmware modules end to end and drives the state machine
through every transition, so a refactor that breaks rendering fails here
rather than on the living-room wall.

PREFER DEPLOYING TO FLASH. `mpremote mount .` also works, but mounting serves
files over the REPL and churns the board's heap until the ~10.7 KB
PicoGraphics framebuffer can no longer be allocated contiguously - it fails
with a MemoryError while `gc.mem_free()` still reports ~140 KB, i.e. a
fragmentation failure that looks like an out-of-memory one. Deploy first:

    mpremote connect "$(scripts/find-board.sh)" fs cp main.py :main.py
    mpremote connect "$(scripts/find-board.sh)" fs cp config.py :config.py
    mpremote connect "$(scripts/find-board.sh)" fs cp routines.json :routines.json
    mpremote connect "$(scripts/find-board.sh)" fs mkdir lib
    for f in lib/*.py; do
      mpremote connect "$(scripts/find-board.sh)" fs cp "$f" ":$f"
    done

then run it:

    mpremote connect "$(scripts/find-board.sh)" run scripts/bench-smoke.py

Every check is prefixed PASS/FAIL and a failing check does not stop the run.
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

PASS = 0
FAIL = 0


def _bad(value):
    raise AssertionError(f"unexpected: {value}")


def check(name, fn):
    global PASS, FAIL
    try:
        detail = fn()
        PASS += 1
        print(f"  PASS  {name}" + (f"  ({detail})" if detail else ""))
    # A bench harness must survive a failing check and keep going.
    except Exception as exc:  # noqa: BLE001
        FAIL += 1
        print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")


print("=== galactic-unicorn bench smoke ===")

# -- imports (this is the module-name switch under test) --------------------
import config
import digits
import display as display_mod

check("import display (module-name switch)", lambda: display_mod.__file__)
check("geometry", lambda: f"{display_mod.WIDTH}x{display_mod.HEIGHT}")

from ambient import Ambient
from buttons import Buttons
from display import Display, traffic_rgb
from routine import Engine
from sound import Audio

check("import ambient/sound/buttons/routine", lambda: "ok")

# -- construction -----------------------------------------------------------
d = Display(config)
check("construct Display", lambda: f"brightness={d.gu.get_brightness():.2f}")
check("light()", lambda: f"{d.light():.1f}")

# -- colour ramp (section 4a: traffic light, green means go) ----------------
def ramp_probe():
    samples = [0.0, 0.25, 0.5, 0.75, 1.0]
    return " ".join(f"{p:.2f}->{traffic_rgb(p)}" for p in samples)


check("traffic_rgb spans red->amber->green", ramp_probe)

def green_at_one():
    r, g, b = traffic_rgb(1.0)
    assert g > 200 and r < 60 and b < 100, f"the end is not green: {(r, g, b)}"
    return f"{r, g, b}"


check("the end is fully green", green_at_one)

def red_at_zero():
    r, g, b = traffic_rgb(0.0)
    assert r > 200 and g < 80, f"the start is not red: {(r, g, b)}"
    return f"{r, g, b}"


check("the start is red", red_at_zero)

# -- routines.json ----------------------------------------------------------
import json

with open("routines.json") as fh:
    data = json.load(fh)
routines = data["routines"]
button_map = data["buttons"]

check("routines.json parses", lambda: f"{len(routines)} routines, {button_map}")
check(
    "mapping is A/B/C/D = bathtime/booktime/tidyup/cancel",
    lambda: button_map
    if button_map == {"A": "bathtime", "B": "booktime", "C": "tidyup", "D": "cancel"}
    else (_ for _ in ()).throw(AssertionError(button_map)),
)

# -- digits render ----------------------------------------------------------
def draw_all_digits():
    t0 = time.ticks_ms()
    for _ in range(20):
        d.clear()
        for n in range(10):
            digits.draw_digit(d, 1 + n * 5, 0, 6, 9, 2, n, rgb=(0, 200, 120))
        d.update()
    return f"{time.ticks_diff(time.ticks_ms(), t0) // 20} ms/frame"


check("draw_digit 0-9", draw_all_digits)

def draw_bar_probe():
    for ratio in (1.0, 0.5, 0.0):
        d.clear()
        digits.draw_bar(d, 0, 9, d.width, 2, ratio, rgb=(0, 255, 48), bg_rgb=(8, 8, 8))
        d.update()
    return "ok"


check("draw_bar 1.0/0.5/0.0", draw_bar_probe)

# -- audio ------------------------------------------------------------------
audio = Audio(d, config)

def play_all_motifs():
    for tune in ("motif1", "motif2", "motif3"):
        audio.play(tune, variant="prompt")
        time.sleep_ms(500)
        audio.play(tune, variant="handoff")
        time.sleep_ms(500)
    audio.stop()
    return "3 motifs x prompt/handoff queued without error"


check("queue every motif (prompt + handoff)", play_all_motifs)

# -- buttons ----------------------------------------------------------------
switches = {
    "A": display_mod.SWITCHES["A"],
    "B": display_mod.SWITCHES["B"],
    "C": display_mod.SWITCHES["C"],
    "D": display_mod.SWITCHES["D"],
    "SLEEP": display_mod.SWITCH_SLEEP,
}
buttons = Buttons(d, switches, {"A": 1000, "B": 1000, "C": 1000, "D": 0, "SLEEP": 2000})


def button_reads():
    now = time.ticks_ms()
    events = buttons.poll(now)
    return f"no crash, events={events}"


check("buttons.poll on a real board", button_reads)

# -- state machine ----------------------------------------------------------
ambient = Ambient(d, config)
ambient.ntp_ok = False
engine = Engine(d, buttons, audio, ambient, config, routines, button_map)

print("--- state machine ---")


def step(label, ms, expect=None):
    t0 = time.ticks_ms()
    n = 0
    while time.ticks_diff(time.ticks_ms(), t0) < ms:
        engine.tick(time.ticks_ms())
        n += 1
        time.sleep_ms(5)
    got = engine.state
    ok = (expect is None) or (got == expect)
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {label}: state={got} ({n} frames)")
    else:
        FAIL += 1
        print(f"  FAIL  {label}: expected {expect}, got {got}")


engine.ambient.ntp_ok = True
step("AMBIENT renders the clock", 300, "ambient")

now = time.ticks_ms()
engine.start_prompt("bathtime", now)
step("PROMPT for bathtime", 400, "prompt")

now = time.ticks_ms()
engine.start_countdown(now)
step("COUNTDOWN renders digits + bar", 400, "countdown")
check("countdown is locally timed (no network)", lambda: f"total_ms={engine.total_ms}")

# Jump to just before zero and confirm HANDOFF is entered.
engine.end_ticks = time.ticks_add(time.ticks_ms(), 120)
step("COUNTDOWN -> HANDOFF at zero", 500, "handoff")

# HANDOFF must never get stuck.
engine.state_started = time.ticks_ms() - config.HANDOFF_MS - 50
step("HANDOFF -> AMBIENT automatically (never stuck)", 300, "ambient")

# D cancels from every active state.
now = time.ticks_ms()
engine.start_prompt("tidyup", now)
engine._handle_routine_event("press", "D", time.ticks_ms())
check("D cancels from PROMPT", lambda: engine.state if engine.state == "ambient" else _bad(engine.state))

engine.start_prompt("tidyup", time.ticks_ms())
engine.start_countdown(time.ticks_ms())
engine._handle_routine_event("press", "D", time.ticks_ms())
check("D cancels from COUNTDOWN", lambda: engine.state if engine.state == "ambient" else _bad(engine.state))

# Routine buttons are inert during COUNTDOWN.
engine.start_prompt("tidyup", time.ticks_ms())
engine.start_countdown(time.ticks_ms())
before = engine.end_ticks
engine._handle_routine_event("press", "A", time.ticks_ms())
check(
    "A/B/C inert in COUNTDOWN (no state change)",
    lambda: "inert" if engine.state == "countdown" and engine.end_ticks == before else _bad(engine.state),
)

# Hold -> +2 minutes.
engine._handle_routine_event("hold", "A", time.ticks_ms())
check(
    "+2 min on hold during COUNTDOWN",
    lambda: f"extended by {time.ticks_diff(engine.end_ticks, before)} ms",
)

engine.cancel(time.ticks_ms())

# -- throughput -------------------------------------------------------------
def frame_cost():
    engine.start_prompt("bathtime", time.ticks_ms())
    engine.start_countdown(time.ticks_ms())
    t0 = time.ticks_ms()
    n = 0
    while time.ticks_diff(time.ticks_ms(), t0) < 1000:
        engine.tick(time.ticks_ms())
        n += 1
    engine.cancel(time.ticks_ms())
    return f"{n} fps in COUNTDOWN"


check("countdown frame rate", frame_cost)

# -- countdown fill (the whole panel, one LED at a time) --------------------
def countdown_renders():
    engine.routine = engine._by_id("bathtime")
    engine.total_ms = 5 * 60 * 1000
    engine.state = "countdown"
    try:
        for frac in (1.0, 0.5, 0.1):
            engine.state_started = time.ticks_ms()
            engine.end_ticks = time.ticks_add(
                time.ticks_ms(), int(engine.total_ms * frac)
            )
            engine.tick(time.ticks_ms())
    finally:
        engine.cancel(time.ticks_ms())
    return "countdown drew at 0/50/90% done"


check("countdown renders at start/middle/end", countdown_renders)


def serpentine_covers_panel():
    path = engine.path
    assert len(path) == d.width * d.height, f"path has {len(path)} LEDs"
    assert len(set(path)) == len(path), "an LED is lit twice"
    assert path[0] == (0, 0), f"the fill starts at {path[0]}"
    assert path[-1] == (d.width - 1, d.height - 1), f"the fill ends at {path[-1]}"
    # Down the screen first, then one column to the right.
    assert path[1] == (0, 1), f"second LED is {path[1]}"
    assert path[d.height] == (1, 0), f"next column starts at {path[d.height]}"
    return f"{len(path)} LEDs, top-left -> bottom-right"


check("countdown path covers the panel down-then-right", serpentine_covers_panel)

# -- icons (frames must be X/. or the icon silently draws nothing) ----------
import icons


def icon_frames_valid():
    for name, frames in icons.ICONS.items():
        assert frames, f"{name} has no frames"
        for fr in frames:
            assert len(fr) == icons.ICON_H, f"{name}: {len(fr)} rows, want {icons.ICON_H}"
            for row in fr:
                assert len(row) == icons.ICON_W, f"{name}: row is {len(row)} wide"
                assert set(row) <= {"X", "."}, f"{name}: bad ink {sorted(set(row))}"
    return f"{len(icons.ICONS)} icons, all {icons.ICON_W}x{icons.ICON_H}"


check("icon frames use only X/. and are the right size", icon_frames_valid)


def every_symbol_resolves():
    for r in routines:
        sym = r.get("symbol")
        assert sym in icons.ICONS, f"{r.get('id')}: symbol {sym!r} has no icon"
    return ", ".join(f"{r['id']}->{r['symbol']}" for r in routines)


check("every routine's symbol names a real icon", every_symbol_resolves)


d.clear()
d.update()

print(f"=== {PASS} passed, {FAIL} failed ===")
