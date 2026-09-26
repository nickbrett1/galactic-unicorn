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
ICON_W = 10  # the usual icon width; the book is wider, so ask icon_width()
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

# A book, open and face-on: a cover edge top and bottom, and pages either side
# of a centre gutter with text lines on them. The blank column pair down the
# middle is what makes it read as *open* - the earlier version was one nested
# rectangle, which looked like the book had been turned ninety degrees.
#
# This one is deliberately wider than the other icons (ICON_W + 4): at 10 px
# the pages were barely wide enough to hold a line of text, and the lines ran
# straight into the cover edge. The extra columns buy a blank margin either
# side of the gutter, so each line sits *inside* its page and reads as text
# rather than as part of the border.
_BOOK_W = ICON_W + 4  # 14
_BOOK_COVER = "X" * _BOOK_W
_BOOK_PAGE = "X" + "." * (_BOOK_W - 2) + "X"
_BOOK_LEFT = "X" + "." + "XXXX" + ".." + "...." + "." + "X"
_BOOK_RIGHT = "X" + "." + "...." + ".." + "XXXX" + "." + "X"
_BOOK_BOTH = "X" + "." + "XXXX" + ".." + "XXXX" + "." + "X"
_BOOK_TEXT_ROWS = (2, 4, 6, 8)


def _book_frame(left, right):
    """The book mid-read: `left` lines written on the left page, `right` on
    the right page. The reader fills the left page before starting the right -
    lines appear in reading order."""
    rows = []
    for r in range(ICON_H):
        if r in (0, ICON_H - 1):
            rows.append(_BOOK_COVER)
        elif r in _BOOK_TEXT_ROWS:
            band = _BOOK_TEXT_ROWS.index(r)
            if band < left and band < right:
                rows.append(_BOOK_BOTH)
            elif band < left:
                rows.append(_BOOK_LEFT)
            elif band < right:
                rows.append(_BOOK_RIGHT)
            else:
                rows.append(_BOOK_PAGE)
        else:
            rows.append(_BOOK_PAGE)
    return tuple(rows)


# Eight frames: down the left page a line at a time, then down the right.
BOOK = tuple(
    _book_frame(left, right)
    for left, right in ((1, 0), (2, 0), (3, 0), (4, 0), (4, 1), (4, 2), (4, 3), (4, 4))
)

# Tidying: little boxes dropped into one big open box. The big box is drawn
# with 2 px walls so it reads as a container even at a glance, and the small
# box falls in on a repeating cycle - the story is "put it away", not just
# "boxes exist".
_LITTLE_BOX = ("XXXX", "X..X", "XXXX")
_BIG_BOX = tuple(
    "XX......XX"
    if 5 <= row <= 9
    else ("X" * ICON_W if row == 10 else "." * ICON_W)
    for row in range(ICON_H)
)


def _place(x, row):
    """One icon row positioned at column x, padded out to the icon width."""
    return "." * x + row + "." * (ICON_W - x - len(row))


def _falling_box(top):
    """A little box as a full-size layer, its first row at `top`."""
    rows = ["." * ICON_W] * top
    rows.extend(_place(3, row) for row in _LITTLE_BOX)
    rows.extend(["." * ICON_W] * (ICON_H - len(rows)))
    return tuple(rows)


# Four frames: high, lower, at the mouth, then settled in the bottom.
BOXES = tuple(_overlay(_BIG_BOX, _falling_box(top)) for top in (0, 2, 4, 7))

ICONS = {
    "bath": BATH,
    "tap": TAP,
    "shower": SHOWER,
    "book": BOOK,
    # The wire/server name for the third routine's symbol (routines.json).
    # Was "boxes"; renamed so the panel draws the routine the server sends.
    "toy-box": BOXES,
}


def icon_width(name):
    """How wide icon `name` draws. Icons are not all the same width, so the
    PROMPT layout must ask rather than assume ICON_W."""
    frames = ICONS.get(name)
    return len(frames[0][0]) if frames else 0


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
