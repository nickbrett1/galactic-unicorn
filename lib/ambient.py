"""Idle rendering: a dim clock, degrading to a single slow-breathing pixel.

AMBIENT is a first-class state, not an afterthought: the display is
shared-room furniture and should read as an object, not as a screen.

The clock is best-effort. If NTP never landed, the screen shows a slow
breathing status pixel instead, so a network problem looks like "quiet",
never like "broken".

One thing here is NOT best-effort: the power lamp in the top-left corner
(lib/display.py: POWER_PIXEL). It is lit whenever the unit is on and no routine
has been selected, so "is this thing even powered?" no longer has to be
answered by looking at the router. In a dark room routine.tick() calls
draw_dark(): the clock and the breathing pixel go, because furniture should not
glow in a child's bedroom, but the lamp stays - a dark room is exactly when "is
this on?" cannot be answered any other way. The weather stays too, because the
phototransistor reads a shaded desk as dark while the room is lit: see
draw_dark and config.AMBIENT_WEATHER_IN_DARK.

After sunset the two sunny conditions - the only glyphs that are a light
source - are replaced by a night sky (a moon and stars), so the panel never
draws a sun in a dark room; see NIGHT_GLYPHS and config.AMBIENT_NIGHT_SKY.
"""

import time

import bigfont
import icons

CLOCK_RGB = (0, 66, 104)
STATUS_RGB = (0, 48, 96)
# The weather reading is a touch brighter than the clock: it is the newer,
# more informative half of the idle screen, and it still stays inside the
# "furniture, not a screen" dimness of AMBIENT brightness.
WEATHER_RGB = (0, 84, 120)
# The cloud is the signature SHADED glyph - the pixel-art kind, lit from above
# with a shadowed underside. Four inks (icons._CLOUD uses 'X' 'L' 'S' 'O'); the
# shared vocabulary is described once, under WEATHER_PENS below.
#
# TONE is what makes a cloud read on a dark panel - not an outline. A single
# flat blue with a bright rim was tried for several passes and on the wall it
# read as a blob or a hill. A pale body, lighter on top where the sky lights
# it and darker underneath, reads as a cloud at a glance.
#
# The body is a soft blue-grey, NOT white: at BRIGHTNESS_WEATHER a white body
# landed near 140 and blew out the dark room. The cloud palette now lands
# around 60-90 on the panel - bright enough to be the clearest thing on the
# idle screen, dim enough to stay furniture. The rim is kept (it was liked) but
# is now a quiet grey edge on the flanks rather than the brightest thing in the
# glyph.
#
# The LIT top has been dialled down repeatedly, and then the whole glyph with
# it. Lit alone: 205/230/240 (panel ~112) -> 170/195/205 (~93) -> 150/178/190
# (~82) -> 140/170/187 (~77). Each pass drew the same "still too much glare",
# and dimming the lit any further just closes the gap to the body until the step
# is no longer a brightness cue at all. So the last pass scales the WHOLE cloud
# to ~0.88 - body, lit, shade and rim together - which takes the glare off
# without flattening the lit-top read.
CLOUD_BODY_RGB = (119, 145, 158)
CLOUD_LIT_RGB = (123, 150, 165)
CLOUD_SHADE_RGB = (55, 84, 106)
CLOUD_RIM_RGB = (84, 88, 95)

# The sun's single gold. Flat by design - see WEATHER_PENS.
SUN_RGB = (170, 140, 32)

# The night sky, drawn after sunset in place of the sun (ambient.NIGHT_GLYPHS).
#
# The moon is a cool pale blue-white rather than the sun's gold, so the panel
# reads as night at a glance; the lit limb is brighter than the body and the
# inner edge darker, the same lit-from-one-side shading the day cloud uses. The
# stars are a dimmer, cooler ink again - points of light, not a second moon.
#
# These sit at the night brightness (BRIGHTNESS_WEATHER_NIGHT, 0.22), where the
# daytime weather colours would glare: the moon lands around the top of the
# cloud-palette range, so it is the clearest thing on the frame without being a
# lamp in a dark room.
MOON_RGB = (150, 165, 205)
MOON_LIT_RGB = (205, 220, 250)
MOON_SHADE_RGB = (70, 85, 130)
STAR_RGB = (120, 140, 190)

# One palette per condition: (body, lit, shade, accent). Every weather glyph is
# shaded the same way ('X' body, 'L' lit top, 'S' shade), and 'O' is the ACCENT
# ink - the rim on the cloud, the sun in `partly`, the drops / flakes / bolt
# elsewhere. A condition we do not know falls back to the cloud palette (the
# icon falls back to the cloud too), so nothing ever draws in a bare single
# ink.
WEATHER_PENS = {
    # The sun is a LIGHT SOURCE, not a lit object, so it takes no lit/shade
    # gradient - all four inks are the same gold. A shaded underside on the sun
    # read as a stripe across the middle of the disc on the panel.
    "sun": (SUN_RGB, SUN_RGB, SUN_RGB, SUN_RGB),
    "partly": ((120, 150, 170), (160, 190, 205), (55, 85, 110), (185, 145, 25)),
    "cloud": (CLOUD_BODY_RGB, CLOUD_LIT_RGB, CLOUD_SHADE_RGB, CLOUD_RIM_RGB),
    "fog": ((70, 110, 140), (110, 150, 180), (35, 65, 95), (70, 110, 140)),
    "rain": ((85, 120, 150), (125, 160, 190), (40, 70, 100), (0, 140, 200)),
    "snow": ((110, 140, 160), (170, 195, 210), (60, 90, 115), (200, 225, 235)),
    "thunder": ((80, 105, 130), (120, 150, 175), (38, 62, 88), (210, 185, 40)),
}

# The after-sunset palettes, keyed by the night GLYPH names in lib/icons.py.
# Same (body, lit, shade, accent) shape as WEATHER_PENS.
#
# `partly-night` is a moon BEHIND a cloud, so its body/lit/shade are the
# daytime cloud greys and only the accent (the moon) is repainted - otherwise
# the cloud would turn blue-white in the dark.
NIGHT_PENS = {
    "night": (MOON_RGB, MOON_LIT_RGB, MOON_SHADE_RGB, STAR_RGB),
    "partly-night": (CLOUD_BODY_RGB, CLOUD_LIT_RGB, CLOUD_SHADE_RGB, MOON_RGB),
}

# The two conditions whose daytime glyph is a LIGHT SOURCE, and the night glyph
# that replaces each once is_day is 0. Every other condition is already honest
# after dark and is left alone.
NIGHT_GLYPHS = {"sun": "night", "partly": "partly-night"}


def pens_for(glyph):
    """The (body, lit, shade, accent) palette for a condition or night glyph."""
    return NIGHT_PENS.get(glyph) or WEATHER_PENS.get(glyph, WEATHER_PENS["cloud"])


BREATH_PERIOD_MS = 4000

# Rebuild the clock string at most this often. The display only shows HH:MM,
# so once a second is plenty - and it must NOT be per frame (see _draw_clock).
CLOCK_REBUILD_MS = 1000

WEATHER_GAP = 2  # px between the weather icon and the temperature


def _format_temp(temp_c):
    """The temperature as the panel draws it: "-5C", "0C", "18C".

    A local copy of lib/weather.py's helper, so the renderer does not import
    the network module (and the host tests can build an Ambient without one).
    """
    return str(int(temp_c)) + "C"


class Ambient:
    def __init__(self, display, config, weather=None):
        self.display = display
        self.config = config
        # Optional: a weather source (lib/weather.py). None, disabled or with no
        # reading yet all mean the same thing - the clock shows on every frame.
        self.weather = weather
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

    def _draw_weather(self, phase_ms=0):
        """The icon and the Celsius reading, centred together as a group.

        Both fill the panel height (the icon is 11 px, the numbers are bigfont
        glyphs), so the frame is balanced top to bottom. The temperature is a
        bare integer with a trailing C - "18C", "-5C" - because the font has no
        degree glyph and a whole degree is all the panel needs to say.

        The palette comes from `pens_for` - the day table (`WEATHER_PENS`) or,
        after sunset, the night table (`NIGHT_PENS`), both in the same (body,
        lit, shade, accent) shape (see lib/icons.py for the ink vocabulary). A
        single-ink glyph simply leaves the extra pens unused, so a flat
        condition and a shaded one cost the drawer the same. The digits stay on
        WEATHER_RGB either way.
        """
        d = self.display
        condition = self.weather.condition or "cloud"
        # After sunset the sunny glyphs become the night sky (moon + stars),
        # never the gold sun. A missing is_day falls on the DAY side (the flag
        # only ever changes what is drawn after dark, and a reading that
        # predates it must not turn a daylight panel into a night one), and
        # AMBIENT_NIGHT_SKY=False opts the whole substitution out.
        if not getattr(self.weather, "is_day", True) and getattr(
            self.config, "AMBIENT_NIGHT_SKY", True
        ):
            glyph = NIGHT_GLYPHS.get(condition, condition)
        else:
            glyph = condition
        label = _format_temp(self.weather.temp_c)
        icon_w = icons.weather_icon_width(glyph) or icons.WEATHER_ICON_W
        span = icon_w + WEATHER_GAP + bigfont.text_width(label)
        x = max(0, (d.width - span) // 2)
        body, lit, shade, accent = pens_for(glyph)
        icons.draw_weather_icon(
            d, glyph, x, 0, age_ms=phase_ms,
            rgb=body, rim_rgb=accent, lit_rgb=lit, shade_rgb=shade,
        )
        bigfont.draw_text(d, x + icon_w + WEATHER_GAP, 0, label, rgb=WEATHER_RGB)

    def _weather_brightness(self):
        """The brightness for a frame with a weather reading on it.

        AMBIENT (0.10) is the right level for the clock and the breathing pixel,
        which are meant to vanish into the furniture, but it is too dim to read
        the weather across a room ("way too dim") - so the weather has its own,
        brighter level. Falls back to AMBIENT if config has not got one.

        After sunset it drops to BRIGHTNESS_WEATHER_NIGHT. The weather is on the
        panel permanently, so at the daytime level it is a small lamp glowing in
        a dark room all evening ("it feels a bit bright when it's showing the
        weather but it's dark outside"). The sun, not the phototransistor, is
        the witness: the sensor reads a shaded desk as dark in a lit room (see
        draw_dark), so keying off it would dim the panel at noon behind a
        cushion. `is_day` comes from the weather source itself (lib/weather.py)
        and rides along in the request the board already makes.

        A missing BRIGHTNESS_WEATHER_NIGHT (an older config) or an absent
        is_day both fall back to the daytime level, so this can only ever dim,
        never brighten.
        """
        day = getattr(self.config, "BRIGHTNESS_WEATHER", self.config.BRIGHTNESS_AMBIENT)
        if self.weather is not None and not getattr(self.weather, "is_day", True):
            return getattr(self.config, "BRIGHTNESS_WEATHER_NIGHT", day)
        return day

    def draw(self, phase_ms):
        d = self.display
        # routine.tick() has already set AMBIENT; raise it for the weather only
        # (the clock fallback below keeps the dimmer level).
        if self.weather is not None and self.weather.ok:
            d.set_brightness(self._weather_brightness())
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
        # The idle screen is the weather, permanently. The clock is only the
        # fallback for the window before the first reading lands (or if weather
        # is disabled), so the panel is never blank; if NTP never synced either,
        # the breathing status pixel takes its place as before.
        if self.weather is not None and self.weather.ok:
            # phase_ms is the idle frame's own clock, so the rain animation is
            # smooth from the first frame and never restarts mid-shower.
            self._draw_weather(phase_ms)
        elif self.ntp_ok:
            self._draw_clock()
        else:
            self._draw_status_pixel(phase_ms)
        d.update()

    def draw_dark(self):
        """A dark room: the lamp, and the weather - and nothing else.

        The clock and the breathing pixel are furniture, and furniture should
        not be a light source in a bedroom at night - so they go, as they always
        have. The lamp stays. It is the one pixel that answers "is this thing
        on?", and a dark room is precisely when nothing else on the panel can
        answer it: the panel is black, the router is in another room, and from
        the sofa a powered board and a dead one look identical.

        One dim LED is also what an appliance looks like when it is off - this
        is a standby light, not a screen. Drawn at AMBIENT brightness (the
        dimmest this app ever draws) rather than whatever the last state left
        behind, so a routine that ended a moment ago cannot leave the lamp
        glaring in the dark.

        The weather is the one exception, and only because the sensor is a poor
        witness: the phototransistor reads a shaded desk as "dark" while the
        room is lit (this board measures a steady light()=17), and blanking the
        weather on that reading is exactly what made a working feature look
        dead. It is drawn at the same BRIGHTNESS_AMBIENT the lit-room path
        already uses, so it is no brighter than the idle screen has always
        been - see config.AMBIENT_WEATHER_IN_DARK to put it back.
        """
        d = self.display
        showing_weather = (
            getattr(self.config, "AMBIENT_WEATHER_IN_DARK", True)
            and self.weather is not None
            and self.weather.ok
        )
        # The weather is a real readout, not a standby lamp: draw it at the
        # weather level so it is legible, and fall back to AMBIENT only when
        # there is nothing on the frame but the lamp.
        if showing_weather:
            d.set_brightness(self._weather_brightness())
        else:
            d.set_brightness(self.config.BRIGHTNESS_AMBIENT)
        d.clear()
        d.power_pixel()
        if showing_weather:
            # phase_ms is not available here (this frame is not the state's
            # phase), so use the raw ms clock: the rain still falls.
            self._draw_weather(time.ticks_ms())
        d.update()
