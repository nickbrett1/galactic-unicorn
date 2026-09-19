"""Idle rendering: a dim clock, degrading to a single slow-breathing pixel.

AMBIENT is a first-class state, not an afterthought: the display is
shared-room furniture and should read as an object, not as a screen.

The clock is best-effort. If NTP never landed, the screen shows a slow
breathing status pixel instead, so a network problem looks like "quiet",
never like "broken".
"""

import time

CLOCK_RGB = (0, 66, 104)
STATUS_RGB = (0, 48, 96)
BREATH_PERIOD_MS = 4000


class Ambient:
    def __init__(self, display, config):
        self.display = display
        self.config = config
        self.ntp_ok = False

    def _draw_clock(self):
        d = self.display
        t = time.localtime(time.time() + self.config.UTC_OFFSET_S)
        label = f"{t[3]:02d}:{t[4]:02d}"
        w = d.text_width(label, scale=1)
        x = (d.width - w) // 2
        y = (d.height - 8) // 2
        d.text(label, x, y, rgb=CLOCK_RGB, scale=1)

    def _draw_status_pixel(self, phase_ms):
        d = self.display
        # Triangle wave 0.25..1.0 - a slow breath, never a blink.
        half = BREATH_PERIOD_MS // 2
        p = phase_ms % BREATH_PERIOD_MS
        f = (p / float(half)) if p < half else (2.0 - p / float(half))
        f = 0.25 + 0.75 * f
        rgb = (int(STATUS_RGB[0] * f), int(STATUS_RGB[1] * f), int(STATUS_RGB[2] * f))
        d.rect(d.width // 2, d.height // 2, 1, 1, rgb)

    def draw(self, phase_ms):
        d = self.display
        d.clear()
        if self.ntp_ok:
            self._draw_clock()
        else:
            self._draw_status_pixel(phase_ms)
        d.update()
