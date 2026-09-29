"""Animated pictures for the PROMPT and HANDOFF screens.

Each icon is a list of 1-bit *frames*, 10 px wide and 11 px tall (the full
height of the panel). Animation is just cycling the frame on a timer - the
cheapest thing that makes a shape read as alive, with no per-pixel maths at
runtime.

Frames are plain strings of 'X' and '.', so adding or redrawing an icon is an
edit here and nothing else. `draw_icon` run-length encodes each row, so a
solid run becomes one rectangle instead of N pixel writes.

Weather glyphs may also use extra ink symbols - 'O' (rim), 'L' (sunlit) and
'S' (shade) - see _draw_frame and the shaded `cloud` below.
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


# -- weather pictures --------------------------------------------------------
#
# The idle screen's weather glyphs: 11x11 frames per condition from
# lib/weather.py, in the same 'X'/'.' form the routine icons use and drawn by
# the same run-length drawer, with 'O' available for a second (rim) ink on
# `cloud` - see _CLOUD and _draw_frame. Most conditions are ONE static frame -
# the idle panel is furniture and should mostly be still. `rain` is the
# exception: it carries three frames so the drops visibly fall, because a still
# frame of drops on a cloud did not read as rain (see _rain_frame).
#
# Sky is drawn as a FILLED silhouette rather than an outline: at 11 px, a
# silhouette reads from across the room where thin lines do not. The rule for
# every cloud is the same - a DEAD FLAT base with lobes on top. A cloud whose
# silhouette tapers at top AND bottom is an oval, and an oval reads as a
# teardrop, not a cloud (lib/icons.py has the scars: the idle panel showed a
# "big drop" for the `cloud` condition until the base was squared off below).
# Like `cloud`, the small glyphs are SHADED - the same 'L' lit top / 'X' body /
# 'S' shade bands, so the whole row of icons reads as lit-from-above. 'O' is
# the ACCENT ink here (the sun, the bolt, the drops, the flakes).
_CLOUD_TOP = (
    "...LL.LL...",
    "..LLLLLLL..",
    ".XXXXXXXXX.",
    "XXXXXXXXXXX",
    "SSSSSSSSSSS",
    "SSSSSSSSSSS",
)


_SUN = (
    ".....L.....",
    ".....L.....",
    "..L.LLL.L..",
    "...LLLLL...",
    "..LLLLLLL..",
    "XXXXXXXXXXX",
    "..SSSSSSS..",
    "...SSSSS...",
    "..S.SSS.S..",
    ".....S.....",
    ".....S.....",
)

# Sun in the accent ink (amber), cloud in the body/lit/shade greys.
_PARTLY = (
    "..O........",
    ".OOO.......",
    "OOOOO......",
    ".OOO.......",
    "..O..LLL...",
    "....LLLLL..",
    "...LLLLLLL.",
    "..XXXXXXXXX",
    ".XXXXXXXXXX",
    "XXXXXXXXXXX",
    "SSSSSSSSSSS",
)

# -- the night sky -----------------------------------------------------------
#
# After sunset the panel must NOT draw the sun: a clear night under a gold disc
# is simply the wrong sky, and it is what the panel did until now (the `sun`
# condition is only ever "code 0", which Open-Meteo reports all night too). So
# the two conditions whose glyph is a LIGHT SOURCE - `sun` and `partly` - swap
# their sun for a moon once `is_day` is 0 (see ambient.NIGHT_GLYPHS). Every
# other condition (cloud, rain, snow, fog, thunder) is already honest at night
# and keeps its glyph.
#
# The night glyphs use the same ink vocabulary as the day set, so no new drawer
# is needed: 'X' moon body, 'L' the moon's lit limb, 'S' its shaded inner edge,
# 'O' the stars (the ACCENT ink - the one colour that is not the moon's).
#
# A word on the STAR INK: it is single pixels on purpose. A star is a point,
# and at 11 px a point is exactly what it should be - the "no single-column
# spire" rule is about a cloud APEX reading as an antenna, not about stars.
# They are scattered rather than gridded so the field does not read as noise.
_NIGHT = (
    "...........",
    "....LS...O.",
    "...LXS..O..",
    "...LXS.....",
    "..LXS.O....",
    "..LXS......",
    "..LXS.....O",
    "...LXS.....",
    "...LXS..O..",
    "....LS.....",
    "...........",
)

# The same crescent, small, where `partly`'s sun used to sit - the cloud below
# is untouched, so a partly-cloudy night is a moon behind a cloud rather than a
# second moon-and-stars. The moon here is ALL accent ink: the 'L'/'S' pens in
# this glyph belong to the cloud, so the moon cannot borrow them.
_PARTLY_NIGHT = (
    "..OO.......",
    ".OOO.......",
    ".OO........",
    ".OOO.......",
    "..OO.LLL...",
    "....LLLLL..",
    "...LLLLLLL.",
    "..XXXXXXXXX",
    ".XXXXXXXXXX",
    "XXXXXXXXXXX",
    "SSSSSSSSSSS",
)

# The cloud is the only SHADED glyph. Its shape and looks are lifted from
# classic pixel-art cloud icons (the blocky, stair-stepped kind used for game
# skies): a wide silhouette of uneven TERRACES - not smooth curves - lit from
# above, with the underside in shadow. The extra ink symbols are what carry
# that:
#
#   'X'  BODY    the mid tone (`ambient.CLOUD_BODY_RGB`)
#   'L'  LIT     sunlit top: every up-facing edge and the cells just under it
#   'S'  SHADE   the underside band along the base
#   'O'  RIM     a thin grey edge on the vertical flanks
#
# WHY SHADING. A dark panel shows a cloud by its TONE, not its outline. The
# earlier drafts were one flat blue with a grey outline, and on the wall they
# read as a blob or a hill. Splitting the silhouette into lit-top / mid-body /
# shaded-underside makes it read as a cloud at a glance: bright where the sky
# lights it, dark underneath where it does not. The rim is kept (it was liked)
# but is now just a quiet edge on the flanks rather than the brightest thing
# in the glyph.
#
# SHAPE. Terraces of uneven size: a taller left bump, a smaller right bump,
# merged into a broad base. Two EQUAL humps read as a sofa/lips, so the bumps
# differ in height AND width. The whole thing is 22 px wide (not 11) and wider
# than it is tall, so it centres itself beside the full-height digits. The
# bottom row steps in one column per side so the base is not a sheer wall.
#
# THE UNDERSIDE IS A LENS, NOT A BAND. The shade used to fill the bottom two
# rows edge to edge, and on the panel that read as a slab the cloud was sitting
# on - a hard dark bar under a lit shape, more like a waterline than weather.
# Real cloud shading is graded: darkest under the thickest part, thinning to
# nothing where the cloud thins out. So the shade steps outward as it descends
# - 4 cells wide, then 8, then 16 in a 20-cell base - and stops short of the
# silhouette on every row, leaving body ink at the flanks. The stepping IS the
# blend: three widths down three rows reads as a gradient at viewing distance,
# and the flanks stay mid-tone instead of dropping straight to shadow.
#
# A single-column spire is forbidden: one column of ink above its neighbours
# reads as an antenna, so every apex is at least two columns wide.
_CLOUD = (
    "......................",
    "......................",
    "....LLLL..............",
    "...LLLLLLL....LLL.....",
    "...OXXXXLO..LLLLLLL...",
    "...OXXXXXXLLLLXXXLO...",
    "LLLXXXXXXXLLXXXXXXXLLL",
    "OLLXXXXXXSSSSXXXXXXLLO",
    ".XXXXXXSSSSSSSSXXXXXX.",
    ".XXSSSSSSSSSSSSSSSSXX.",
    "......................",
)

_FOG = (
    "...........",
    "..LLLLLLLL.",
    "...........",
    "XXXXXXXXX..",
    "...........",
    "..XXXXXXXX.",
    "...........",
    "SSSSSSSSS..",
    "...........",
    "..SSSSSSSS.",
    "...........",
)

# Rain is the one weather glyph that ANIMATES. A single static frame read as
# "a blob with dots" on the wall, not as rain: the drops sat inside the cloud
# silhouette instead of falling out of it. So the cloud is shortened to the top
# six rows and three one-pixel streams fall through the five rows beneath it,
# one row per frame, cycling 0..2 - a short, slow shower rather than a flicker.
_RAIN_CLOUD = _CLOUD_TOP


_RAIN_STREAM = "..O..O..O.."
_RAIN_BLANK = "..........."


def _rain_frame(offset):
    """The rain cloud with its falling streams `offset` rows lower (0..2).

    Three one-pixel streams, each three rows tall, slide down one row per
    frame through the five rows under the cloud; wrapping back to the top is
    the drop leaving the cloud again. Every row is the full icon width, so a
    frame is a legal 11x11 blit.
    """
    rows = list(_RAIN_CLOUD)
    for r in range(5):
        rows.append(_RAIN_STREAM if offset <= r < offset + 3 else _RAIN_BLANK)
    return tuple(rows)


_RAIN = (_rain_frame(0), _rain_frame(1), _rain_frame(2))

_SNOW = _CLOUD_TOP + (
    "..O.O.O.O..",
    "...........",
    ".O.O.O.O.O.",
    "...........",
    "..O.O.O.O..",
)

_THUNDER = _CLOUD_TOP + (
    "...OO......",
    "..OO.......",
    "..OOOOO....",
    "....OO.....",
    "....O......",
)

# Keyed by the condition names lib/weather.py returns. `cloud` is the fallback
# for anything unknown, so it is always present.
WEATHER_ICONS = {
    "sun": (_SUN,),
    "partly": (_PARTLY,),
    "cloud": (_CLOUD,),
    "fog": (_FOG,),
    "rain": _RAIN,  # already a tuple of frames - see _rain_frame
    "snow": (_SNOW,),
    "thunder": (_THUNDER,),
    # The after-sunset substitutions for the two sunny conditions - see _NIGHT.
    # Keyed here so weather_icon_width / draw_weather_icon need no new API.
    "night": (_NIGHT,),
    "partly-night": (_PARTLY_NIGHT,),
}

WEATHER_ICON_W = 11

# How long each weather animation frame is held. Faster than the routine icons'
# FRAME_MS (400): at three frames it is a whole cycle in 0.9 s, which reads as
# a steady shower rather than a slow twitch - and it is still two orders of
# magnitude slower than the render loop, so it costs nothing.
WEATHER_FRAME_MS = 300


def weather_icon_width(name):
    """How wide weather icon `name` draws, or 0 if it is unknown."""
    frames = WEATHER_ICONS.get(name)
    return len(frames[0][0]) if frames else 0


def draw_weather_icon(display, condition, x, y, age_ms=0, rgb=None, rim_rgb=None,
                      lit_rgb=None, shade_rgb=None):
    """Draw the weather icon for `condition` at (x, y), animated by `age_ms`.

    A single-frame condition ignores `age_ms`; `rain` advances through its
    frames on the shared WEATHER_FRAME_MS cadence.

    `rgb` is the body colour; `rim_rgb` / `lit_rgb` / `shade_rgb` are the pens
    for the 'O' / 'L' / 'S' ink symbols. Only `cloud` uses the extra symbols -
    every other glyph is all-'X', so their extra pens are irrelevant.
    """
    frames = WEATHER_ICONS.get(condition) or WEATHER_ICONS["cloud"]
    frame = frames[(age_ms // WEATHER_FRAME_MS) % len(frames)]
    _draw_frame(display, frame, x, y, rgb, rim_rgb, lit_rgb, shade_rgb)


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
    _draw_frame(display, frame, x, y, rgb)


def _draw_frame(display, frame, x, y, rgb=None, rim_rgb=None, lit_rgb=None,
                shade_rgb=None):
    """Blit one frame, run-length encoding each row so a solid run becomes one
    rectangle instead of N pixel writes.

    Ink symbols: 'X' body (pen `rgb`), 'O' rim, 'L' lit, 'S' shade - each extra
    pen falls back to `rgb` when not supplied. Runs of one symbol share a
    rectangle, and the pen is only reset when it actually changes, so
    single-ink glyphs cost exactly what they did before.
    """
    pens = {
        "X": rgb,
        "O": rim_rgb if rim_rgb is not None else rgb,
        "L": lit_rgb if lit_rgb is not None else rgb,
        "S": shade_rgb if shade_rgb is not None else rgb,
    }
    pen = None
    for r, row in enumerate(frame):
        c = 0
        n = len(row)
        while c < n:
            ch = row[c]
            if ch not in pens:
                c += 1
                continue
            run = 1
            while c + run < n and row[c + run] == ch:
                run += 1
            want = pens[ch]
            if want is not None and want != pen:
                display.use(want)
                pen = want
            display.rect(x + c, y + r, run, 1)
            c += run
