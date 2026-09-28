#!/usr/bin/env python3
"""Tests for the idle weather's PURE helpers (lib/weather.py) and glyphs.

    python3 -m pytest tests/test_weather.py

The fetch itself cannot run without a board (no emulator), so what CAN be
pinned on the host is pinned here: the WMO-code classifier, the JSON parse
(the shape a changed or wrong host must not crash on), the panel's temperature
text, and the geometry of the weather glyphs the idle screen draws.

lib/weather.py imports nothing board-only at module level (no machine, no
network), and lib/icons.py imports nothing at all, so both run under CPython.

Kept MicroPython-subset-safe on purpose (the same subset lib/weather.py lives
in): no f-strings, no walrus, no type annotations, py3.4-ish syntax.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

import icons
import weather

# -- the WMO-code classifier --------------------------------------------------

def test_classify_clear_and_cloud_bands():
    assert weather.classify(0) == weather.SUN
    assert weather.classify(1) == weather.PARTLY
    assert weather.classify(2) == weather.PARTLY
    assert weather.classify(3) == weather.CLOUD


def test_classify_fog_rain_snow_thunder():
    assert weather.classify(45) == weather.FOG
    assert weather.classify(48) == weather.FOG
    assert weather.classify(51) == weather.RAIN  # drizzle folds into rain
    assert weather.classify(63) == weather.RAIN
    assert weather.classify(82) == weather.RAIN  # violent showers
    assert weather.classify(71) == weather.SNOW
    assert weather.classify(86) == weather.SNOW
    assert weather.classify(95) == weather.THUNDER
    assert weather.classify(99) == weather.THUNDER


def test_classify_unknown_falls_back_to_cloud():
    # A code we do not know, or none at all, must not be drawn as a sun.
    assert weather.classify(12345) == weather.CLOUD
    assert weather.classify(None) == weather.CLOUD
    assert weather.classify("nonsense") == weather.CLOUD
    # A numeric string is still a code.
    assert weather.classify("0") == weather.SUN


# -- the JSON parse -----------------------------------------------------------

def test_parse_current_bytes_and_str():
    body = (
        b'{"latitude":40.71,"current":{"time":"2026-09-28T13:45",'
        b'"temperature_2m":14.3,"weather_code":3}}'
    )
    assert weather.parse_current(body) == (14, "cloud")
    assert weather.parse_current(body.decode()) == (14, "cloud")


def test_parse_current_rounds_to_whole_degrees():
    assert weather.parse_current('{"current":{"temperature_2m":14.5}}')[0] == 14
    assert weather.parse_current('{"current":{"temperature_2m":-6.2}}')[0] == -6
    assert weather.parse_current('{"current":{"temperature_2m":0}}')[0] == 0


def test_parse_current_missing_weather_code_still_gives_a_temperature():
    # Open-Meteo omits a field if it was not asked for; the temperature is the
    # part the panel must keep, with an honest cloud fallback for the sky.
    assert weather.parse_current('{"current":{"temperature_2m":7.0}}') == (7, "cloud")


def test_parse_current_bad_input_returns_none():
    assert weather.parse_current(b"") == (None, None)
    assert weather.parse_current(b"not json") == (None, None)
    assert weather.parse_current(b"{}") == (None, None)
    assert weather.parse_current(b'{"current":{}}') == (None, None)
    assert weather.parse_current(b'{"current":{"temperature_2m":"hot"}}') == (None, None)


# -- the panel's temperature text ---------------------------------------------

def test_format_temp():
    assert weather.format_temp(18) == "18C"
    assert weather.format_temp(0) == "0C"
    assert weather.format_temp(-5) == "-5C"


# -- glyph geometry -----------------------------------------------------------
#
# The idle screen blits these raw, so a wrong row length would draw a skewed
# shape that only shows up by eye, on the wall. Pin the grid here.

def test_every_weather_icon_frame_is_11_rows_of_the_right_width():
    for name, frames in icons.WEATHER_ICONS.items():
        assert len(frames) >= 1, name
        w = icons.weather_icon_width(name)
        allowed = set("XOLS.")
        for frame in frames:
            assert len(frame) == 11, (name, len(frame))
            for row in frame:
                assert len(row) == w, (name, row, len(row), w)
                assert set(row) <= allowed, (name, row)


def test_every_weather_glyph_is_shaded():
    """Every glyph carries the lit-top / shaded-base bands, not one flat ink.

    A single flat colour read as a blob on the wall; the tone split is what
    makes the icons read. Pin that each glyph actually uses the lit or shade
    ink (so a future edit cannot quietly flatten one back to a stamp).
    """
    for name, frames in icons.WEATHER_ICONS.items():
        flat = "".join("".join(frame) for frame in frames)
        assert set("LS") & set(flat), name


def test_the_cloud_is_lit_on_top_and_shaded_underneath():
    """The shade reads by TONE: lit crown, mid body, a shaded underside."""
    (cloud,) = icons.WEATHER_ICONS["cloud"]
    rows = [r for r, row in enumerate(cloud) if set(row) - {"."}]
    assert rows, "the cloud must draw something"

    # The crown is sunlit - no body or shade sits above the lit edge.
    assert set(cloud[rows[0]]) <= set("L."), cloud[rows[0]]

    # The base is shaded, but the shade is a LENS and not a band: it steps
    # outward as it descends and stops short of the silhouette on every row,
    # leaving body ink on the flanks. A shade run that reached the edge is the
    # flat slab this glyph deliberately stopped drawing (lib/icons.py `_CLOUD`).
    widths = []
    for row in [cloud[r] for r in rows[-3:]]:
        inked = [i for i, ink in enumerate(row) if ink != "."]
        assert row[inked[0]] != "S" and row[inked[-1]] != "S", row
        shaded = [i for i, ink in enumerate(row) if ink == "S"]
        assert shaded, row
        # one contiguous run, never split into per-lobe puddles
        assert shaded == list(range(shaded[0], shaded[-1] + 1)), row
        widths.append(len(shaded))
    # wider at every step down: the stepping IS the blend
    assert widths == sorted(widths) and len(set(widths)) == 3, widths

    # All three extra inks appear, and there is a body inside the shading.
    flat = "".join(cloud)
    for ink in "OLS":
        assert ink in flat, ink
    assert "X" in flat


def test_draw_weather_icon_paints_each_ink_with_its_own_pen():
    """'X' 'O' 'L' 'S' each take their own pen within one frame."""
    drawn = []

    class FakeDisplay:
        def use(self, rgb):
            self.pen = rgb

        def rect(self, x, y, w, h):
            drawn.append((self.pen, y))

    icons.draw_weather_icon(
        FakeDisplay(), "cloud", 0, 0, rgb=(1, 2, 3), rim_rgb=(9, 9, 9),
        lit_rgb=(4, 4, 4), shade_rgb=(5, 5, 5),
    )
    assert drawn, "the cloud must draw something"
    pens = {pen for pen, _ in drawn}
    assert pens == {(1, 2, 3), (9, 9, 9), (4, 4, 4), (5, 5, 5)}, pens


def test_rain_animates_and_the_drops_fall():
    """Rain is the one moving glyph: its frames differ, and a drop moves down.

    A still "cloud with dots" did not read as rain on the wall, so this pins
    that the frames are actually distinct and that the falling streams advance
    downward rather than flickering in place.
    """
    frames = icons.WEATHER_ICONS["rain"]
    assert len(frames) >= 2, "rain must have more than one frame to animate"

    def drop_rows(frame):
        # The cloud is the top six rows; a drop is an accent ink below it.
        return [r for r in range(6, 11) if "O" in frame[r]]

    rows = [drop_rows(frame) for frame in frames]
    assert all(rows), "every rain frame must show falling drops"
    assert len({tuple(r) for r in rows}) == len(frames), rows
    # Each successive frame's drops sit one row lower than the last.
    for i in range(1, len(rows)):
        assert min(rows[i]) == min(rows[i - 1]) + 1, rows


def test_only_rain_animates():
    """Everything else is a single static frame (the idle panel is furniture)."""
    for name, frames in icons.WEATHER_ICONS.items():
        if name == "rain":
            continue
        assert len(frames) == 1, name


def test_every_classified_condition_has_an_icon():
    conditions = set()
    for code in (0, 1, 3, 45, 51, 71, 95, 12345):
        conditions.add(weather.classify(code))
    assert conditions <= set(icons.WEATHER_ICONS)


def test_weather_icon_width_matches_the_frames():
    for name in icons.WEATHER_ICONS:
        assert icons.weather_icon_width(name) == len(
            icons.WEATHER_ICONS[name][0][0]
        ), name
    # the cloud is deliberately much wider than the 11-wide conditions
    assert icons.weather_icon_width("cloud") > 11
    assert icons.weather_icon_width("does-not-exist") == 0


def test_draw_weather_icon_falls_back_for_an_unknown_condition():
    """An unknown condition draws the cloud, not nothing."""
    drawn = []

    class FakeDisplay:
        def use(self, rgb):
            drawn.append(("use", rgb))

        def rect(self, x, y, w, h):
            drawn.append(("rect", x, y, w, h))

    icons.draw_weather_icon(FakeDisplay(), "hail", 0, 0, rgb=(1, 2, 3))
    assert drawn, "an unknown condition must still draw something"
