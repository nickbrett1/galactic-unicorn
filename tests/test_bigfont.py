#!/usr/bin/env python3
"""Regression tests for the block font (lib/bigfont.py). Host-only, no board.

    python3 tests/test_bigfont.py

bigfont is what makes text big enough to read across a room: the boot HELLO,
the weather's temperature label, and the idle banner. It is a hand-drawn
alphabet of five rows of logical pixels, so its failure modes are all authoring
slips - a glyph with the wrong number of rows, rows of different widths (which
would corrupt both the advance and the drawing), an unknown character silently
dropped, or an inset glyph that touches the panel's edge rows and overdraws the
power lamp at (0, 0).

Nothing here needs a panel: the font is pure geometry, and draw_text only calls
`display.use` and `display.rect`.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

import bigfont

HEIGHT = 11


class Rec:
    """A display that remembers the rectangles drawn on it, as a bitmap."""

    def __init__(self, width=200):
        self.width = width
        self.rows = [[0] * width for _ in range(HEIGHT)]

    def use(self, rgb):
        pass

    def rect(self, x, y, w, h, rgb=None):
        for dx in range(int(w)):
            for dy in range(int(h)):
                xx, yy = int(x) + dx, int(y) + dy
                if 0 <= xx < self.width and 0 <= yy < HEIGHT:
                    self.rows[yy][xx] = 1

    def ink_rows(self):
        return [y for y in range(HEIGHT) if any(self.rows[y])]


def report(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL':<4} {name:<52} {detail}")
    return ok


def case_every_glyph_is_five_uniform_rows():
    """A glyph is five rows, and all five rows are the same non-zero width."""

    def body():
        bad = []
        for ch, rows in bigfont.GLYPHS.items():
            widths = {len(r) for r in rows}
            if len(rows) != 5 or len(widths) != 1 or 0 in widths:
                bad.append((ch, sorted(widths)))
        return report("every glyph is five uniform-width rows", not bad, f"bad={bad}")

    return body()


def case_glyphs_use_only_ink_and_blank():
    """The grid is '1' (ink) and '0' (blank), nothing else."""

    def body():
        chars = set()
        for rows in bigfont.GLYPHS.values():
            for r in rows:
                chars |= set(r)
        return report(
            "glyphs use only '0' and '1'", chars <= {"0", "1"}, f"chars={sorted(chars)}"
        )

    return body()


def case_draw_advance_matches_text_width():
    """draw_text's return value equals text_width - the advance is one truth."""

    def body():
        bad = []
        for s in ("HELLO", "16C", "Good night!", "5:30?", '"Hi", & (x)'):
            end = bigfont.draw_text(Rec(), 0, 0, s)
            if end != bigfont.text_width(s):
                bad.append((s, end, bigfont.text_width(s)))
        return report("the drawn advance matches text_width", not bad, f"bad={bad}")

    return body()


def case_inset_leaves_the_edge_rows_clear():
    """The inset layout inks rows 1..9, so (0, 0) and row 10 stay free."""

    def body():
        d = Rec()
        bigfont.draw_text_inset(d, 0, 0, "HELLO 5:30?")
        rows = d.ink_rows()
        return report(
            "draw_text_inset leaves rows 0 and 10 clear",
            bool(rows) and min(rows) == 1 and max(rows) == HEIGHT - 2,
            f"ink_rows={rows[:1]}..{rows[-1:]}",
        )

    return body()


def case_full_text_fills_the_height():
    """The full layout is the panel's height, edge to edge."""

    def body():
        d = Rec()
        bigfont.draw_text(d, 0, 0, "HELLO")
        rows = d.ink_rows()
        return report(
            "draw_text fills the panel height",
            bool(rows) and min(rows) == 0 and max(rows) == HEIGHT - 1,
            f"ink_rows={rows[:1]}..{rows[-1:]}",
        )

    return body()


def case_punctuation_is_not_dropped():
    """Every punctuation glyph draws some ink - none is a silent blank."""

    def body():
        marks = ".,?!:;'\"()/&+=*#%_@$^~{}[]<>" + "\u00a7" + "\u2026"
        missing = []
        for ch in marks:
            one = Rec(width=20)
            bigfont.draw_text_inset(one, 0, 0, ch)
            if not one.ink_rows():
                missing.append(ch)
        return report(
            "every punctuation mark draws some ink", not missing, f"missing={missing}"
        )

    return body()


def main():
    results = [
        case_every_glyph_is_five_uniform_rows(),
        case_glyphs_use_only_ink_and_blank(),
        case_draw_advance_matches_text_width(),
        case_inset_leaves_the_edge_rows_clear(),
        case_full_text_fills_the_height(),
        case_punctuation_is_not_dropped(),
    ]
    print()
    print(f"{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
