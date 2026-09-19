"""Chunky numerals and the draining bar.

The money screens. Do NOT scale the bitmap font: hand-draw blocky
7-segment-ish numerals so one or two digits fill the full height and read
from across the room.
"""

# Which segments light for each digit. Layout:
#
#     aaaa
#    f    b
#    f    b
#     gggg
#    e    c
#    e    c
#     dddd
#
_SEGMENTS = {
    0: "abcdef",
    1: "bc",
    2: "abged",
    3: "abgcd",
    4: "fgbc",
    5: "afgcd",
    6: "afgecd",
    7: "abc",
    8: "abcdefg",
    9: "abcdfg",
}


def draw_digit(display, x, y, w, h, t, digit, rgb=None):
    """Draw one 7-segment digit. `t` is segment thickness."""
    segs = _SEGMENTS.get(digit)
    if segs is None:
        return
    hh = h // 2
    inner_w = w - 2 * t
    up_len = hh - t - t // 2
    lo_len = h - (hh + t // 2) - t

    if rgb is not None:
        display.use(rgb)

    if "a" in segs:
        display.rect(x + t, y, inner_w, t)
    if "g" in segs:
        display.rect(x + t, y + hh - t // 2, inner_w, t)
    if "d" in segs:
        display.rect(x + t, y + h - t, inner_w, t)
    if "f" in segs:
        display.rect(x, y + t, t, up_len)
    if "b" in segs:
        display.rect(x + w - t, y + t, t, up_len)
    if "e" in segs:
        display.rect(x, y + hh + t // 2, t, lo_len)
    if "c" in segs:
        display.rect(x + w - t, y + hh + t // 2, t, lo_len)


def draw_number(display, x, y, w, h, t, value, gap=2, rgb=None):
    """Draw a non-negative integer, most-significant digit first.

    Returns the x coordinate just past the last digit.
    """
    s = f"{int(value)}"
    for ch in s:
        draw_digit(display, x, y, w, h, t, int(ch), rgb)
        x += w + gap
    return x - gap


def number_width(n_digits, w, gap=2):
    return n_digits * w + (n_digits - 1) * gap


def draw_bar(display, x, y, w, h, ratio, rgb=None, bg_rgb=None):
    """A draining bar. `ratio` 1.0 = full, 0.0 = empty.

    Drains toward the *left* so the remaining green is always adjacent to the
    digits, and the screen reaches a fully green end state at zero.
    """
    if ratio < 0.0:
        ratio = 0.0
    elif ratio > 1.0:
        ratio = 1.0
    if bg_rgb is not None:
        display.rect(x, y, w, h, bg_rgb)
    filled = int(w * ratio + 0.5)
    if filled > 0:
        display.rect(x, y, filled, h, rgb)
    return filled


def draw_bg_bar(display, x, y, w, h, ratio, rgb=None, dim_rgb=None):
    """Layout B: the whole background is the bar, draining left -> right.

    Nothing special beyond a full-height bar; provided as a named alternative
    so both candidate layouts can be prototyped on the bench.
    """
    return draw_bar(display, x, y, w, h, ratio, rgb=rgb, bg_rgb=dim_rgb)
