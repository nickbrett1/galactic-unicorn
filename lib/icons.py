"""Animated pictures for the PROMPT and HANDOFF screens.

Each icon is a list of 1-bit *frames*, 10 px wide and 11 px tall (the full
height of the panel). Animation is just cycling the frame on a timer - the
cheapest thing that makes a shape read as alive, with no per-pixel maths at
runtime.

Frames are plain strings of 'X' and '.', so adding or redrawing an icon is an
edit here and nothing else. `draw_icon` run-length encodes each row, so a
solid run becomes one rectangle instead of N pixel writes.
"""

FRAME_MS = 400  # how long each frame is held
ICON_W = 10
ICON_H = 11

def _falling(base, below, count=3, step=2):
    """Frames of `base` over `below`, with `below` scrolled down `step` rows
    per frame and wrapping - i.e. water falling from the head/tap."""
    out = []
    n = len(below)
    for f in range(count):
        off = (f * step) % n
        out.append(base + tuple(below[(i - off) % n] for i in range(n)))
    return tuple(out)


def _overlay(*layers):
    """Pixel-wise OR of equal-sized layers (each a tuple of rows).

    Used to drop a moving layer (bubbles) onto a static one (the tub) without
    hand-merging the two row by row.
    """
    return tuple(
        "".join("X" if "X" in cells else "." for cells in zip(*rows))
        for rows in zip(*layers)
    )


# A tap: knob top-right, stem, arm to the left, water falling from the spout.
_TAP = (
    "......XXX.",
    "......X...",
    "......X...",
    "..XXXXXX..",
)
_TAP_WATER = (
    "..XX......",
    "..XX......",
    "..........",
    "..XX......",
    "..XX......",
    "..........",
    "..........",
)
TAP = _falling(_TAP, _TAP_WATER)

# A shower head with the streams lined up under it: cols 1-2, 4-5, 7-8, all
# inside the head (cols 1-8), each stream at its own height.
_SHOWER_HEAD = (
    ".XXXXXXXX.",
    ".XXXXXXXX.",
)
_SHOWER_WATER = (
    ".XX.......",
    ".XX.......",
    "..........",
    "....XX....",
    "....XX....",
    "..........",
    ".......XX.",
    ".......XX.",
    "..........",
)
SHOWER = _falling(_SHOWER_HEAD, _SHOWER_WATER)

# A bath, side-on: a wall pipe on the right, a spout over the tub, dripping
# into an open-topped basin. The tub's mouth is deliberately left open (the
# walls only rise to the rim) so it reads as a bath and not a closed crate -
# the earlier version put the tap *on* a sealed rim, which looked like a box
# with a post on it.
#
# Only two small things move: the drip (rows 3-4, repeating the fall) and the
# bubbles in the water (rows 6-8, rotating upward). The tub outline is static,
# so the silhouette holds still at a distance.
_BATH_PIPE = (
    ".........X",  # pipe, right-hand end
    ".........X",
    "......XXXX",  # spout arm reaching left, over the tub
)
_BATH_RIM = ("XXXXXXXXXX",)
_BATH_BOTTOM = (
    ".XXXXXXXX.",  # basin floor, inset for a rounded look
    ".X......X.",  # feet
)
# The drip: falls from the spout mouth (col 6) then vanishes into the water.
_BATH_DRIP = (
    ("......X...", ".........."),
    ("..........", "......X..."),
    ("..........", ".........."),
)
# Bubbles between the walls, rotated upward a row per frame so they rise.
_BATH_BUBBLE_ROWS = ("X.X...X..X", "X........X", "X..X...X.X")
_BATH_BUBBLES = tuple(
    tuple(_BATH_BUBBLE_ROWS[(r + f) % 3] for r in range(3)) for f in range(3)
)
BATH = tuple(
    _BATH_PIPE + drip + _BATH_RIM + bubbles + _BATH_BOTTOM
    for drip, bubbles in zip(_BATH_DRIP, _BATH_BUBBLES)
)

# A book, gently rising and falling - for book time.
BOOK = (
    (
        ".XXXXXXXX.",
        ".X......X.",
        ".X.XXXX.X.",
        ".X.XXXX.X.",
        ".X......X.",
        ".X.XXXX.X.",
        ".X.XXXX.X.",
        ".X......X.",
        ".XXXXXXXX.",
        "..........",
        "..........",
    ),
    (
        "..........",
        ".XXXXXXXX.",
        ".X......X.",
        ".X.XXXX.X.",
        ".X.XXXX.X.",
        ".X......X.",
        ".X.XXXX.X.",
        ".X.XXXX.X.",
        ".X......X.",
        ".XXXXXXXX.",
        "..........",
    ),
    (
        "..........",
        "..........",
        ".XXXXXXXX.",
        ".X......X.",
        ".X.XXXX.X.",
        ".X.XXXX.X.",
        ".X......X.",
        ".X.XXXX.X.",
        ".X.XXXX.X.",
        ".X......X.",
        ".XXXXXXXX.",
    ),
)

# A brush, bristles scrubbing side to side - for cleanup.
BRUSH = (
    (
        "....XX....",
        "....XX....",
        "....XX....",
        "....XX....",
        "...XXXX...",
        "..XXXXXX..",
        ".XXXXXXXX.",
        "XXXXXXXXXX",
        "X.X.X.X.X.",
        "X.X.X.X.X.",
        ".X.X.X.X..",
    ),
    (
        "....XX....",
        "....XX....",
        "....XX....",
        "....XX....",
        "...XXXX...",
        "..XXXXXX..",
        ".XXXXXXXX.",
        "XXXXXXXXXX",
        ".X.X.X.X.X",
        ".X.X.X.X.X",
        "X.X.X.X.X.",
    ),
)

ICONS = {
    "bath": BATH,
    "tap": TAP,
    "shower": SHOWER,
    "book": BOOK,
    "brush": BRUSH,
}


def draw_icon(display, name, x, y, age_ms, rgb=None):
    """Draw icon `name` at (x, y), advancing its animation by `age_ms`."""
    frames = ICONS.get(name)
    if not frames:
        return
    frame = frames[(age_ms // FRAME_MS) % len(frames)]
    if rgb is not None:
        display.use(rgb)
    for r, row in enumerate(frame):
        c = 0
        n = len(row)
        while c < n:
            if row[c] == "X":
                run = 1
                while c + run < n and row[c + run] == "X":
                    run += 1
                display.rect(x + c, y + r, run, 1)
                c += run
            else:
                c += 1
