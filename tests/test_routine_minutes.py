#!/usr/bin/env python3
"""Host tests for the engine half of the remote-chosen countdown length.

    python3 -m pytest tests/test_routine_minutes.py

`lib/routine.py` is the side-effect half (lib/reconcile.py holds the pure
decision). It normally cannot be imported on the host because its imports pull
in the frozen firmware modules, so this file fakes `galactic`/`picographics`
the same way tests/test_ambient.py does, and drives the ENGINE's event handling
directly (`_drain_injected` + `_handle_events`) - no rendering, no hardware.

What is pinned here is the fence from NOTES.md (T6) and
device-protocols.md section 3:

  * a remote start with `minutes` runs for that length;
  * the length is consumed once, never persisted;
  * a physical press (no request) keeps routines.json `minutes`;
  * cancel drops a pending length, and switching selection drops it;
  * an invalid length falls back to the routine's own, exactly like absent.

The pure rule (1|3|5, default) is pinned in tests/test_reconcile.py; the remote
forwarding seam in tests/test_remote.py.
"""

import os
import sys
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

# The board has ticks_ms/ticks_diff/ticks_add; the host does not. `now` here is
# a plain integer, so ticks_add is ordinary addition (same shim idea as
# test_remote.py).
_TICKS = [0]
if not hasattr(time, "ticks_ms"):
    time.ticks_ms = lambda: _TICKS[0]
if not hasattr(time, "ticks_diff"):
    time.ticks_diff = lambda now, then: now - then
if not hasattr(time, "ticks_add"):
    time.ticks_add = lambda ticks, delta: ticks + delta


# -- the frozen firmware modules, faked (same pattern as test_ambient.py) -----

class _FakeUnicorn:
    WIDTH = 53
    HEIGHT = 11
    SWITCH_A = "A"
    SWITCH_B = "B"
    SWITCH_C = "C"
    SWITCH_D = "D"
    SWITCH_SLEEP = "SLEEP"
    SWITCH_VOLUME_UP = "VOL_UP"
    SWITCH_VOLUME_DOWN = "VOL_DOWN"
    SWITCH_BRIGHTNESS_UP = "BRIGHT_UP"
    SWITCH_BRIGHTNESS_DOWN = "BRIGHT_DOWN"


class _FakeGraphics:
    def __init__(self, display=None):
        pass

    def create_pen(self, r, g, b):
        return (r, g, b)

    def set_pen(self, pen):
        pass

    def clear(self):
        pass

    def pixel(self, x, y):
        pass

    def rectangle(self, x, y, w, h):
        pass

    def text(self, s, x, y, scale=1):
        pass

    def measure_text(self, s, scale=1):
        return 6 * len(s) * scale


_galactic = types.ModuleType("galactic")
_galactic.GalacticUnicorn = _FakeUnicorn
sys.modules["galactic"] = _galactic

_picographics = types.ModuleType("picographics")
_picographics.DISPLAY_GALACTIC_UNICORN = 0
_picographics.PicoGraphics = _FakeGraphics
sys.modules["picographics"] = _picographics

import routine

# -- minimal doubles: nothing here renders ------------------------------------

class _Display:
    def clear(self, rgb=None):
        pass

    def update(self):
        pass

    def set_brightness(self, value):
        pass

    def use(self, rgb):
        pass

    def pixel(self, x, y):
        pass

    def room_is_dark(self):
        return False


class _Buttons:
    def poll(self, now):
        return []


class _Audio:
    def stop(self):
        pass

    def tick(self, now):
        pass

    def chime(self, now):
        pass


class _Ambient:
    def draw(self, age_ms):
        pass


class _Config:
    DEMO_SECONDS = 0
    D_CANCEL_HOLD_MS = 0
    EXTEND_MINUTES = 2
    PROMPT_MS = 3000
    BRIGHTNESS_AMBIENT = 0.1
    BRIGHTNESS_PROMPT = 0.5
    BRIGHTNESS_COUNTDOWN = 0.5
    BRIGHTNESS_HANDOFF = 0.5


# Deliberately not all 5: booktime is 7 so "fell back to the routine" and
# "defaulted to 5" are distinguishable.
ROUTINES = [
    {"id": "bathtime", "label": "Bath", "minutes": 5, "symbol": "bath"},
    {"id": "booktime", "label": "Book", "minutes": 7, "symbol": "book"},
    {"id": "cleanup", "label": "Cleanup", "minutes": 5, "symbol": "toy-box"},
]
BUTTON_MAP = {"A": "bathtime", "B": "booktime", "C": "cleanup", "D": "cancel"}

MIN = 60 * 1000


def _engine():
    return routine.Engine(
        _Display(), _Buttons(), _Audio(), _Ambient(), _Config(), ROUTINES, BUTTON_MAP
    )


def _deliver(engine, now):
    """Drain queued remote events and dispatch them, with no frame render."""
    events = []
    engine._drain_injected(events)
    engine._handle_events(events, now)
    return events


def _press(engine, name, now):
    engine._handle_events([("press", name)], now)


# -- a remote start runs for the requested length -----------------------------

def test_remote_start_uses_the_requested_minutes():
    e = _engine()
    e.post_event("bathtime", 3)
    _deliver(e, 1000)
    assert e.state == routine.PROMPT
    assert e.routine_id() == "bathtime"
    e.start_countdown(2000)
    assert e.state == routine.COUNTDOWN
    assert e.total_ms == 3 * MIN


def test_remote_start_overrides_a_different_routine_length():
    # booktime is configured for 7; the remote asks for 3 and gets 3.
    e = _engine()
    e.post_event("booktime", 3)
    _deliver(e, 1000)
    e.start_countdown(2000)
    assert e.total_ms == 3 * MIN


def test_each_offered_length_is_honoured():
    for value in (1, 3, 5):
        e = _engine()
        e.post_event("bathtime", value)
        _deliver(e, 1000)
        e.start_countdown(2000)
        assert e.total_ms == value * MIN


# -- absent / invalid falls back to the routine's own minutes -----------------

def test_absent_minutes_uses_the_routine_length():
    # An old remote: the field is absent, so the routine's own 7 is used.
    e = _engine()
    e.post_event("booktime")
    _deliver(e, 1000)
    e.start_countdown(2000)
    assert e.total_ms == 7 * MIN


def test_invalid_minutes_falls_back_to_the_routine_length():
    for value in (2, 4, 0, -1, True, "3", 3.0):
        e = _engine()
        e.post_event("booktime", value)
        _deliver(e, 1000)
        e.start_countdown(2000)
        assert e.total_ms == 7 * MIN


def test_physical_press_keeps_the_routine_length():
    # No remote event at all: the panel behaves exactly as before the change.
    e = _engine()
    _press(e, "A", 1000)
    e.start_countdown(2000)
    assert e.total_ms == 5 * MIN


def test_physical_press_for_the_seven_minute_routine():
    e = _engine()
    _press(e, "B", 1000)
    e.start_countdown(2000)
    assert e.total_ms == 7 * MIN


# -- the length is per-command: consumed once, dropped on cancel/switch -------

def test_the_request_is_consumed_once():
    e = _engine()
    e.post_event("bathtime", 3)
    _deliver(e, 1000)
    e.start_countdown(2000)
    assert e.total_ms == 3 * MIN
    # A later, physical run of the same routine is back on routines.json.
    e.cancel(3000)
    _press(e, "A", 4000)
    e.start_countdown(5000)
    assert e.total_ms == 5 * MIN


def test_cancel_drops_a_pending_request():
    e = _engine()
    e.post_event("bathtime", 3)
    _deliver(e, 1000)  # -> PROMPT
    e.cancel(2000)  # -> AMBIENT, request dropped
    _press(e, "A", 3000)
    e.start_countdown(4000)
    assert e.total_ms == 5 * MIN


def test_switching_selection_drops_the_request():
    # The remote asked for bathtime at 3; a physical switch to booktime must
    # not carry 3 onto booktime's countdown.
    e = _engine()
    e.post_event("bathtime", 3)
    _deliver(e, 1000)  # -> PROMPT bathtime
    _press(e, "B", 2000)  # -> PROMPT booktime
    assert e.routine_id() == "booktime"
    e.start_countdown(3000)
    assert e.total_ms == 7 * MIN


def test_a_second_remote_start_replaces_the_request():
    e = _engine()
    e.post_event("bathtime", 3)
    _deliver(e, 1000)  # -> PROMPT bathtime
    e.post_event("cleanup", 1)
    _deliver(e, 2000)  # -> PROMPT cleanup
    e.start_countdown(3000)
    assert e.routine_id() == "cleanup"
    assert e.total_ms == 1 * MIN


def test_remote_start_while_prompting_the_same_routine_goes_straight_to_countdown():
    e = _engine()
    _press(e, "A", 1000)  # physical -> PROMPT bathtime
    e.post_event("bathtime", 3)
    _deliver(e, 2000)  # same routine again -> COUNTDOWN
    assert e.state == routine.COUNTDOWN
    assert e.total_ms == 3 * MIN
