"""A chunky block font that fills the panel height.

The built-in bitmap font is 8 px tall and thin: fine for the ambient clock,
too weedy to read across a room. These glyphs are drawn from "fat pixels" -
each logical pixel is a 2x2 block - so the letters are bold and 11 rows tall,
i.e. the full height of the panel. That is the largest text the hardware can
show, and it reads from the far side of the living room.

Glyphs are a classic blocky alphabet, three logical pixels wide by default. A
few letters (N) get a fourth column when three cannot make them legible. The
five rows are laid out with heights (2, 2, 3, 2, 2), which sums to exactly 11:
the extra pixel goes into the *middle* row, where nobody can see it, instead of
leaving a lopsided margin at the bottom (the thing that looked "off" about the
first pass).
"""

# -- on-screen geometry -----------------------------------------------------

PIX_W = 2  # a logical pixel is 2 px wide
ROW_Y = (0, 2, 4, 7, 9)  # top edge of each of the five logical rows
ROW_H = (2, 2, 3, 2, 2)  # its height; the sum is HEIGHT (11)
DEFAULT_COLS = 3  # glyphs are this many logical pixels wide unless they say otherwise
GAP = 1  # px between glyphs
HEIGHT = 11

# -- the alphabet -----------------------------------------------------------
# Each glyph is five rows of logical pixels; '1' is ink. Rows may be 3 or 4
# wide - the width is taken from the row itself. Deliberately sparse (blocky):
# these are signs, not a typeface.

GLYPHS = {
    "A": ("111", "101", "111", "101", "101"),
    "B": ("110", "101", "110", "101", "110"),
    "C": ("111", "100", "100", "100", "111"),
    "D": ("110", "101", "101", "101", "110"),
    "E": ("111", "100", "111", "100", "111"),
    "F": ("111", "100", "111", "100", "100"),
    "G": ("111", "100", "101", "101", "111"),
    "H": ("101", "101", "111", "101", "101"),
    "I": ("111", "010", "010", "010", "111"),
    "J": ("001", "001", "001", "101", "111"),
    "K": ("101", "101", "110", "101", "101"),
    "L": ("100", "100", "100", "100", "111"),
    # M and N carry a diagonal stroke, so they get a fourth column: at three
    # columns they collapse into the same H-like blob.
    "M": ("1001", "1111", "1001", "1001", "1001"),
    "N": ("1001", "1101", "1011", "1001", "1001"),
    "O": ("111", "101", "101", "101", "111"),
    "P": ("111", "101", "111", "100", "100"),
    "Q": ("111", "101", "101", "111", "001"),
    "R": ("111", "101", "110", "101", "101"),
    "S": ("111", "100", "111", "001", "111"),
    "T": ("111", "010", "010", "010", "010"),
    "U": ("101", "101", "101", "101", "111"),
    "V": ("101", "101", "101", "101", "010"),
    "W": ("101", "101", "111", "111", "101"),
    "X": ("101", "101", "010", "101", "101"),
    "Y": ("101", "101", "010", "010", "010"),
    "Z": ("111", "001", "010", "100", "111"),
    "!": ("010", "010", "010", "000", "010"),
    " ": ("000", "000", "000", "000", "000"),
}


def glyph_cols(ch):
    """Logical columns in glyph `ch` (3 or 4)."""
    rows = GLYPHS.get(ch.upper())
    return len(rows[0]) if rows else DEFAULT_COLS


def text_width(s, gap=GAP):
    """Width in pixels of `s` as drawn by draw_text."""
    if not s:
        return 0
    w = 0
    for ch in s:
        w += glyph_cols(ch) * PIX_W + gap
    return w - gap


def draw_text(display, x, y, s, rgb=None):
    """Draw `s` with its top-left at (x, y). Returns the x past the last glyph.

    Unknown characters are skipped (but still advance), so a stray character
    never takes the panel down.
    """
    if rgb is not None:
        display.use(rgb)
    for ch in s:
        rows = GLYPHS.get(ch.upper())
        if rows is not None:
            for r in range(5):
                ry = y + ROW_Y[r]
                rh = ROW_H[r]
                row = rows[r]
                for c in range(len(row)):
                    if row[c] == "1":
                        display.rect(x + c * PIX_W, ry, PIX_W, rh)
        x += glyph_cols(ch) * PIX_W + GAP
    return x - GAP
