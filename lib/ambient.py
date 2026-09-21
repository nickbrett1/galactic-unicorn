"""Idle rendering: a dim clock, degrading to a single slow-breathing pixel.

AMBIENT is a first-class state, not an afterthought: the display is
shared-room furniture and should read as an object, not as a screen.

The clock is best-effort. If NTP never landed, the screen shows a slow
breathing status pixel instead, so a network problem looks like "quiet",
never like "broken".

One thing here is NOT best-effort: the power lamp in the top-left corner
(lib/display.py: POWER_PIXEL). It is lit whenever the unit is on and no routine
has been selected, so "is this thing even powered?" no longer has to be
answered by looking at the router. It shares the dark-room rule with everything
else - in a dark room routine.tick() blanks the panel outright and the lamp
goes with it, because furniture should not glow in a child's bedroom.
"""

import time

CLOCK_RGB = (0, 66, 104)
STATUS_RGB = (0, 48, 96)
BREATH_PERIOD_MS = 4000

# Rebuild the clock string at most this often. The display only shows HH:MM,
# so once a second is plenty - and it must NOT be per frame (see _draw_clock).
CLOCK_REBUILD_MS = 1000


class Ambient:
    def __init__(self, display, config):
        self.display = display
        self.config = config
        self.ntp_ok = False
        self._label = None
        self._label_w = 0
        self._label_at = 0

    def _draw_clock(self):
        d = self.display
        # Do NOT build the time string every frame. At ~50 fps that was a
        # float, an 8-tuple and a string per frame - about 3.4 KB/s of garbage
        # - which drove the heap to ~1 KB free at the bottom of each GC cycle
        # and eventually killed the loop ("goes to sleep after a while"). The
        # clock only changes once a minute, so rebuild at most once a second.
        now_ms = time.ticks_ms()
        stale = time.ticks_diff(now_ms, self._label_at) >= CLOCK_REBUILD_MS
        if self._label is None or stale:
            t = time.localtime(time.time() + self.config.UTC_OFFSET_S)
            self._label = f"{t[3]:02d}:{t[4]:02d}"
            self._label_w = d.text_width(self._label, scale=1)
            self._label_at = now_ms
        x = (d.width - self._label_w) // 2
        y = (d.height - 8) // 2
        d.text(self._label, x, y, rgb=CLOCK_RGB, scale=1)

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
        # The one thing here that is not a function of the network: a corner
        # lamp saying "this unit has power", lit for as long as no routine has
        # been selected. The clock below it is best-effort and the breathing
        # pixel below THAT is what a failed NTP looks like - so on a bad night
        # the status pixel is ambiguous (working radio? dead one? board even
        # on?) and this is the pixel that is not. It is also the frame boot.py
        # drew before anything else and main.py kept lit through the banner, so
        # the panel is never once completely dark between power-on and the
        # countdown - not for a boot, and not for a clock that did not sync.
        d.power_pixel()
        if self.ntp_ok:
            self._draw_clock()
        else:
            self._draw_status_pixel(phase_ms)
        d.update()
