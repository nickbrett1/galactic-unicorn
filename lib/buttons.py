"""Debounce, short press vs hold, and button -> event.

The buttons will be mashed. That is the design constraint, not an edge case.

Events yielded by poll():
    ("press", name)  a fresh short press
    ("hold", name)   held for `hold_ms` (fired once, on the way in)

`name` is one of "A", "B", "C", "D", "SLEEP", "VOL_UP", "VOL_DOWN".
"""

import time

DEBOUNCE_MS = 30


class Buttons:
    def __init__(self, display, switches, hold_ms):
        self.display = display
        self.switches = switches
        # `hold_ms` is an int (same threshold for every button) or a per-name
        # dict, because SLEEP holds for 2 s while a routine button's +2 min
        # needs only ~1 s.
        self.hold_ms = hold_ms
        self._stable = {}
        self._last_raw = {}
        self._changed_at = {}
        self._pressed_at = {}
        self._hold_fired = {}
        for name in switches:
            self._stable[name] = False
            self._last_raw[name] = False
            self._changed_at[name] = 0
            self._pressed_at[name] = 0
            self._hold_fired[name] = False

    def _raw(self, name):
        # A single unreadable switch must not take the whole panel down; treat
        # it as released and carry on.
        try:
            return bool(self.display.is_pressed(self.switches[name]))
        except Exception:  # noqa: BLE001
            return False

    def poll(self, now):
        """Return a list of events since the last poll."""
        events = []
        for name in self.switches:
            raw = self._raw(name)
            if raw != self._last_raw[name]:
                self._last_raw[name] = raw
                self._changed_at[name] = now

            # Only accept a level once it has been stable past the debounce.
            if time.ticks_diff(now, self._changed_at[name]) < DEBOUNCE_MS:
                continue
            if raw == self._stable[name]:
                continue

            self._stable[name] = raw
            if raw:
                self._pressed_at[name] = now
                self._hold_fired[name] = False
                events.append(("press", name))
            else:
                self._pressed_at[name] = 0
                self._hold_fired[name] = False

        # Hold detection on currently-held buttons.
        for name in self.switches:
            if not self._stable[name] or self._hold_fired[name]:
                continue
            threshold = self.hold_ms
            if isinstance(threshold, dict):
                threshold = threshold.get(name, 0)
            if threshold <= 0:
                continue
            if time.ticks_diff(now, self._pressed_at[name]) >= threshold:
                self._hold_fired[name] = True
                events.append(("hold", name))
        return events

    def is_stable_down(self, name):
        return bool(self._stable.get(name))
