"""Wedge protection: an armed RP2040 watchdog, and a feed that is always safe.

The gap this closes: `boot.py` running before `main.py` makes a bad release
*repairable*, and the rollback slot makes it *recoverable* - but both need a
boot to happen, and a wedged `main.py` never causes one. The panes stop
changing, the panel sits there, and nothing recovers it until someone unplugs
it. An armed hardware watchdog turns "wedged forever" into "reboots itself in
8 seconds, straight into boot.py's recovery".

Measured on THIS board (2026-09-20, MicroPython v1.29.0, RP2040), because both
facts change the design and neither is safe to assume:

  * `timeout` IS honoured - `machine.WDT(timeout=2000)` fired at ~2 s, not at
    the RP2040's fixed ~8.3 s. So the timeout is ours to choose.
  * an armed watchdog SURVIVES a soft reset. This is the important one. Once
    armed, the *next* boot runs under it too - and that boot's `boot.py` joins
    wifi and downloads a pack, which is up to ~15 s with nothing feeding the
    watchdog. Armed carelessly, the 8 s fuse would cut the update off mid
    download and the board would reset-loop through every boot, never
    finishing an update and never running the app. Hence `_wdt_feed()` calls
    inside the updater's blocking loops: those are not belt-and-braces, they
    are what makes arming safe at all.

HOW IT IS ARMED: by the first `feed()` in a session, which constructs the WDT
if it has not been constructed yet. The first of those is inside the updater's
`_join_wifi` poll loop, i.e. early in boot.py's network phase, and `main.py`
then re-arms explicitly before its render loop. So the fuse covers boot as well
as the running app, rather than starting late in `main.py` and leaving the
longest stretch of the boot - the network phase - unprotected.

Note what follows from that: the module that keeps the fuse fed during boot is
`lib/updater.py`, which is EXCLUDED from the update pack (a remote updater that
can replace itself is how you ship a brick). So a board whose `updater.py`
predates the feeds can be reset-looped by a fuse that only that same file knows
how to feed - the one update that would fix it is the one it cannot receive.
Recover it over USB, and never let the two drift.

The rule for anything added later: **no loop may block longer than TIMEOUT_MS
without calling feed()**. The loops that can are: `_join_wifi` (up to 15 s) and
`_download` in updater.py, the wifi-join and NTP retries in main.py, and the
render loop itself. Everything else - recovery/rollback, the manifest fetch,
unpack and apply - is sub-second on a 54 KB pack and sits inside the window.

`feed()` never raises and never reports: it is called from the render loop,
where a failure to feed must not itself take the display down. If the board
has no WDT (a future `micropython` build without it), every call is a silent
no-op and the board keeps its old behaviour - degraded, not broken.

And where a loop cannot feed at all, the answer used to be to widen the fuse
rather than feed it: `NETWORK_TIMEOUT_MS = 30000` was armed around the update
check and `TIMEOUT_MS` restored after, in a `finally`. That is gone, because the
board cannot honour it (see WDT_MAX_MS below). The update path was instead made
to fit under the 8 s fuse - it fetches the manifest and the pack over plain
HTTP from the LAN service, with an explicit socket timeout on every operation
(config.UPDATE_TIMEOUT_S, lib/net.py:http_get) - so there is nothing left that
needs a longer window. A rule that says "never block longer than the fuse" is
only useful if the fuse can be the right length; on this board it cannot, so the
blocking is bounded instead.
"""

import machine

# 8 s: far longer than any legitimate single blocking operation (the slowest is
# a wifi poll at 200 ms or one 1 KB chunk off a socket), and short enough that
# a wedged app is back on its feet before anyone notices the panes stopped.
#
# This is the fuse for the APP - the render loop, which feeds it every 20 ms -
# and, since 2026-09-26, for the update check too: that path is now bounded to
# fit under it rather than asking for a longer fuse it cannot have (WDT_MAX_MS).
TIMEOUT_MS = 8000

# The longest fuse this board will actually give. `arm()` clamps every request
# to it, so no caller can believe it holds a window the hardware refused.
#
# History, because deleting a constant needs a reason. The update check used to
# widen the fuse to `NETWORK_TIMEOUT_MS = 30000`, on the reasoning that
# `check_for_update` leaves the render loop, spends its time inside a blocking
# `urequests.get` (DNS, TCP and the TLS handshake) that cannot feed the fuse,
# and so would turn an 8 s fuse into a REBOOT on a dead network. The reset log
# backed that up: every reset in wifi.log was reset_cause=3 (WDT_RESET), and the
# unattended ones each landed about one UPDATE_RETRY_MS after a boot - exactly
# when the in-loop check ran.
#
# Measured on this board, 2026-09-26: the widening does not work. An explicit
# `machine.WDT(timeout=30000)` did NOT give 30 s - the fuse fired in under 11 s.
# The RP2040 watchdog cannot provide a ~30 s window, so asking for one left the
# code believing it had 30 s while it really had ~8, which is strictly worse
# than not asking: it hides the real budget. So the fiction is removed.
#
# The update path is instead made short enough to fit (see the module docstring
# and config.UPDATE_TIMEOUT_S): plain HTTP from the LAN, a literal IP, an
# explicit socket timeout on connect and on every read. The rule stands - no
# loop may block longer than TIMEOUT_MS without calling feed() - and it is now
# true of the update check as well.
WDT_MAX_MS = TIMEOUT_MS

_wdt = None


def clamp_timeout_ms(requested):
    """The fuse the hardware will actually arm, for a requested value.

    Pure and host-tested (tests/test_watchdog.py). A request above WDT_MAX_MS is
    not honoured on this board - measured, see above - so it is clamped here
    rather than silently mis-delivered by the loader. Anything unusable (junk, a
    non-positive number) falls back to the app fuse, which is always safe.
    """
    try:
        ms = int(requested)
    except (TypeError, ValueError):
        return TIMEOUT_MS
    if ms <= 0:
        return TIMEOUT_MS
    return min(ms, WDT_MAX_MS)


def arm(timeout_ms=TIMEOUT_MS):
    """Start (or restart) the fuse. Returns True if the board has a watchdog.

    `timeout_ms` is CLAMPED to WDT_MAX_MS (clamp_timeout_ms), so a caller asking
    for a window this board cannot give still gets a fuse that fires when it
    says it will, instead of one that fires early and takes the panel with it.
    """
    global _wdt
    try:
        _wdt = machine.WDT(timeout=clamp_timeout_ms(timeout_ms))
        return True
    except Exception:  # noqa: BLE001 - no WDT is a degradation, not a crash
        _wdt = None
        return False


def feed():
    """Push the fuse back. Safe to call anywhere, including before arming."""
    global _wdt
    try:
        if _wdt is None:
            _wdt = machine.WDT(timeout=TIMEOUT_MS)
        _wdt.feed()
    except Exception:  # noqa: BLE001, S110 - a failed feed must not be fatal
        pass


def reset_was_watchdog():
    """True when the last restart was the watchdog firing.

    This is the only surviving evidence that the wedge protection ever worked:
    a hard reset wipes the heap, the display and any in-memory note of what
    happened, so the reason has to be read back from the chip.
    """
    try:
        return machine.reset_cause() == machine.WDT_RESET
    except Exception:  # noqa: BLE001
        return False
