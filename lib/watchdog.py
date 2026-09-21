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

And where a loop cannot feed at all - a blocking `urequests.get` that owns the
thread for as long as the network takes - widening the fuse is the answer
rather than feeding it: `NETWORK_TIMEOUT_MS` is armed around the update check
and TIMEOUT_MS restored after, in a `finally`. A rule that says "never block
longer than the fuse" is only useful if the fuse can be the right length.
"""

import machine

# 8 s: far longer than any legitimate single blocking operation (the slowest is
# a wifi poll at 200 ms or one 1 KB chunk off a socket), and short enough that
# a wedged app is back on its feet before anyone notices the panes stopped.
#
# This is the fuse for the APP - the render loop, which feeds it every 20 ms.
# It is NOT the right fuse for the update check (see NETWORK_TIMEOUT_MS).
TIMEOUT_MS = 8000

# The fuse for the network phase of an update check, and the reason the fuse is
# a parameter at all rather than a constant.
#
# `check_for_update` leaves the render loop and spends its time inside
# `urequests.get` - DNS, TCP and the TLS handshake - and updater._get says it
# plainly: none of that can feed the fuse. config.py documents the same attempt
# as blocking "up to ~30 s in a dead window". With an 8 s fuse, that is not a
# slow check, it is a REBOOT.
#
# Measured on this board, 2026-09-21: every reset in wifi.log is reset_cause=3
# (WDT_RESET) - the board was never crashing - and the two unattended ones
# (13:14:24, 15:07:43) each land about one UPDATE_RETRY_MS after a boot, i.e.
# exactly when the in-loop check ran. update.log beside them shows the network
# stalling ("wifi attempt 1/3 got no IP" x8, "OSError('http 504',)").
#
# The cost of a reboot here is not a lost check: it unwinds whatever the panel
# was doing, including a running countdown. So the check widens the fuse for
# the network phase and puts it back afterwards (main.py, and the arm() calls
# in lib/updater.py's own blocking loops). 30 s is the documented worst case
# plus margin: long enough that a stalled check finishes or gives up, short
# enough that a genuinely wedged board is still back on its feet in half a
# minute.
NETWORK_TIMEOUT_MS = 30000

_wdt = None


def arm(timeout_ms=TIMEOUT_MS):
    """Start (or restart) the fuse. Returns True if the board has a watchdog."""
    global _wdt
    try:
        _wdt = machine.WDT(timeout=timeout_ms)
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
