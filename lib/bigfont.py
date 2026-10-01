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

# The same five-row glyphs, drawn one pixel in from the top and bottom (rows
# 1..9 of the 11 px panel). The idle banner uses this: it scrolls across the
# whole width, so it passes through x=0, and the power lamp lives at (0, 0) -
# the border is what keeps the lamp lit (a full-height glyph would overdraw it
# as the text scrolls past). The outline rows keep their 2 px and the middle row
# gives up its third pixel, so the block stays solid where the eye reads it and
# the panel is never more than one pixel of margin off full height.
INSET_ROW_Y = (1, 3, 5, 6, 8)  # top edge of each row, one row down
INSET_ROW_H = (2, 2, 1, 2, 2)  # its height; the sum is HEIGHT - 2 (9)
INSET_TOP = 1  # px of border above the ink; the same is left below

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
    # Digits and a minus, so a bare number can be drawn full height. They were
    # added for the idle screen's Celsius reading ("18C", "-5C"); they use the
    # same fat-pixel grid as the letters, so a number sits with a label.
    "0": ("111", "101", "101", "101", "111"),
    "1": ("010", "110", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"),
    "3": ("111", "001", "111", "001", "111"),
    "4": ("101", "101", "111", "001", "001"),
    "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"),
    "7": ("111", "001", "001", "010", "010"),
    "8": ("111", "101", "111", "101", "111"),
    "9": ("111", "101", "111", "001", "111"),
    "-": ("000", "000", "111", "000", "000"),
    "!": ("010", "010", "010", "000", "010"),
    # Punctuation, so a message a parent types arrives as typed rather than
    # losing every stop and mark. Most are narrow - one or two logical pixels -
    # so a full stop or a comma does not open a canyon between the words; the
    # marks that need the height and the diagonal (?, /, the brackets) keep
    # three columns. The advance comes from the row width (glyph_cols), so a
    # narrow glyph advances less and the word stays tight.
    ".": ("0", "0", "0", "0", "1"),
    ",": ("0", "0", "0", "1", "1"),
    "'": ("1", "1", "0", "0", "0"),
    '"': ("101", "101", "000", "000", "000"),
    ":": ("0", "1", "0", "1", "0"),
    ";": ("0", "1", "0", "1", "1"),
    "?": ("111", "001", "011", "000", "010"),
    "(": ("01", "10", "10", "10", "01"),
    ")": ("10", "01", "01", "01", "10"),
    "/": ("001", "001", "010", "100", "100"),
    "&": ("011", "100", "010", "101", "011"),
    "+": ("000", "010", "111", "010", "000"),
    "=": ("000", "111", "000", "111", "000"),
    "*": ("101", "010", "101", "000", "000"),
    "#": ("101", "111", "101", "111", "101"),
    "%": ("101", "001", "010", "100", "101"),
    "_": ("000", "000", "000", "000", "111"),
    # The rarer marks: symbols and the brackets. Kept for completeness so that
    # whatever a parent types shows as something; the two non-ASCII keys are
    # written as escapes so this file stays ASCII for the board.
    "@": ("111", "101", "111", "100", "111"),
    "$": ("111", "110", "111", "011", "111"),
    "^": ("010", "101", "000", "000", "000"),
    "~": ("000", "011", "110", "000", "000"),
    "{": ("011", "010", "110", "010", "011"),
    "}": ("110", "010", "011", "010", "110"),
    "[": ("11", "10", "10", "10", "11"),
    "]": ("11", "01", "01", "01", "11"),
    "<": ("001", "010", "100", "010", "001"),
    ">": ("100", "010", "001", "010", "100"),
    "\u00a7": ("111", "100", "111", "001", "111"),  # section sign
    "\u2026": ("00000", "00000", "00000", "00000", "10101"),  # ellipsis
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


def _draw(display, x, y, s, row_y, row_h, rgb):
    """Draw `s` on the given row layout. Returns the x past the last glyph."""
    if rgb is not None:
        display.use(rgb)
    for ch in s:
        rows = GLYPHS.get(ch.upper())
        if rows is not None:
            for r in range(5):
                ry = y + row_y[r]
                rh = row_h[r]
                row = rows[r]
                for c in range(len(row)):
                    if row[c] == "1":
                        display.rect(x + c * PIX_W, ry, PIX_W, rh)
        x += glyph_cols(ch) * PIX_W + GAP
    return x - GAP


def draw_text(display, x, y, s, rgb=None):
    """Draw `s` with its top-left at (x, y). Returns the x past the last glyph.

    Fills the panel height. Unknown characters are skipped (but still
    advance), so a stray character never takes the panel down.
    """
    return _draw(display, x, y, s, ROW_Y, ROW_H, rgb)


def draw_text_inset(display, x, y, s, rgb=None):
    """Draw `s` one pixel in from the top and bottom (rows 1..9). Returns x.

    Same glyphs and advance as `draw_text`, laid out one pixel smaller, for a
    caller that must keep the panel's edge pixels clear - the scrolling idle
    banner, because its own ink passes over the power lamp at (0, 0). `y` is the
    top of the block; the ink begins at y + INSET_TOP.
    """
    return _draw(display, x, y, s, INSET_ROW_Y, INSET_ROW_H, rgb)
