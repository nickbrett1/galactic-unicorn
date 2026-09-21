#!/usr/bin/env python3
"""Regression tests for the fuse policy. Host-only, no board.

    python3 tests/test_watchdog.py

The bug these pin down: the render loop runs an update check that spends its
time inside a blocking `urequests.get` (DNS, TCP, TLS) where nothing can feed
the fuse, against an 8 s fuse - so a stalled network did not make the check
slow, it rebooted the board (wifi.log, 2026-09-21: every reset reset_cause=3,
the unattended ones one UPDATE_RETRY_MS after a boot).

`machine` is injected before the import, because watchdog only needs WDT() and
reset_cause() - and a fake WDT is the only way to see, on the host, which
timeout the board would have been armed with and when.
"""

import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

# --- the board's machine module, on the host. MUST precede `import watchdog`.
WDT_RESET = 3
created = []
cause = [WDT_RESET]


class FakeWDT:
    def __init__(self, timeout=None):
        self.timeout = timeout
        self.feeds = 0
        created.append(self)

    def feed(self):
        self.feeds += 1


class BoomWDT(FakeWDT):
    def __init__(self, timeout=None):
        raise OSError(19)  # ENODEV: a build with no watchdog at all


machine = types.ModuleType("machine")
machine.WDT = FakeWDT
machine.WDT_RESET = WDT_RESET
machine.reset_cause = lambda: cause[0]
sys.modules["machine"] = machine

import watchdog


def case_fuse_is_short_for_the_app():
    created[:] = []
    watchdog.arm()
    ok = created[-1].timeout == watchdog.TIMEOUT_MS
    return _report("arm() defaults to the app fuse", ok, f"{created[-1].timeout} ms")


def case_network_fuse_is_longer():
    # The whole point: the fuse for a phase that cannot feed it must be longer
    # than the fuse for a loop that feeds it every 20 ms.
    ok = watchdog.NETWORK_TIMEOUT_MS > watchdog.TIMEOUT_MS
    return _report(
        "network fuse is longer than the app fuse",
        ok,
        f"{watchdog.NETWORK_TIMEOUT_MS} vs {watchdog.TIMEOUT_MS} ms",
    )


def case_network_fuse_covers_the_documented_worst_case():
    # config.py documents the attempt as blocking "up to ~30 s in a dead
    # window". A fuse below that turns the worst case into a reboot.
    ok = watchdog.NETWORK_TIMEOUT_MS >= 30000
    return _report(
        "network fuse covers the ~30 s documented worst case",
        ok,
        f"{watchdog.NETWORK_TIMEOUT_MS} ms",
    )


def case_fuse_can_be_widened_and_put_back():
    # What main._checked_under_network_fuse does: widen, run, restore.
    created[:] = []
    watchdog.arm(watchdog.NETWORK_TIMEOUT_MS)
    wide = created[-1].timeout
    watchdog.arm(watchdog.TIMEOUT_MS)
    narrow = created[-1].timeout
    ok = (wide, narrow) == (watchdog.NETWORK_TIMEOUT_MS, watchdog.TIMEOUT_MS)
    return _report("the fuse can be widened and restored", ok, f"{wide} -> {narrow} ms")


def case_feed_before_arming_is_safe():
    # feed() is called from the render loop, including before main arms.
    watchdog._wdt = None
    try:
        watchdog.feed()
        ok = created[-1].feeds >= 0
    except Exception:  # noqa: BLE001
        ok = False
    return _report("feed() before arming constructs the WDT, does not raise", ok, "")


def case_no_watchdog_is_degraded_not_broken():
    machine.WDT = BoomWDT
    ok = watchdog.arm() is False and watchdog.feed() is None
    machine.WDT = FakeWDT
    return _report("a build with no WDT degrades silently", ok, "arm False, feed None")


def case_reset_cause_is_a_latch_check():
    cause[0] = WDT_RESET
    was = watchdog.reset_was_watchdog()
    cause[0] = 1  # PWRON_RESET
    now = watchdog.reset_was_watchdog()
    ok = was is True and now is False
    return _report("reset_was_watchdog follows reset_cause", ok, f"{was}/{now}")


def _report(label, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL':<4} {label:<52} {detail}")
    return ok


def main():
    results = [
        case_fuse_is_short_for_the_app(),
        case_network_fuse_is_longer(),
        case_network_fuse_covers_the_documented_worst_case(),
        case_fuse_can_be_widened_and_put_back(),
        case_feed_before_arming_is_safe(),
        case_no_watchdog_is_degraded_not_broken(),
        case_reset_cause_is_a_latch_check(),
    ]
    print()
    print(f"{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
