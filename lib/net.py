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

import socket
import time

# One socket read is at most this many bytes, so a wrong or hostile server
# cannot make a single `sock.recv()` allocate more than the board can hold.
CHUNK = 256


def _noop(*_args, **_kwargs):
    """Default for the optional `log` / `feed` callables."""


def _log_with(log, message, exc=None):
    if log is not None:
        log(message, exc)


# --- bounded plain-HTTP GET (shared by the updater) -------------------------
#
# One bounded GET, used by lib/updater.py for both the manifest and the pack.
# It lives here, beside the wifi helpers, so the board keeps ONE way of talking
# HTTP and the two callers cannot drift.
#
# Why not `urequests`: it can do neither of the two things the update path
# needs. It cannot be bounded - it builds its own socket, so the caller cannot
# `settimeout` it, and `usocket` has no module-level default (`dir(socket)` on
# this board is `socket, getaddrinfo` and the constants; there is no
# setdefaulttimeout). And it cannot stream a large body, which is why the old
# `_download` had to reach into `response.raw` anyway. A raw socket with an
# explicit timeout gets both, and it is the pattern lib/remote.py already
# proves on this board for the poll.
#
# The timeout is the whole point: it bounds connect AND every individual read,
# so the longest stretch that goes unfed is one timeout, not "however long the
# network feels like". That is what lets the update check run under the ~8 s
# app fuse - the fuse cannot be lengthened on this board (lib/watchdog.py).


def split_url(url):
    """("http://host:port/path") -> (host, port, path), or (None, None, None).

    Only plain http:// is accepted. https:// returns Nones rather than being
    silently downgraded: this board has no working TLS (see
    config.UPDATE_MANIFEST_URL), so an https URL here is a configuration error
    and should fail loudly, not quietly become something else.
    """
    if not url:
        return None, None, None
    if not url.startswith("http://"):
        return None, None, None
    rest = url[7:]
    slash = rest.find("/")
    if slash < 0:
        hostport, path = rest, "/"
    else:
        hostport, path = rest[:slash], rest[slash:]
    if not hostport:
        return None, None, None
    if ":" in hostport:
        host, _, port_str = hostport.partition(":")
        try:
            port = int(port_str)
        except ValueError:
            return None, None, None
    else:
        host, port = hostport, 80
    if not host:
        return None, None, None
    return host, port, path


def status_of(head):
    """The status code from a response head's first line, or 0 if unreadable."""
    line_end = head.find(b"\r\n")
    if line_end < 0:
        line_end = len(head)
    fields = head[:line_end].decode().split(" ")
    if len(fields) < 2:
        return 0
    try:
        return int(fields[1])
    except ValueError:
        return 0


def _read_head(sock, head_limit=1024):
    """Read up to the blank line that ends the response head.

    Returns (head_bytes, leftover). `leftover` is the part of the BODY that
    arrived in the same read as the head, and it must not be dropped: a small
    manifest is smaller than one read, so its whole body usually arrives here.
    """
    buf = b""
    while b"\r\n\r\n" not in buf:
        if len(buf) > head_limit:
            raise OSError("http response head too long")
        piece = sock.recv(64)
        if not piece:
            break
        buf += piece
    idx = buf.find(b"\r\n\r\n")
    if idx < 0:
        return buf, b""
    return buf[:idx], buf[idx + 4:]


def http_get(host, port, path, timeout_s, feed=None, sink=None, read_cap=None,
             chunk=CHUNK):
    """One bounded plain-HTTP/1.0 GET. It cannot hang.

    `sock.settimeout(timeout_s)` bounds connect and every read, so nothing here
    can block past one timeout. `sink(data)` receives the body as it arrives, so
    a 139 KB pack is never held whole (the heap is ~126 KB and a single 47 KB
    read already fails on this board); with `sink` None the body is returned.
    `read_cap`, when set, stops the read at that many body bytes, so a wrong or
    hostile server cannot hand the board a body it cannot hold.

    Returns (status, body_bytes) when `sink` is None, else (status, total_bytes).
    Raises OSError from the socket on connect/read failure - the caller decides
    what that means.
    """
    if feed is None:
        feed = _noop
    feed()
    addr = socket.getaddrinfo(host, port)[0][-1]
    sock = socket.socket()
    parts = []
    total = 0
    try:
        sock.settimeout(timeout_s)
        sock.connect(addr)
        request = (
            "GET " + path + " HTTP/1.0\r\nHost: " + host + "\r\nConnection: close\r\n\r\n"
        )
        sock.send(request.encode())
        feed()
        head, leftover = _read_head(sock)
        status = status_of(head)
        if leftover:
            total += _take(leftover, sink, parts, read_cap, total)
        while read_cap is None or total < read_cap:
            # Feed before each read, not after: the fuse must be reset before
            # the blocking call, not once it has already come back.
            feed()
            take = chunk if read_cap is None else min(chunk, read_cap - total)
            if take <= 0:
                break
            data = sock.recv(take)
            if not data:
                break
            total += _take(data, sink, parts, read_cap, total)
    finally:
        sock.close()
    if sink is None:
        return status, b"".join(parts)
    return status, total


def _take(data, sink, parts, read_cap, total):
    """Deliver one body slice to the sink (or buffer), honouring `read_cap`."""
    if read_cap is not None and total + len(data) > read_cap:
        data = data[: read_cap - total]
    if not data:
        return 0
    if sink is None:
        parts.append(data)
    else:
        sink(data)
    return len(data)


# --- bounded NTP query (the ambient clock) ----------------------------------
#
# The same rule as the GET above, for the same reason, and it is not optional:
# main.py's NTP retry calls `watchdog.feed()` and THEN goes to the network, so
# the call that follows has to fit inside the fuse on its own. `ntptime.settime`
# could not be bounded -- it builds its own socket, picks its own timeout, and
# resolves a HOSTNAME, and getaddrinfo is the one thing here that has no
# timeout at all. Measured on this board, 2026-09-27: with NTP unreachable,
# every boot died about a second after the third failure, reset_cause=3, and
# `pool.ntp.org` made it a DNS problem as well as an NTP one.
#
# So the query is ours, the host is a literal IP (config.NTP_HOST: no
# getaddrinfo, no DNS in the path at all), and `settimeout` bounds the send and
# the read. The longest stretch that goes unfed is one timeout, which is what
# the fuse needs to be true.

NTP_EPOCH_DELTA = 2208988800  # 1900-01-01 -> 1970-01-01, in seconds


def ntp_time(host, timeout_s, port=123, feed=None):
    """Seconds since the Unix epoch, from `host`. Bounded; raises on failure.

    `host` should be a literal IP (config.NTP_HOST) -- a name would put an
    unbounded getaddrinfo back in front of the bounded part. Any failure (no
    reply, short reply, nonsense reply) raises OSError, which is what main.py's
    retry loop is written against; nothing here blocks past one timeout.
    """
    if feed is None:
        feed = _noop
    feed()
    addr = socket.getaddrinfo(host, port)[0][-1]
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(timeout_s)
        query = bytearray(48)
        query[0] = 0x1B  # LI = 0 (no warning), VN = 3 (v3), Mode = 3 (client)
        sock.sendto(query, addr)
        msg = sock.recv(48)
    finally:
        sock.close()
    if len(msg) < 48:
        raise OSError("short ntp reply")
    # Bytes 40..44 are the server's RECEIVE timestamp: the moment our query
    # arrived, which is the sample that costs no round-trip correction. Read in
    # the same place ntptime does; the transmit timestamp we sent is zero.
    seconds = int.from_bytes(msg[40:44], "big") - NTP_EPOCH_DELTA
    if seconds <= 0:
        raise OSError("bad ntp reply")
    return seconds


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


# --- cycling the radio itself -----------------------------------------------
#
# Everything above this line treats the interface as a thing that is either up
# or down. It is not: it can also be UP AND DEAF, and that is the state that
# made the remote poller useless on this board.
#
# Measured 2026-09-27. `wlan.isconnected()` returns True, `wlan.status()` is 3,
# `wlan.ifconfig()` hands back a valid lease (192.168.1.63), `rssi` reads a
# healthy -39 dBm - and every socket call dies with OSError(110) (ETIMEDOUT) at
# exactly its own timeout, while the board stops answering ICMP from the LAN
# entirely (0/24 pings, against 10/10 to the NAS from the same machine). The
# board's own update.log and remote.log alternate
# "no update: 0.1.29 is already running" with
# "update failed, keeping current firmware: OSError(110,)" - so this is not a
# remote-poller bug, it is the radio.
#
# What clears it, and what does not, both matter:
#
#   * `wlan.active(False); wlan.active(True)` clears it. Measured: 10/10 polls
#     OK immediately after, then 180 s at a 3 s cadence with 3 isolated
#     failures and no wedge - i.e. the radio comes back and STAYS back.
#   * a soft reset does NOT clear it.
#   * `machine.reset()` does NOT clear it either, which is the surprising one.
#     The CYW43 is a separate chip with its own supply, so neither of the
#     RP2040's resets powers it down; only a real power cycle does.
#
# That last fact is why this lives in firmware rather than in a procedure. Every
# board-side "fix" a human can reach over USB - mpremote's reset, Ctrl-D - is a
# reset of the wrong chip, so a wedged board stays wedged on the wall until
# somebody unplugs it. A cycle the app can do itself is the only recovery that
# does not need a person.
#
# The sleep between off and on is not decoration: `active(True)` immediately
# after `active(False)` can return before the driver has finished tearing the
# association down, and the rejoin then fails.
#
# And the teardown has to be VERIFIED. Measured 2026-09-27, from inside the
# render loop: `active(False)` then `active(True)` returned in under a
# millisecond, `isconnected()` never went False, and the radio stayed deaf. The
# cycle "worked" only because nothing was checked - the same class of bug as
# the unbounded NTP call, an operation with no number to compare against. An IP
# is not evidence either: the wedge has isconnected() True and a valid lease.
# So the cycle now does three things a no-op cannot fake - ask for the
# association to be dropped, wait until the driver reports it HAS dropped, and
# finish with a real TCP round trip - and reports honestly when it cannot.

RADIO_SETTLE_MS = 1000
RADIO_DOWN_MS = 3000
RADIO_JOIN_MS = 15000
RADIO_PROBE_MS = 2000


def wait_for_link_down(wlan, budget_ms, feed=None):
    """True once the station reports it has let go. Feeds the fuse while waiting.

    The mirror of `wait_for_ip`, and load-bearing for the same reason: a wait
    this long would otherwise expire the 8 s fuse on its own.
    """
    started = time.ticks_ms()
    while True:
        try:
            connected = wlan.isconnected()
        except Exception:  # noqa: BLE001 - an unreadable radio is not an up one
            connected = False
        if not connected:
            return True
        if time.ticks_diff(time.ticks_ms(), started) > budget_ms:
            return False
        if feed is not None:
            feed()
        time.sleep_ms(100)


def tcp_probe(host, port, timeout_s, feed=None):
    """One bounded TCP handshake. True only if the stack completed one.

    The evidence the wedge cannot fake: while the radio is deaf `isconnected()`
    is True and the lease is valid, but `connect()` dies OSError(110) at exactly
    `timeout_s`. Nothing is sent and nothing is read - a completed handshake is
    enough to say the stack is talking to the LAN again.
    """
    if feed is None:
        feed = _noop
    feed()
    try:
        addr = socket.getaddrinfo(host, port)[0][-1]
    except OSError:
        return False
    sock = socket.socket()
    try:
        sock.settimeout(timeout_s)
        sock.connect(addr)
        return True
    except OSError:
        return False
    finally:
        sock.close()


def split_host_port(url, default_port=80):
    """Split "http://host:port[/path]" into (host, port); (None, None) on TLS.

    A smaller copy of lib/remote.py's split_url, kept here so the radio
    recovery verifies against the same literal IP the poller uses without
    depending on the remote module being present.
    """
    if not url:
        return None, None
    rest = url
    if rest.startswith("http://"):
        rest = rest[7:]
    elif rest.startswith("https://"):
        return None, None
    rest = rest.split("/", 1)[0]
    if not rest:
        return None, None
    if ":" in rest:
        host, _, port = rest.partition(":")
        try:
            return host, int(port)
        except ValueError:
            return None, None
    return rest, default_port


def _probe_target(config):
    """(host, port) to verify a cycle against, or None.

    The LAN service the poller uses is the right witness: it is the thing the
    radio is FOR, it is on the board's own segment, and it is configured as a
    literal IP so the probe costs no DNS. The update manifest's host is the
    fallback - also literal - so a build with no remote still verifies.
    """
    for url in (getattr(config, "REMOTE_SERVICE_URL", None),
                getattr(config, "UPDATE_MANIFEST_URL", None)):
        host, port = split_host_port(url)
        if host:
            return host, port
    return None


def radio_reset(config, log=None, feed=None):
    """Power-cycle the radio in software, rejoin, and PROVE it worked.

    Not a `join_wifi` retry: joining assumes the interface works and the
    association is what failed. This assumes the opposite - the interface
    reports itself healthy and is not - so it takes the interface down entirely
    and brings the driver back up before joining. Every step is bounded and
    feeds the fuse, so it is safe to call from the render loop.

    Returns True only when the interface was OBSERVED to go down, came back
    with an IP, and then completed a real TCP round trip. Anything less returns
    False and says which part could not be shown, so the caller can stop
    claiming a recovery it did not get.
    """
    if not getattr(config, "WIFI_SSID", None):
        return False
    import network

    wlan = network.WLAN(network.STA_IF)
    try:
        was = "connected=" + str(wlan.isconnected()) + " status=" + str(wlan.status())
    except Exception:  # noqa: BLE001 - the log must not be what fails
        was = ""
    _log_with(log, "radio: cycling the interface" + (" (" + was + ")" if was else ""))
    try:
        wlan.active(False)
    except Exception as exc:  # noqa: BLE001 - report it, do not raise into the loop
        _log_with(log, "radio: could not take the interface down", exc)
        return False
    # active(False) alone is not enough to trust: ask for the association to be
    # dropped as well, then wait until the driver admits it has. Without this
    # wait every line below ran against a radio that had never restarted.
    try:
        wlan.disconnect()
    except Exception as exc:  # noqa: BLE001 - reported, never raised into the loop
        _log_with(log, "radio: disconnect raised", exc)
    went_down = wait_for_link_down(
        wlan, getattr(config, "RADIO_DOWN_MS", RADIO_DOWN_MS), feed
    )
    _log_with(
        log,
        "radio: interface is down" if went_down
        else "radio: interface never reported down - the cycle did not take effect",
    )
    wdt_sleep(getattr(config, "RADIO_SETTLE_MS", RADIO_SETTLE_MS), feed)
    try:
        wlan.active(True)
    except Exception as exc:  # noqa: BLE001
        _log_with(log, "radio: could not bring the interface back up", exc)
        return False
    # Static IP first, exactly as join_wifi does it and for the same reason: a
    # soft reset leaves the radio associated while lwip has no address, so the
    # address has to be re-applied before anything resolves.
    apply_static_ip(wlan, config, log)
    try:
        wlan.connect(config.WIFI_SSID, config.WIFI_PASSWORD)
    except OSError as exc:
        _log_with(log, "radio: connect raised", exc)
    if not wait_for_ip(wlan, getattr(config, "RADIO_JOIN_MS", RADIO_JOIN_MS), feed):
        _log_with(log, "radio: did not get an IP back")
        return False
    if not went_down:
        # It came back because it never left. Rejoining proves nothing, and
        # reporting "back up" here is the false report this rewrite exists to
        # end - it is what let a no-op cycle look like a cure.
        _log_with(log, "radio: rejoined, but the cycle did not take effect")
        return False
    target = _probe_target(config)
    if target is None:
        _log_with(log, "radio: back up (no endpoint configured to verify against)")
        return True
    # config carries the budget in ms, like every other board constant; sockets
    # want seconds.
    timeout_s = getattr(config, "RADIO_PROBE_MS", RADIO_PROBE_MS) / 1000.0
    if tcp_probe(target[0], target[1], timeout_s, feed):
        _log_with(log, "radio: back up and answering")
        return True
    _log_with(log, "radio: back up but still unreachable - the cycle did not take effect")
    return False
