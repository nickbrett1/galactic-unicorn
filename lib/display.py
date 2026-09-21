"""GalacticUnicorn + PicoGraphics wrapper, light-sensor auto-dim, pens.

THE MODULE-NAME SWITCH LIVES HERE AND NOWHERE ELSE.

The board's display module was renamed between firmware generations:

  * stock MicroPython 1.19.1 (2022-10-19, what this board shipped with)
    exposes it as ``galactic``
  * current ``pimoroni/unicorn`` builds very likely expose ``galactic_unicorn``

Verified on this board 2026-09-19 with a read-only probe:

    os.uname().machine == "Raspberry Pi Pico W with RP2040"
    import galactic          -> OK
    import galactic_unicorn  -> ImportError

So the primary import below is ``galactic``. Do NOT hand-write
``from galactic_unicorn import ...`` from the product name - that is a
plausible-looking guess that does not work on the firmware actually present.
If a reflash renames the module, the fallback catches it; check the import
immediately after flashing and, if the fallback is what fired, swap the two.
"""

try:
    from galactic import GalacticUnicorn  # stock 1.19.1 firmware
except ImportError:  # pragma: no cover - depends on firmware generation
    from galactic_unicorn import GalacticUnicorn  # newer pimoroni/unicorn builds

from picographics import DISPLAY_GALACTIC_UNICORN, PicoGraphics

WIDTH = GalacticUnicorn.WIDTH
HEIGHT = GalacticUnicorn.HEIGHT

# Switch pins, re-exported so button code never imports `galactic` directly.
SWITCH_A = GalacticUnicorn.SWITCH_A
SWITCH_B = GalacticUnicorn.SWITCH_B
SWITCH_C = GalacticUnicorn.SWITCH_C
SWITCH_D = GalacticUnicorn.SWITCH_D
SWITCH_SLEEP = GalacticUnicorn.SWITCH_SLEEP
SWITCH_VOLUME_UP = GalacticUnicorn.SWITCH_VOLUME_UP
SWITCH_VOLUME_DOWN = GalacticUnicorn.SWITCH_VOLUME_DOWN
SWITCH_BRIGHTNESS_UP = GalacticUnicorn.SWITCH_BRIGHTNESS_UP
SWITCH_BRIGHTNESS_DOWN = GalacticUnicorn.SWITCH_BRIGHTNESS_DOWN

# The four labelled buttons, front and centre. Prompt order A, B, C, D.
SWITCHES = {
    "A": SWITCH_A,
    "B": SWITCH_B,
    "C": SWITCH_C,
    "D": SWITCH_D,
}

# Colour ramp anchors. The COUNTDOWN is a traffic light (memo section 4a:
# green means go), and it ends on the brightest green we draw.
RED = (255, 32, 0)
AMBER = (255, 140, 0)
GREEN = (0, 255, 48)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
GREY = (40, 44, 52)

# The power lamp: ONE white LED in the top-left corner, lit whenever the unit
# is powered and no routine has been selected. It is the answer to a question
# the panel could not previously answer at all - "is this thing on?" - without
# being a screen: a single dim pixel in the corner reads as an object with a
# power light, which is what the thing is.
#
# Top-left is the one square nothing else uses: the clock, the breathing status
# pixel, the icon+label selection and the 34 px HELLO banner are all centred,
# and the banner's leftmost glyph starts at x=9. So this never collides, and it
# is the same corner boot.py lights before it does anything else (boot.py
# hard-codes the coordinate - it must not import this file, which arrives over
# the update channel it exists to repair).
POWER_PIXEL = (0, 0)
POWER_RGB = WHITE


def _lerp(a, b, t):
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    return int(a + (b - a) * t)


def lerp_rgb(c0, c1, t):
    """Interpolate between two (r, g, b) tuples."""
    return (_lerp(c0[0], c1[0], t), _lerp(c0[1], c1[1], t), _lerp(c0[2], c1[2], t))


def traffic_rgb(progress):
    """Traffic-light colour for a countdown that is `progress` (0..1) done.

    Red -> amber -> green, so the colour *ends* on green: the moment the
    routine begins is the most noticeable moment on the panel, which is the
    whole point of the countdown. (The first pass went blue -> teal -> green,
    which is far too similar at the low end to read as a gradient at all.)
    """
    if progress <= 0.0:
        return RED
    if progress >= 1.0:
        return GREEN
    if progress < 0.5:
        return lerp_rgb(RED, AMBER, progress / 0.5)
    return lerp_rgb(AMBER, GREEN, (progress - 0.5) / 0.5)


class Display:
    """Owns the panel and its pens; knows nothing about routines."""

    def __init__(self, config):
        self.config = config
        self.gu = GalacticUnicorn()
        self.graphics = PicoGraphics(display=DISPLAY_GALACTIC_UNICORN)
        self.width = WIDTH
        self.height = HEIGHT
        self._pen = None
        self._cache = {}
        self.set_brightness(config.BRIGHTNESS_AMBIENT)

    # -- pens ------------------------------------------------------------

    def pen(self, rgb):
        """Create (and cache) a pen for an (r, g, b) tuple."""
        key = (int(rgb[0]), int(rgb[1]), int(rgb[2]))
        p = self._cache.get(key)
        if p is None:
            p = self.graphics.create_pen(key[0], key[1], key[2])
            self._cache[key] = p
            if len(self._cache) > 64:
                self._cache = {key: p}
        return p

    def use(self, rgb):
        self._pen = self.pen(rgb)
        self.graphics.set_pen(self._pen)
        return self._pen

    # -- primitives ------------------------------------------------------

    def clear(self, rgb=BLACK):
        self.use(rgb)
        self.graphics.clear()

    def rect(self, x, y, w, h, rgb=None):
        if rgb is not None:
            self.use(rgb)
        self.graphics.rectangle(int(x), int(y), int(w), int(h))

    def pixel(self, x, y, rgb=None):
        if rgb is not None:
            self.use(rgb)
        self.graphics.pixel(int(x), int(y))

    def power_pixel(self):
        """Draw the power lamp. Like every primitive here, does not update().

        Lit by whatever is drawing - boot.py's first frame, main.py's loading
        frame and banner, and the ambient clock at rest - so the one pixel that
        means "powered" is continuous from the instant the panel exists until
        the last LED goes dark, instead of blinking once and vanishing.
        """
        self.pixel(POWER_PIXEL[0], POWER_PIXEL[1], rgb=POWER_RGB)

    def text(self, s, x, y, rgb=None, scale=1):
        if rgb is not None:
            self.use(rgb)
        self.graphics.text(s, int(x), int(y), scale=scale)

    def text_width(self, s, scale=1):
        return self.graphics.measure_text(s, scale=scale)

    def update(self):
        self.gu.update(self.graphics)

    # -- light sensor / brightness ---------------------------------------

    def light(self):
        return self.gu.light()

    def room_is_dark(self):
        return self.light() < self.config.LIGHT_DARK

    def set_brightness(self, value):
        if value < 0.0:
            value = 0.0
        elif value > self.config.BRIGHTNESS_MAX:
            value = self.config.BRIGHTNESS_MAX
        self.gu.set_brightness(value)

    def effective_brightness(self, base):
        """Scale `base` down when the room is dim, keeping a visible floor."""
        light = self.light()
        if light < self.config.LIGHT_DIM:
            scaled = base * (0.35 + 0.65 * (light / float(self.config.LIGHT_DIM)))
        else:
            scaled = base
        return scaled

    # -- audio passthrough (kept here so `galactic` is imported once) -----

    def synth_channel(self, n):
        return self.gu.synth_channel(n)

    def play_synth(self):
        self.gu.play_synth()

    def stop_playing(self):
        self.gu.stop_playing()

    def set_volume(self, v):
        self.gu.set_volume(v)

    def volume_up(self):
        self.gu.adjust_volume(1)

    def volume_down(self):
        self.gu.adjust_volume(-1)

    def is_pressed(self, switch):
        return self.gu.is_pressed(switch)
