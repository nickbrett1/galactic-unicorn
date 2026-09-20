#!/usr/bin/env python3
"""Regression tests for the wifi health trace. Host-only, no board.

    python3 tests/test_wifihealth.py

The trace exists to tell two things apart that look identical from the router's
side: a board that is UP without an IP, and a board that is RESETTING. So the
two behaviours under test are the ones that carry that reading - a header per
boot, and a status line per change - plus the bound that keeps the file from
growing forever on a device with no log rotation.

MicroPython's time.ticks_ms has no host equivalent, so the clock is shimmed with
one we control. That is not just a portability shim: it is what makes the
heartbeat testable at all, instead of waiting five minutes for it to come round.
"""

import os
import shutil
import sys
import tempfile
import time

# --- the board's clock, on the host. MUST precede `import wifihealth`, which
# --- does `import time` and then calls these.
_TICKS = [0]
time.ticks_ms = lambda: _TICKS[0]
time.ticks_diff = lambda now, then: now - then

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

import wifihealth


class FakeWlan:
    """A radio whose status, address and signal we can drive by hand."""

    def __init__(self, status=3, ip="192.168.1.63", rssi=-58):
        self.state = status
        self.ip = ip
        self.rssi = rssi

    def status(self, what=None):
        if what == "rssi":
            return self.rssi
        return self.state

    def ifconfig(self):
        return (self.ip, "255.255.255.0", "192.168.1.1", "192.168.1.1")


class FakeConfig:
    WIFI_ENABLED = True
    WIFI_SSID = "Little British Empire"


def tick(ms):
    """Advance the shimmed clock. The sampler is 1 Hz, so steps are seconds."""
    _TICKS[0] += ms


def lines():
    with open(wifihealth.LOG_FILE) as fh:
        return [line for line in fh.read().splitlines() if line.strip()]


def with_tmp(name, body):
    """Run one case in a scratch cwd, like test_recover does."""
    tmp = tempfile.mkdtemp()
    cwd = os.getcwd()
    try:
        os.chdir(tmp)
        return body()
    finally:
        os.chdir(cwd)
        shutil.rmtree(tmp, ignore_errors=True)


def report(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL':<4} {name:<46} {detail}")
    return ok


def case_header_per_boot():
    """One header per boot is what turns a reset loop into an obvious pattern.

    Two constructions here are two boots. On the board that is a wall of
    identical headers, which is exactly the evidence the Orbi log could not
    give: it cannot tell a resetting board from a board sitting still.
    """

    def body():
        wlan = FakeWlan()
        wifihealth.Watch(wlan, print, "0.1.13")
        wifihealth.Watch(wlan, print, "0.1.13")
        heads = [line for line in lines() if "=== boot" in line]
        return report(
            "a header is written on every boot",
            len(heads) == 2,
            f"headers={len(heads)}",
        )

    return with_tmp("headers", body)


def case_transition_once():
    """A change is logged once; a steady radio is not logged every second."""

    def body():
        wlan = FakeWlan(status=3)
        watch = wifihealth.Watch(wlan, print, "0.1.13")
        tick(1000)
        watch.sample()
        tick(1000)
        watch.sample()
        tick(1000)
        watch.sample()
        stated = [line for line in lines() if "status=3" in line]
        return report(
            "steady radio is not re-logged every sample",
            len(stated) == 1,
            f"lines={len(stated)}",
        )

    return with_tmp("steady", body)


def case_failure_window():
    """3 -> 2 is the failure window, and it must be visible with no address."""

    def body():
        wlan = FakeWlan(status=3)
        watch = wifihealth.Watch(wlan, print, "0.1.13")
        tick(1000)
        watch.sample()
        wlan.state = 2
        wlan.ip = "0.0.0.0"
        tick(1000)
        watch.sample()
        found = [line for line in lines() if "status=2(no-ip)" in line]
        # The borrowed 0.0.0.0 is a placeholder, not an address - it must not be
        # printed as though the board had one.
        clean = found and "0.0.0.0" not in found[0]
        return report(
            "3 -> 2 logs a no-ip window with no bogus address",
            bool(clean),
            f"{found[0] if found else 'no line'}",
        )

    return with_tmp("window", body)


def case_heartbeat():
    """A quiet log must be disprovable: silence means stopped, not 'fine'."""

    def body():
        watch = wifihealth.Watch(FakeWlan(), print, "0.1.13")
        tick(1000)
        watch.sample()
        before = len(lines())
        tick(wifihealth.HEARTBEAT_MS)
        watch.sample()
        after = lines()
        beats = [line for line in after if "steady status" in line]
        return report(
            "a heartbeat is written when nothing changes",
            len(after) > before and len(beats) == 1,
            f"beats={len(beats)}",
        )

    return with_tmp("heartbeat", body)


def case_cap():
    """The file is bounded; over the cap it starts over rather than growing."""

    def body():
        with open(wifihealth.LOG_FILE, "w") as fh:
            fh.write("filler\n" * (wifihealth.LOG_LIMIT // 4))
        oversize = os.stat(wifihealth.LOG_FILE)[6] > wifihealth.LOG_LIMIT
        wifihealth.Watch(FakeWlan(), print, "0.1.13")
        body_lines = lines()
        capped = os.stat(wifihealth.LOG_FILE)[6] <= wifihealth.LOG_LIMIT
        marked = any("starting over" in line for line in body_lines)
        return report(
            "an over-cap log starts over, it does not grow",
            oversize and capped and marked,
            f"capped={capped} marked={marked}",
        )

    return with_tmp("cap", body)


def case_no_radio_no_trace():
    """No credentials means no radio to watch, and no trace pretending otherwise."""

    class NoWifi:
        WIFI_ENABLED = True
        WIFI_SSID = None

    class Off:
        WIFI_ENABLED = False
        WIFI_SSID = "ssid"

    def body():
        a = wifihealth.start(NoWifi(), print)
        b = wifihealth.start(Off(), print)
        return report(
            "start() returns None without wifi or credentials",
            a is None and b is None,
            f"no_ssid={a} disabled={b}",
        )

    return with_tmp("nordio", body)


def main():
    _TICKS[0] = 0
    results = [
        case_header_per_boot(),
        case_transition_once(),
        case_failure_window(),
        case_heartbeat(),
        case_cap(),
        case_no_radio_no_trace(),
    ]
    print()
    print(f"{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
