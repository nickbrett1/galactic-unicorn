"""The state machine: AMBIENT -> PROMPT -> COUNTDOWN -> HANDOFF -> AMBIENT.

Timing is monotonic and local (memo section 6): the countdown stores
`end_ticks = ticks_ms() + duration` and never consults a wall clock, WiFi or
a broker. An MQTT blip (phase 2) is therefore invisible mid-countdown.
"""

import time

import digits
from display import ramp_rgb

AMBIENT = "ambient"
PROMPT = "prompt"
COUNTDOWN = "countdown"
HANDOFF = "handoff"
OFF = "off"

COUNTDOWN_LABEL_RGB = (60, 70, 90)
HANDOFF_TEXT_RGB = (0, 0, 0)
HANDOFF_FLASH_MS = 2500

# Prompt button order, front and centre.
ROUTINE_BUTTONS = ("A", "B", "C")


def _scale(rgb, f):
    if f > 1.0:
        f = 1.0
    elif f < 0.0:
        f = 0.0
    return (int(rgb[0] * f), int(rgb[1] * f), int(rgb[2] * f))


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
        self.total_ms = int(self.routine.get("minutes", 5)) * 60 * 1000
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

    def _render_prompt(self, now):
        d = self.display
        routine = self.routine
        age = time.ticks_diff(now, self.state_started)
        reveal = age / float(self.config.PROMPT_MS)
        rgb = _scale((0, 150, 150), 0.4 + 0.6 * reveal)
        d.clear()
        label = routine.get("label", "")
        w = d.text_width(label, scale=1)
        if w > d.width:
            # Long labels scroll rather than clip.
            x = d.width - int((w + d.width) * reveal)
        else:
            x = (d.width - w) // 2
        d.text(label, x, (d.height - 8) // 2, rgb=rgb, scale=1)
        d.update()

    def _render_countdown(self, now):
        d = self.display
        remaining = self.remaining_ms(now)
        remaining_s = remaining / 1000.0
        total_s = self.total_ms / 1000.0
        rgb = ramp_rgb(remaining_s, total_s)

        # Final stretch: slow pulse, pace increasing (the only "hurry" signal).
        pulse = 1.0
        if remaining_s <= self.config.FINAL_STRETCH_S:
            frac = (self.config.FINAL_STRETCH_S - remaining_s) / float(
                self.config.FINAL_STRETCH_S
            )
            period = 1000 - 600 * frac  # 1000 ms -> 400 ms
            phase = (now % int(period)) / float(period)
            tri = phase * 2 if phase < 0.5 else 2 - phase * 2
            pulse = 0.65 + 0.35 * tri

        pen_rgb = _scale(rgb, pulse)

        d.clear()

        # Digits: minutes normally, seconds in the last minute.
        if remaining > 60000:
            value = (remaining + 59999) // 60000
            value = min(value, 99)
        else:
            value = (remaining + 999) // 1000

        digits.draw_number(d, 1, 0, 6, 9, 2, value, gap=2, rgb=pen_rgb)

        # Routine label top-right, dim - it is there for the parent.
        label = self.routine.get("label", "")
        lw = d.text_width(label, scale=1)
        if lw <= d.width:
            d.text(label, d.width - lw - 1, 0, rgb=COUNTDOWN_LABEL_RGB, scale=1)

        # Full-width draining bar along the bottom two rows.
        ratio = remaining_s / total_s if total_s else 0.0
        digits.draw_bar(d, 0, 9, d.width, 2, ratio, rgb=pen_rgb, bg_rgb=(6, 8, 12))
        d.update()

    def _render_handoff(self, now):
        d = self.display
        age = time.ticks_diff(now, self.state_started)
        msg = self.routine.get("end_message", "GO!")
        if age < self.config.HANDOFF_MS:
            if age < HANDOFF_FLASH_MS:
                # Phase 1: the message flashes large.
                on = (age // 250) % 2 == 0
                d.clear()
                if on:
                    mw = d.text_width(msg, scale=1)
                    if mw <= d.width:
                        x = (d.width - mw) // 2
                    else:
                        x = d.width - int((mw + d.width) * ((age % 1200) / 1200.0))
                    d.text(msg, x, (d.height - 8) // 2, rgb=(0, 220, 60), scale=1)
                d.update()
            else:
                # Phase 2: the screen floods green - the brightest state we reach.
                d.clear((0, 255, 48))
                d.text(msg, 1, (d.height - 8) // 2, rgb=HANDOFF_TEXT_RGB, scale=1)
                d.update()

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
