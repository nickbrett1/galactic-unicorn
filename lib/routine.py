"""The state machine: AMBIENT -> PROMPT -> COUNTDOWN -> HANDOFF -> AMBIENT.

Timing is monotonic and local (memo section 6): the countdown stores
`end_ticks = ticks_ms() + duration` and never consults a wall clock, WiFi or
a broker. An MQTT blip (phase 2) is therefore invisible mid-countdown.
"""

import time

import bigfont
import icons
from display import GREEN, HEIGHT, WHITE, WIDTH, traffic_rgb

AMBIENT = "ambient"
PROMPT = "prompt"
COUNTDOWN = "countdown"
HANDOFF = "handoff"
OFF = "off"

# PROMPT/SELECTION is plain white: maximum contrast on the dark panel, and it
# stays out of the way of the traffic-light countdown. GREEN is reserved for
# the payoff, so green only ever means "go".
SELECTION_RGB = WHITE
HANDOFF_RGB = GREEN
HANDOFF_FLASH_MS = 250
ICON_GAP = 2

# Prompt button order, front and centre.
ROUTINE_BUTTONS = ("A", "B", "C")


def _scale(rgb, f):
    if f > 1.0:
        f = 1.0
    elif f < 0.0:
        f = 0.0
    return (int(rgb[0] * f), int(rgb[1] * f), int(rgb[2] * f))


def _serpentine_path():
    """Every LED, in the order the countdown lights them.

    Down the screen, then one column to the right, repeatedly (memo section
    4a): the fill grows from the left-hand edge rightward, and within each
    column every LED gets its own moment. That is the "each LED is part of the
    timer" animation, and it ends with the whole panel lit under the traffic
    light.
    """
    path = []
    for x in range(WIDTH):
        for y in range(HEIGHT):
            path.append((x, y))
    return path


class Engine:
    def __init__(self, display, buttons, audio, ambient, config, routines, button_map):
        self.display = display
        self.buttons = buttons
        self.audio = audio
        self.ambient = ambient
        self.config = config
        self.routines = routines
        self.button_map = button_map  # {"A": "bathtime", ...}

        self.state = AMBIENT
        self.routine = None
        self.total_ms = 0
        self.end_ticks = 0
        self.state_started = time.ticks_ms()

        self.path = _serpentine_path()
        self.path_len = len(self.path)

    # -- helpers ---------------------------------------------------------

    def _by_id(self, rid):
        for r in self.routines:
            if r.get("id") == rid:
                return r
        return None

    def state_name(self):
        return self.state

    def remaining_ms(self, now):
        r = time.ticks_diff(self.end_ticks, now)
        return max(0, r)

    # -- transitions -----------------------------------------------------

    def _enter(self, state, now):
        self.state = state
        self.state_started = now

    def start_prompt(self, rid, now):
        routine = self._by_id(rid)
        if routine is None:
            return
        self.routine = routine
        self._enter(PROMPT, now)
        self.audio.play(routine.get("tune", "motif1"), variant="prompt")

    def start_countdown(self, now):
        if self.routine is None:
            return
        total_ms = int(self.routine.get("minutes", 5)) * 60 * 1000
        if self.config.DEMO_SECONDS > 0:
            # Demo mode: a real run, compressed - so the whole transition can
            # be watched in seconds rather than minutes.
            total_ms = int(self.config.DEMO_SECONDS) * 1000
        self.total_ms = total_ms
        self.end_ticks = time.ticks_add(now, self.total_ms)
        self._enter(COUNTDOWN, now)

    def cancel(self, now):
        """Silent by design: a reset must not sound like a fourth event."""
        self.audio.stop()
        self.routine = None
        self._enter(AMBIENT, now)

    # -- button dispatch -------------------------------------------------

    def _handle_events(self, events, now):
        for kind, name in events:
            # Global: parent's escape hatch, top corner, out of a child's way.
            if kind == "hold" and name == "SLEEP":
                if self.state != AMBIENT:
                    self.cancel(now)
                continue
            if kind == "press" and name == "VOL_UP":
                self.display.volume_up()
                continue
            if kind == "press" and name == "VOL_DOWN":
                self.display.volume_down()
                continue
            if kind == "hold" and name == "VOL_DOWN":
                self.audio.toggle_mute()
                continue
            # Brightness buttons stay wired to hardware brightness for the bench.
            if kind == "press" and name == "BRIGHT_UP":
                self.display.gu.adjust_brightness(1)
                continue
            if kind == "press" and name == "BRIGHT_DOWN":
                self.display.gu.adjust_brightness(-1)
                continue

            self._handle_routine_event(kind, name, now)

    def _handle_routine_event(self, kind, name, now):
        st = self.state

        if st == AMBIENT or st == OFF:
            if kind == "press" and name in self.button_map:
                rid = self.button_map[name]
                if rid not in (None, "cancel"):
                    self.start_prompt(rid, now)
            return

        if st == PROMPT:
            if kind == "press" and name == "D":
                self.cancel(now)
            elif kind == "press" and name in self.button_map:
                rid = self.button_map[name]
                if rid == "cancel":
                    self.cancel(now)
                elif self.routine is not None and rid == self.routine.get("id"):
                    # Same button again -> skip straight to COUNTDOWN.
                    self.start_countdown(now)
                elif rid is not None:
                    # Another routine button -> switch prompt.
                    self.start_prompt(rid, now)
            return

        if st == COUNTDOWN:
            if kind == "press" and name == "D" and self.config.D_CANCEL_HOLD_MS == 0 or kind == "hold" and name == "D" and self.config.D_CANCEL_HOLD_MS > 0:
                self.cancel(now)
            elif kind == "hold" and name in ROUTINE_BUTTONS:
                # Every parent needs this: hold a routine button -> +2 minutes.
                self.end_ticks = time.ticks_add(
                    self.end_ticks, self.config.EXTEND_MINUTES * 60 * 1000
                )
                self.total_ms += self.config.EXTEND_MINUTES * 60 * 1000
            # Routine presses are otherwise INERT: no feedback, because
            # feedback teaches them it is a toy.
            return

        if st == HANDOFF:
            if kind == "press" and name == "D":
                self.cancel(now)
            return

    # -- rendering -------------------------------------------------------

    def _draw_selection(self, age_ms, rgb, blank=False):
        """The one selection screen: the animated icon and the big label,
        centred together as a group.

        Both fill the full panel height (11 px), so there is no lopsided
        margin top or bottom. Used by PROMPT and by HANDOFF (which is the same
        picture in green), so "it flashes the icon too" needs no second
        drawing routine.
        """
        d = self.display
        d.clear()
        if blank:
            d.update()
            return
        routine = self.routine
        label = routine.get("label", "")
        symbol = routine.get("symbol", "bath")
        icon_x = 0
        text_x = 0
        width = icons.icon_width(symbol)
        span = width + ICON_GAP + bigfont.text_width(label)
        if 0 < span <= d.width:
            icon_x = (d.width - span) // 2
            text_x = icon_x + width + ICON_GAP
        icons.draw_icon(d, symbol, icon_x, 0, age_ms, rgb=rgb)
        bigfont.draw_text(d, text_x, 0, label, rgb=rgb)
        d.update()

    def _render_prompt(self, now):
        age = time.ticks_diff(now, self.state_started)
        reveal = age / float(self.config.PROMPT_MS) if self.config.PROMPT_MS else 1.0
        reveal = min(reveal, 1.0)
        # Fades up as it appears: a sign arriving, not a warning.
        self._draw_selection(age, _scale(SELECTION_RGB, 0.45 + 0.55 * reveal))

    def _render_countdown(self, now):
        """No digits, no label, no icon: just the panel filling up.

        One LED at a time, down the screen and then to the left, in a traffic
        light that ends on a full, bright green - so the *end* of the wait is
        the most noticeable thing on the panel, exactly when the routine is
        about to start.
        """
        d = self.display
        remaining = self.remaining_ms(now)
        if self.total_ms > 0:
            progress = 1.0 - remaining / float(self.total_ms)
        else:
            progress = 1.0
        if progress < 0.0:
            progress = 0.0
        elif progress > 1.0:
            progress = 1.0

        # Quiet at the start, full colour at the end.
        rgb = _scale(traffic_rgb(progress), 0.55 + 0.45 * progress)
        lit = int(progress * self.path_len + 0.5)

        d.clear()
        d.use(rgb)
        path = self.path
        for i in range(lit):
            x, y = path[i]
            d.pixel(x, y)
        d.update()

    def _render_handoff(self, now):
        """The payoff, back where it started: the prompt's icon *and* label,
        now flashing green. No inverted flood - flashing is the whole event.
        """
        age = time.ticks_diff(now, self.state_started)
        on = (age // HANDOFF_FLASH_MS) % 2 == 0
        self._draw_selection(age, HANDOFF_RGB, blank=not on)

    def _render_off(self):
        self.display.clear((0, 0, 0))
        self.display.update()

    # -- main tick -------------------------------------------------------

    def tick(self, now):
        events = self.buttons.poll(now)
        self._handle_events(events, now)

        # Dark room -> everything goes dark. A press wakes it.
        if self.display.room_is_dark() and self.state == AMBIENT:
            self._render_off()
            return
        if self.state == OFF:
            if events:
                self._enter(AMBIENT, now)
            else:
                self._render_off()
                return

        if self.state == AMBIENT:
            self.display.set_brightness(self.config.BRIGHTNESS_AMBIENT)
            self.ambient.draw(time.ticks_diff(now, self.state_started))
            return

        if self.state == PROMPT:
            self.display.set_brightness(self.config.BRIGHTNESS_PROMPT)
            self._render_prompt(now)
            if time.ticks_diff(now, self.state_started) >= self.config.PROMPT_MS:
                self.start_countdown(now)
            return

        if self.state == COUNTDOWN:
            self.display.set_brightness(self.config.BRIGHTNESS_COUNTDOWN)
            if self.remaining_ms(now) <= 0:
                self.audio.play(self.routine.get("tune", "motif1"), variant="handoff")
                self._enter(HANDOFF, now)
                self._render_handoff(now)
                return
            self._render_countdown(now)
            return

        if self.state == HANDOFF:
            self.display.set_brightness(self.config.BRIGHTNESS_HANDOFF)
            self._render_handoff(now)
            if time.ticks_diff(now, self.state_started) >= self.config.HANDOFF_MS:
                # Never gets stuck: the child is allowed to walk away from it.
                self.cancel(now)
            return
