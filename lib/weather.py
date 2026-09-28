"""The current NYC weather, for the idle screen.

Built exactly like lib/remote.py: a PASSIVE poller. main.py owns one instance
and calls poll_if_due(now) once per render loop; it does nothing until its
cadence is up, then does at most one bounded fetch and never touches the
display. The Ambient renderer reads the four attributes it keeps
(`temp_c`, `condition`, `is_day`, `ok`) and draws them.

WHY PLAIN HTTP: the board cannot complete a TLS handshake (measured 2026-09-26;
see config.UPDATE_MANIFEST_URL). Open-Meteo answers this request over plain
HTTP with a ~200-byte JSON body, needs no API key, and takes a fixed lat/lon
(NYC). The URL is built in config.py; nothing here is source-specific beyond
the two field names it reads.

WHY ONLY WHEN IDLE: the fetch may join WiFi, which can block for a wifi
attempt. main.py calls poll_if_due only while no routine is active, so a
countdown never shares this thread's time with a network join - the same rule
the remote poller follows (device-protocols.md section 8.2).

Failure is fail-soft and VISIBLE: the last good reading is kept (so the panel
does not flicker to an empty frame), the failure is logged with heap and link
context so a heap failure cannot be mistaken for a link failure, and the next
attempt is scheduled sooner than the normal refresh.

The pure helpers (classify, parse_current, format_temp) import nothing
board-only, so they run under the host pytest suite (tests/test_weather.py).

Subset note: this module stays inside MicroPython 1.19.1's language subset
(roughly CPython 3.4). No f-strings, no walrus operator, no type annotations.
"""

import gc
import json
import os
import time

import net

# One socket read is at most this many bytes. The body is small, so a small
# chunk keeps a single recv allocation tiny.
CHUNK = 128

# weather.log starts over past this size - the board has ~126 KB of heap and no
# rotation (same policy as remote.log).
LOG_LIMIT = 4096


# -- pure helpers (host-tested) ----------------------------------------------

# The small set of pictures lib/icons.py can draw. The mapping is deliberately
# coarse: the panel has room for one shape, so drizzle and heavy rain are both
# "rain".
SUN = "sun"
PARTLY = "partly"
CLOUD = "cloud"
FOG = "fog"
RAIN = "rain"
SNOW = "snow"
THUNDER = "thunder"

_RAIN_CODES = (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82)
_SNOW_CODES = (71, 73, 75, 77, 85, 86)
_THUNDER_CODES = (95, 96, 99)


def classify(code):
    """A WMO weather code -> a condition name from the set above.

    Open-Meteo reports WMO codes (0 clear, 1-3 cloud, 45/48 fog, 5x-8x rain,
    7x/8x snow, 9x thunder). Unknown or unparseable codes fall back to `cloud`,
    the honest "something is happening in the sky" default rather than a wrong
    sun.
    """
    try:
        value = int(code)
    except (TypeError, ValueError):
        return CLOUD
    if value == 0:
        return SUN
    if value in (1, 2):
        return PARTLY
    if value == 3:
        return CLOUD
    if value in (45, 48):
        return FOG
    if value in _RAIN_CODES:
        return RAIN
    if value in _SNOW_CODES:
        return SNOW
    if value in _THUNDER_CODES:
        return THUNDER
    return CLOUD


def parse_current(body):
    """(temp_c, condition, is_day) from an Open-Meteo body, or (None, None, None).

    Only the fields the panel needs are read, and every step is defensive: a
    wrong host, a truncated body or a changed shape must leave the last good
    reading alone (by returning None), never crash the loop.

    `is_day` is the daylight flag the panel uses to dim the weather after
    sunset (BRIGHTNESS_WEATHER_NIGHT). It is asked for in WEATHER_URL, but any
    body without it - a host that does not return it, or an older URL - is
    treated as DAY, so a missing field can only ever leave the panel at its
    normal brightness, never silently dim it.
    """
    try:
        if isinstance(body, (bytes, bytearray)):
            body = body.decode()
        data = json.loads(body)
        current = data["current"]
        raw_temp = current["temperature_2m"]
        code = current.get("weather_code")
    except (ValueError, KeyError, TypeError):
        return None, None, None
    try:
        temp = round(float(raw_temp))
    except (TypeError, ValueError):
        return None, None, None
    return temp, classify(code), _is_day(current.get("is_day"))


def _is_day(raw):
    """Open-Meteo's is_day (1 day / 0 night) -> a bool, defaulting to day.

    Anything but a clear 0 counts as day: the flag only ever *dims* the panel,
    so an absent or odd value must fall on the safe, bright side.
    """
    try:
        return int(raw) != 0
    except (TypeError, ValueError):
        return True


def format_temp(temp_c):
    """The temperature as the panel draws it: "-5C", "0C", "18C"."""
    return str(int(temp_c)) + "C"


# -- board helpers -----------------------------------------------------------

def _default_feed():
    try:
        from watchdog import feed

        feed()
    except Exception:  # noqa: BLE001, S110 - no watchdog is a degradation
        pass


def _file_log(path, line):
    """Append one line to a bounded log file. Never raises."""
    try:
        try:
            size = os.stat(path)[6]
        except OSError:
            size = 0
        if size > LOG_LIMIT:
            os.remove(path)
        with open(path, "a") as fh:
            fh.write(line + "\n")
    except Exception:  # noqa: BLE001, S110 - logging must never be fatal
        pass


class Weather:
    """Fetches the reading and holds it. One instance, owned by main.py.

    Deliberately passive, like the remote: `poll_if_due(now)` is called once
    per render loop, does nothing until the cadence is up, and then does at
    most one fetch. It never sleeps, never runs its own loop, and never touches
    the display.
    """

    def __init__(self, config, log, feed=None):
        self.config = config
        self.log = log
        self._feed = feed or _default_feed

        self.enabled = bool(getattr(config, "WEATHER_ENABLED", False))
        self.url = getattr(config, "WEATHER_URL", "") or ""
        self.host, self.port, self.path = net.split_url(self.url)
        self.timeout_s = float(getattr(config, "WEATHER_TIMEOUT_S", 3))
        self.read_cap = int(getattr(config, "WEATHER_READ_CAP", 1024))
        self.poll_ms = int(getattr(config, "WEATHER_POLL_MS", 15 * 60 * 1000))
        self.retry_ms = int(getattr(config, "WEATHER_RETRY_MS", 60 * 1000))
        self.join_attempts = int(getattr(config, "WEATHER_WIFI_ATTEMPTS", 1))
        self.join_attempt_ms = int(getattr(config, "WEATHER_WIFI_ATTEMPT_MS", 10000))
        self.log_file = getattr(config, "WEATHER_LOG_FILE", "weather.log")

        # The latest reading. `ok` stays False until the first success, so an
        # idle panel shows the clock rather than an empty weather frame.
        self.temp_c = None
        self.condition = None
        # Daylight flag from the same reading: False after sunset, so the panel
        # can dim the weather (lib/ambient.py:_weather_brightness). Defaults to
        # True - a reading that predates this field must not dim the panel.
        self.is_day = True
        self.ok = False

        self._wlan = None
        self.next_poll_at = 0
        # One warning per outage, re-armed on success (so a dead host does not
        # write a line every retry).
        self._warned = False
        self._disabled_reason = self._disabled()

    def _disabled(self):
        if not self.enabled:
            return "WEATHER_ENABLED is False"
        if not self.host:
            return "no usable WEATHER_URL"
        if not getattr(self.config, "WIFI_SSID", None):
            return "no WiFi credentials"
        return None

    def describe(self):
        if self._disabled_reason:
            return "disabled (" + self._disabled_reason + ")"
        return "polling " + self.url

    def reading(self):
        """A one-line summary of the last reading, for the log."""
        if not self.ok:
            return "no reading yet"
        return format_temp(self.temp_c) + " " + str(self.condition)

    def _net_log(self, message, exc=None):
        """net.join_wifi's log hook, folded into this module's log line."""
        if exc is None:
            self.log("weather: " + str(message))
        else:
            self.log("weather: " + str(message) + ": " + repr(exc))

    def _radio(self):
        if self._wlan is None:
            try:
                import network

                self._wlan = network.WLAN(network.STA_IF)
            except Exception:  # noqa: BLE001 - no radio: every poll will defer
                return None
        return self._wlan

    def poll_if_due(self, now):
        """Do at most one fetch, if the cadence says it is time. Never raises."""
        if self._disabled_reason is not None:
            return
        if time.ticks_diff(now, self.next_poll_at) < 0:
            return
        try:
            self._poll(now)
        except Exception as exc:  # noqa: BLE001 - a fetch must not kill the loop
            self._log_failure(exc, "fetch")
            self._schedule(now, self.retry_ms)

    def _schedule(self, now, ms):
        self.next_poll_at = time.ticks_add(now, ms)

    def _poll(self, now):
        # Populate the radio handle for the failure log's link context.
        self._radio()
        # ALWAYS go through join_wifi, never "only if the link looks down".
        # After a SOFT reset the radio chip keeps the association, so
        # isconnected() is true while lwip has no resolver yet - and this URL
        # is a NAME, so that is exactly the boot on which name resolution fails
        # with OSError(-2). join_wifi re-applies the static address (and with it
        # the resolver, network.ipconfig(dns=...)) even when the radio reports
        # connected; main.py's NTP path relies on the same behaviour
        # (lib/net.py:join_wifi).
        if not net.join_wifi(
            self.config,
            self.join_attempts,
            self.join_attempt_ms,
            log=self._net_log,
            feed=self._feed,
        ):
            if not self._warned:
                self._log_failure(None, "wifi join failed")
                self._warned = True
            self._schedule(now, self.retry_ms)
            return
        self._warned = False

        # Heap before network (same discipline as the remote poll): the
        # connection and the read allocate in C, where a shortfall surfaces as
        # OSError(12) rather than MemoryError.
        gc.collect()
        self._feed()
        status, body = net.http_get(
            self.host,
            self.port,
            self.path,
            self.timeout_s,
            feed=self._feed,
            read_cap=self.read_cap,
            chunk=CHUNK,
        )
        if status != 200:
            raise OSError("http " + str(status))
        temp, condition, is_day = parse_current(body)
        if temp is None:
            raise ValueError("unparseable weather body")
        self.temp_c = temp
        self.condition = condition
        self.is_day = is_day
        self.ok = True
        self.log("weather: " + self.reading())
        self._schedule(now, self.poll_ms)

    def _log_failure(self, exc, where):
        """One line with heap AND link context, so the two cannot be confused."""
        try:
            free = gc.mem_free()
        except Exception:  # noqa: BLE001 - logging must never be fatal
            free = None
        wlan = self._wlan
        link = None
        if wlan is not None:
            try:
                link = wlan.isconnected()
            except Exception:  # noqa: BLE001, S110 - the log must not raise
                pass
        line = (
            "weather: "
            + where
            + " failed exc="
            + repr(exc)
            + " heap_free="
            + str(free)
            + " link="
            + str(link)
        )
        self.log(line)
        _file_log(self.log_file, line)
