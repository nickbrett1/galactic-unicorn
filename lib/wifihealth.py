"""A wifi health trace, written down where it outlives the session.

The gap this closes: the network here fails in WINDOWS of minutes, and every
record of it has been accidental. The serial console is gone by the time anyone
looks, and DHCP negotiation only ever happens at BOOT - nothing after main.py's
self-test touches the radio - so the only durable evidence has been what the
DHCP server wrote down on the other end. That is how this was diagnosed at all:
an Orbi log showing this board's MAC leased 192.168.1.63 every 13-25 seconds,
in bursts.

Those bursts have two readings and no amount of staring at the router tells
them apart:

  * the board is UP and re-negotiating, or
  * the board is RESETTING, and each boot's wifi join is one more lease

They mean opposite things - the first is a DHCP fault, the second is a board
fault - and the fix is different for each. So the board keeps its own trace,
with a header per boot. A reset loop then reads as a wall of headers; a board
merely sitting there without an IP reads as one header followed by
status=2 ("associated, no IP") that goes nowhere.

WHAT IS WRITTEN (all to LOG_FILE, append-only within a boot):

  * one HEADER line per boot - firmware, reset cause, free heap. This is the
    line that makes a reset loop visible, and it is the only line a boot costs.
  * one line per CHANGE of wlan.status(). 3 -> 2 is the failure window; 3 -> 1
    is a lost association. The shape of the change is the diagnosis.
  * a HEARTBEAT line every HEARTBEAT_MS, so that the absence of lines means the
    sampler stopped rather than that nothing happened.

The trace is bounded and starts itself over at LOG_LIMIT: this is a device with
126 KB of heap and no log rotation, and losing old lines is a better trade than
an unbounded file. Note that a reset loop fills the cap with headers, which is
itself the answer.

COST: one wlan.status() per SAMPLE_MS (1 Hz - the failure being looked for
lasts minutes, so faster sampling buys nothing), and one file open per line
written. sample() never raises: an instrument must not take down the thing it
is measuring.

Sample the render loop with `sample()`; there is no thread and no timer.
"""

import time

LOG_FILE = "wifi.log"

# 1 Hz: the failure lasts minutes.
SAMPLE_MS = 1000

# Long enough to be quiet, short enough that a stopped sampler is obvious.
HEARTBEAT_MS = 5 * 60 * 1000

# ~130 lines. Small enough that reading it back over the REPL is instant.
LOG_LIMIT = 8192

# The names cyw43 reports, so a trace reads without a lookup table.
STATUS_NAMES = {
    0: "idle",  # interface down
    1: "joined",  # associated to an AP, no IP yet
    2: "no-ip",  # associated, DHCP unanswered  <- the failure window
    3: "up",  # has an IP
}

# Distinct from every real status, so the first sample always reports.
_UNSET = "__unset__"


class Watch:
    """Samples the radio and writes what changed. Never raises."""

    def __init__(self, wlan, log, version="dev"):
        self._wlan = wlan
        self._log = log
        self._started = time.ticks_ms()
        self._next_sample = self._started
        self._next_beat = self._started
        self._status = _UNSET

        self._start_over()
        self._header(version)

    # --- what it does -----------------------------------------------------

    def sample(self):
        """One look at the radio, from the render loop. Cheap; never raises."""
        now = time.ticks_ms()
        if time.ticks_diff(now, self._next_sample) < SAMPLE_MS:
            return
        self._next_sample = now

        try:
            status = self._wlan.status()
        except Exception:  # noqa: BLE001 - a silent radio is not a change
            return

        changed = status != self._status
        if not changed and time.ticks_diff(now, self._next_beat) < HEARTBEAT_MS:
            # Proves the sampler is alive. Without it, a quiet log is ambiguous
            # between "nothing changed" and "nothing is running".
            return

        self._status = status
        self._next_beat = now
        # Everything that ALLOCATES happens here, on the way to a line that
        # actually gets written - once per change, and otherwise once every
        # HEARTBEAT_MS. Deliberately not once per sample: ifconfig() builds a
        # tuple and four strings, and this runs at 1 Hz inside the render loop,
        # which is the one place on this board where heap churn is expensive
        # (see the ENOMEM note in updater._get - a fragmented heap is what makes
        # the in-loop update check fail).
        note = "status" if changed else "steady status"
        self._line(
            f"wifi: {self._stamp()} {note}={status}({STATUS_NAMES.get(status, '?')})"
            f"{self._detail()}"
        )

    def _detail(self):
        """ " ip=... rssi=..." - only called when a line is about to be written."""
        out = ""
        try:
            ip = self._wlan.ifconfig()[0]
            if ip and ip != "0.0.0.0":  # "no address" spelled as an address
                out += " ip=" + ip
        except Exception:  # noqa: BLE001, S110
            pass
        try:
            out += " rssi=" + str(self._wlan.status("rssi"))
        except Exception:  # noqa: BLE001, S110 - not every port answers this
            pass
        return out

    # --- the file ---------------------------------------------------------

    def _stamp(self):
        """Wall clock when the RTC has been set, else boot-relative.

        Wall clock matters: the trace is meant to be lined up against the
        router's log, which is in real time. Until NTP lands the RTC reads its
        2000-01-01 default, so fall back rather than print a confident lie.
        """
        try:
            t = time.localtime()
            if t[0] >= 2024:
                return (
                    f"{t[0]:04d}-{t[1]:02d}-{t[2]:02d}T{t[3]:02d}:{t[4]:02d}:{t[5]:02d}"
                )
        except Exception:  # noqa: BLE001, S110
            pass
        return f"boot+{time.ticks_diff(time.ticks_ms(), self._started) // 1000}s"

    def _header(self, version):
        """The per-boot line. A wall of these IS the reset-loop diagnosis."""
        cause = "?"
        try:
            import machine

            cause = machine.reset_cause()
        except Exception:  # noqa: BLE001, S110
            pass
        free = -1
        try:
            import gc

            free = gc.mem_free()
        except Exception:  # noqa: BLE001, S110
            pass
        self._line(f"wifi: === boot fw={version} reset_cause={cause} free={free} ===")

    def _line(self, text):
        """One line, to the console and to the file.

        Both, because the console is where a live session looks and the file is
        the only one that survives a reset. Neither is allowed to be fatal.
        """
        try:
            self._log(text)
        except Exception:  # noqa: BLE001, S110
            pass
        try:
            with open(LOG_FILE, "a") as fh:
                fh.write(text + "\n")
        except Exception:  # noqa: BLE001, S110 - logging must never be fatal
            pass

    def _start_over(self):
        """Cap the file. Only ever run once per boot, before anything is written."""
        try:
            import os

            if os.stat(LOG_FILE)[6] <= LOG_LIMIT:
                return
        except Exception:  # noqa: BLE001 - missing file is the normal case
            return
        try:
            with open(LOG_FILE, "w") as fh:
                fh.write(
                    f"wifi: === {self._stamp()} log hit the {LOG_LIMIT}-byte cap,"
                    " starting over ===\n"
                )
        except Exception:  # noqa: BLE001, S110
            pass


def start(config, log, version="dev"):
    """Begin a trace, or return None when there is no radio to watch.

    Returning None rather than a no-op is deliberate: the render loop then
    checks for None, so it never samples a radio that was never brought up and
    never pays for a trace that could not say anything.
    """
    if not getattr(config, "WIFI_ENABLED", False):
        return None
    if not getattr(config, "WIFI_SSID", None):
        # No credentials: sync_ntp skips and the radio may never associate, so a
        # trace of it would be noise rather than evidence.
        return None
    try:
        import network

        wlan = network.WLAN(network.STA_IF)
    except Exception as exc:  # noqa: BLE001 - a trace we cannot take is not fatal
        log("wifi: not watching the radio: " + repr(exc))
        return None
    return Watch(wlan, log, version)
