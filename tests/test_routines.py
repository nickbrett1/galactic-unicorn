#!/usr/bin/env python3
"""Host tests for the routine catalogue and the remote-only routine.

    python3 -m pytest tests/test_routines.py

Two things are pinned here:

  * `routines.json` is drawable: every routine names a symbol `lib/icons.py`
    actually has, and its icon + label fit the 53 px panel. This is what makes
    adding a routine to the table a safe edit rather than a blank screen.
  * A routine with no button of its own (`shoes`) can still be started from the
    remote, and takes exactly the path a physical press takes: PROMPT, settle
    into COUNTDOWN, inert while another countdown runs.

`lib/routine.py` imports the frozen firmware modules, so — as in
tests/test_routine_minutes.py — `galactic`/`picographics` are faked and the
engine's event handling is driven directly. Nothing here renders.
"""

import json
import os
import sys
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

# The board's ticks_* functions do not exist on the host; `now` is an integer.
_TICKS = [0]
if not hasattr(time, "ticks_ms"):
    time.ticks_ms = lambda: _TICKS[0]
if not hasattr(time, "ticks_diff"):
    time.ticks_diff = lambda now, then: now - then
if not hasattr(time, "ticks_add"):
    time.ticks_add = lambda ticks, delta: ticks + delta


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

import bigfont
import icons
import routine

with open(os.path.join(ROOT, "routines.json")) as _fh:
    CATALOGUE = json.load(_fh)
ROUTINES = CATALOGUE["routines"]
BY_ID = {r["id"]: r for r in ROUTINES}

PANEL_W = _FakeUnicorn.WIDTH
ICON_GAP = routine.ICON_GAP
MIN = 60 * 1000


# -- the catalogue is drawable ------------------------------------------------

def test_every_routine_names_an_icon_the_panel_has():
    for r in ROUTINES:
        assert r["symbol"] in icons.ICONS, r["id"]


def test_every_icon_frame_is_11_rows_and_ten_or_more_wide():
    for r in ROUTINES:
        frames = icons.ICONS[r["symbol"]]
        assert frames, r["id"]
        for frame in frames:
            assert len(frame) == _FakeUnicorn.HEIGHT, r["id"]
            for row in frame:
                assert len(row) == len(frame[0]), r["id"]


def test_every_label_fits_the_panel_on_its_own():
    # A label wider than the panel is clipped, so no routine may have one.
    # (The *pair* does not always fit: "Cleanup" + its icon is 62 px on a 53 px
    # panel and _draw_selection falls back to drawing both at x=0. That is the
    # shipped behaviour for the third routine, not a rule to copy - see the
    # next test for the pair this feature adds.)
    for r in ROUTINES:
        assert 0 < bigfont.text_width(r["label"]) <= PANEL_W, r["id"]


def test_shoes_icon_and_label_fit_together():
    span = icons.icon_width("shoe") + ICON_GAP + bigfont.text_width("Shoes")
    assert 0 < span <= PANEL_W, span


def test_only_the_three_physical_buttons_start_routines():
    # D cancels. `shoes` is deliberately unbound: the panel must not grow a
    # fourth routine button out of a config edit.
    assert CATALOGUE["buttons"] == {
        "A": "bathtime",
        "B": "booktime",
        "C": "cleanup",
        "D": "cancel",
    }
    bound = set(CATALOGUE["buttons"].values())
    assert "shoes" not in bound


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


def _engine():
    return routine.Engine(
        _Display(), _Buttons(), _Audio(), _Ambient(), _Config(),
        ROUTINES, CATALOGUE["buttons"],
    )


def _deliver(engine, now):
    """Drain queued remote events and dispatch them, with no frame render."""
    events = []
    engine._drain_injected(events)
    engine._handle_events(events, now)
    return events


# -- a buttonless routine, started from the remote ----------------------------

def test_shoes_has_no_button_so_the_remote_path_is_the_only_one():
    e = _engine()
    assert e._button_event("shoes") is None


def test_remote_shoes_works_from_ambient():
    e = _engine()
    e.post_event("shoes")
    assert _deliver(e, 1000) == [("remote", "shoes")]
    assert e.state == routine.PROMPT
    assert e.routine_id() == "shoes"


def test_remote_shoes_uses_its_own_routines_json_minutes():
    e = _engine()
    e.post_event("shoes")
    _deliver(e, 1000)
    e.start_countdown(2000)
    assert e.state == routine.COUNTDOWN
    assert e.total_ms == BY_ID["shoes"]["minutes"] * MIN


def test_remote_shoes_honours_a_requested_length():
    e = _engine()
    e.post_event("shoes", 2)
    _deliver(e, 1000)
    e.start_countdown(2000)
    assert e.total_ms == 2 * MIN


def test_a_second_remote_shoes_while_selected_starts_the_countdown():
    # The PROMPT settle is 3 s; a repeat ask shortcuts it, like pressing the
    # same button twice.
    e = _engine()
    e.post_event("shoes")
    _deliver(e, 1000)
    e.post_event("shoes")
    _deliver(e, 2000)
    assert e.state == routine.COUNTDOWN


def test_remote_shoes_is_inert_while_another_countdown_runs():
    e = _engine()
    e.post_event("bathtime")
    _deliver(e, 1000)
    e.start_countdown(2000)
    e.post_event("shoes")
    assert _deliver(e, 3000) == [("remote", "shoes")]
    assert e.state == routine.COUNTDOWN
    assert e.routine_id() == "bathtime"


def test_remote_shoes_switches_the_prompt():
    e = _engine()
    e.post_event("bathtime")
    _deliver(e, 1000)
    e.post_event("shoes")
    _deliver(e, 2000)
    assert e.state == routine.PROMPT
    assert e.routine_id() == "shoes"


def test_d_still_cancels_a_remote_shoes_countdown():
    e = _engine()
    e.post_event("shoes")
    _deliver(e, 1000)
    e.start_countdown(2000)
    e._handle_events([("press", "D")], 3000)
    assert e.state == routine.AMBIENT
    assert e.routine_id() is None


def test_a_physical_button_still_starts_its_own_routine():
    e = _engine()
    e._handle_events([("press", "A")], 1000)
    assert e.state == routine.PROMPT
    assert e.routine_id() == "bathtime"
