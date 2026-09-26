"""WiFi associate / reconnect, shared by the updater and the remote poller.

Extracted verbatim (comments and all) from lib/updater.py so the two callers
cannot drift: the radio behaves the same regardless of which caller needs it,
and every measured "why" below - static IP BEFORE connect, a disconnect BEFORE
a retry, the watchdog feeds inside the blocking waits - applies to both the
boot-time update check and the phase-2 remote poll.

The two things a caller supplies instead of globals:

  * `log(message, exc=None)` - where the diagnostics go. updater passes its
    `_log`; the remote passes its own. A no-op default keeps the module usable
    without a logger.
  * `feed()` - the watchdog feed. updater passes its `_wdt_feed` (which is a
    no-op when lib/watchdog.py is absent). The remote passes `watchdog.feed`.
    A no-op default keeps this module importable on the host.

Nothing here changes what updater.py did; updater's `apply_static_ip` and
`_join_wifi` are now thin wrappers over these functions with the same names,
signatures and log lines, so the host tests under tests/test_static_ip.py still
pin the measured behaviour (micropython#15695).

Subset note: this module stays inside MicroPython 1.19.1's language subset
(roughly CPython 3.4). No f-strings, no walrus operator, no type annotations.
"""

import time


def _noop(*_args, **_kwargs):
    """Default for the optional `log` / `feed` callables."""


def _log_with(log, message, exc=None):
    if log is not None:
        log(message, exc)


def wdt_sleep(ms, feed=None):
    """Sleep in fuse-sized steps. The fuse is 8 s and an attempt is 10 s."""
    started = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), started) < ms:
        if feed is not None:
            feed()
        time.sleep_ms(200)


def wait_for_ip(wlan, budget_ms, feed=None):
    """True once the station has an IP. Feeds the fuse while it waits."""
    started = time.ticks_ms()
    while not wlan.isconnected():
        if time.ticks_diff(time.ticks_ms(), started) > budget_ms:
            return False
        # The join can legitimately outlast the 8 s fuse, so this is
        # load-bearing, not tidiness: without it a slow join resets the board.
        if feed is not None:
            feed()
        time.sleep_ms(200)
    return True


def apply_static_ip(wlan, config, log=None):
    """Point the radio at a fixed address, if the network has reserved one.

    Does nothing without a complete STATIC_* set, so the DHCP path is untouched
    for anyone who has not configured it. Must be called with the interface
    active and BEFORE connect(): with an address already set, connect() only has
    to associate, which is the part that has always worked here.

    Uses ipconfig(), NOT the older ifconfig((ip, mask, gw, dns)). On the rp2
    port that 4-tuple leaves the board unable to resolve ANY name: every
    socket.getaddrinfo raises OSError(-2), which cost the NTP sync all three
    attempts and made the update check fail on every single boot. The network
    was never at fault - raw DNS over UDP to the same server answered in
    8-160 ms throughout - so it is the resolver's configuration, and it is a
    known MicroPython regression (micropython#15695). The newer API separates
    the two: wlan.ipconfig(addr4=..., gw4=...) sets the address, and
    network.ipconfig(dns=...) sets the resolver for the whole stack.

    Measured on this board, same address and same server, one after the other:
    with ifconfig every lookup failed, with ipconfig pool.ntp.org resolved in
    144 ms and ntptime.settime() then took 80 ms.
    """
    ip = getattr(config, "STATIC_IP", None)
    if not ip:
        return False
    mask = getattr(config, "STATIC_MASK", None)
    gateway = getattr(config, "STATIC_GATEWAY", None)
    dns = getattr(config, "STATIC_DNS", None)
    if not (mask and gateway and dns):
        _log_with(log, "static ip incomplete, using dhcp")
        return False
    try:
        # Older firmware has no network.ipconfig, and there the 4-tuple
        # ifconfig is both the only way and a working one - the regression
        # arrived in 1.24, and network.ipconfig with it.
        import network

        wlan.ipconfig(addr4=(ip, mask), gw4=gateway)
        network.ipconfig(dns=dns)
        return True
    except Exception as exc:  # noqa: BLE001 - fall back to the older form
        _log_with(log, "ipconfig could not set the static address", exc)
    try:
        wlan.ifconfig((ip, mask, gateway, dns))
    except Exception as exc:  # noqa: BLE001 - DHCP is always the fallback
        _log_with(log, "could not set static ip, using dhcp", exc)
        return False
    return True


def join_wifi(config, attempts=3, attempt_ms=10000, log=None, feed=None):
    """Bring the station up, with `attempts` tries of `attempt_ms` each.

    Returns True once the interface has an IP, False if it runs out of tries.
    Every call is bounded and feeds the fuse while it waits.
    """
    if not getattr(config, "WIFI_SSID", None):
        return False
    import network

    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    # Applied even when the radio already reports a connection, and that order
    # is load-bearing. After a SOFT reset the radio chip keeps the association,
    # so isconnected() is true from the first line of boot.py - while lwip on
    # the RP2040 starts again with no address and no resolver. A check that
    # short-circuits on isconnected() therefore runs with name resolution
    # broken, which is exactly what the log showed: a boot with no join in it
    # at all, and "update failed, keeping current firmware: OSError(-2,)".
    # Re-applying while connected is safe (measured: still connected, lookups
    # went from failing to 66 ms, ntptime to 43 ms), and it is the only thing
    # that puts the resolver back on that boot.
    apply_static_ip(wlan, config, log)
    if wlan.isconnected():
        return True
    # Before the first connect(): a reserved address means there is no DHCP
    # exchange to hang on (see config.STATIC_IP).
    # Association was never the problem: the board reaches "associated, no IP"
    # (status 2) within a second or two and then sits there while DHCP never
    # completes - for the WHOLE attempt. Measured on this board: boot.py's first
    # join burned the full timeout and logged "no wifi", while main.py's join
    # seconds later on the same radio got an IP in 4 s; a manual join got an IP
    # 6/6 on one run and 0/6 three minutes later. So a single attempt is a coin
    # flip, and a retry is what converts it - but the DHCP exchange has to be
    # restarted, which needs a disconnect first.
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            try:
                wlan.disconnect()
            except Exception:  # noqa: BLE001, S110 - not connected is fine
                pass
            wdt_sleep(500, feed)
        try:
            wlan.connect(config.WIFI_SSID, config.WIFI_PASSWORD)
        except OSError as exc:
            _log_with(log, "wifi connect raised", exc)
        if wait_for_ip(wlan, attempt_ms, feed):
            return True
        _log_with(log, "wifi attempt " + str(attempt) + "/" + str(attempts) + " got no IP")
    return False
