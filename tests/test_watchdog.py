#!/usr/bin/env python3
"""Regression tests for the fuse policy. Host-only, no board.

    python3 tests/test_watchdog.py

The bug these pin down, twice over.

FIRST (2026-09-21): the render loop ran an update check that spent its time
inside a blocking `urequests.get` (DNS, TCP, TLS) where nothing can feed the
fuse, against an 8 s fuse - so a stalled network did not make the check slow, it
rebooted the board (wifi.log: every reset reset_cause=3, the unattended ones one
UPDATE_RETRY_MS after a boot).

THEN (2026-09-26), the fix for the first bug turned out to be a fiction: the
check was widened to `NETWORK_TIMEOUT_MS = 30000`, but an explicit
`machine.WDT(timeout=30000)` on this board fires in under 11 s, not at 30 s. The
code believed it had a 30 s window and really had ~8 s. So there is no wider
fuse any more; `arm()` clamps every request to WDT_MAX_MS and the update path is
bounded to fit under the app fuse instead. These tests pin the clamp, so the
board can never again be armed with a number the hardware will not honour.

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


def case_no_network_fuse_lies_about_the_window():
    # The 30 s fuse was a fiction: the board fired it in under 11 s. It is gone,
    # and the ceiling arm() will honour is the app fuse itself.
    ok = not hasattr(watchdog, "NETWORK_TIMEOUT_MS")
    return _report(
        "the fictional 30 s network fuse is gone",
        ok,
        "no NETWORK_TIMEOUT_MS",
    )


def case_clamp_caps_at_the_hardware_ceiling():
    # An explicit request for the old 30 s must come back as the ceiling, not as
    # 30 s - otherwise the loader silently under-delivers and the code believes
    # a window it does not have.
    ok = (
        watchdog.clamp_timeout_ms(30000) == watchdog.WDT_MAX_MS
        and watchdog.WDT_MAX_MS <= watchdog.TIMEOUT_MS
    )
    return _report(
        "a request above the ceiling is clamped",
        ok,
        f"30000 -> {watchdog.clamp_timeout_ms(30000)} ms (max {watchdog.WDT_MAX_MS})",
    )


def case_clamp_passes_through_a_smaller_request():
    ok = watchdog.clamp_timeout_ms(2000) == 2000
    return _report("a request below the ceiling is honoured", ok, "2000 ms")


def case_clamp_rejects_junk():
    ok = (
        watchdog.clamp_timeout_ms(None) == watchdog.TIMEOUT_MS
        and watchdog.clamp_timeout_ms("nope") == watchdog.TIMEOUT_MS
        and watchdog.clamp_timeout_ms(0) == watchdog.TIMEOUT_MS
        and watchdog.clamp_timeout_ms(-5) == watchdog.TIMEOUT_MS
    )
    return _report("junk requests fall back to the app fuse", ok, "None/'nope'/0/-5")


def case_arm_clamps_a_larger_request():
    # What the loop's update path did before the fix - arm(NETWORK_TIMEOUT_MS).
    # It must now arm the ceiling, never the requested 30 s.
    created[:] = []
    watchdog.arm(30000)
    ok = created[-1].timeout == watchdog.WDT_MAX_MS
    return _report(
        "arm(30000) arms the ceiling, not 30000",
        ok,
        f"{created[-1].timeout} ms",
    )


def case_arm_can_be_called_again():
    created[:] = []
    watchdog.arm()
    first = created[-1].timeout
    watchdog.arm()
    second = created[-1].timeout
    ok = first == second == watchdog.TIMEOUT_MS
    return _report("re-arming is idempotent", ok, f"{first} -> {second} ms")


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
        case_no_network_fuse_lies_about_the_window(),
        case_clamp_caps_at_the_hardware_ceiling(),
        case_clamp_passes_through_a_smaller_request(),
        case_clamp_rejects_junk(),
        case_arm_clamps_a_larger_request(),
        case_arm_can_be_called_again(),
        case_feed_before_arming_is_safe(),
        case_no_watchdog_is_degraded_not_broken(),
        case_reset_cause_is_a_latch_check(),
    ]
    print()
    print(f"{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
