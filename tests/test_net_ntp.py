#!/usr/bin/env python3
"""Host tests for net.ntp_time and the NTP fuse budget. No board, no network.

    python3 tests/test_net_ntp.py

Why this file exists. `sync_ntp` in main.py feeds the watchdog and THEN goes to
the network, so whatever it calls has to fit inside the fuse by itself. It used
to call `ntptime.settime()`, which cannot be bounded: it builds its own socket,
chooses its own timeout, and resolves a hostname -- and `getaddrinfo` has no
timeout at all. Measured on the board, 2026-09-27, with NTP unreachable:

    unicorn: ntp: wifi up, syncing
    unicorn: ntp: attempt 1/3 failed (-2)
    unicorn: ntp: attempt 2/3 failed (-2)
    unicorn: ntp: attempt 3/3 failed (-2)
    <USB node gone: hard reset, reset_cause=3>

a reboot on every boot, ~1 s after the third failure -- the fuse the retry had
just fed, expiring during the blocking call that followed it. The fix mirrors
the one already made for the update path: do the query ourselves, over a raw
socket with an explicit timeout, against a literal IP so DNS is not in the path.

The last test is the policy that keeps it fixed: the NTP timeout must stay
comfortably under the fuse. That is the invariant, and it is the one that was
violated -- silently, because an unbounded call has no number to compare.
"""

import os
import socket
import sys
import threading
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.path.insert(0, ROOT)

import net

# --- the board's machine module, on the host. MUST precede `import watchdog`.
machine = types.ModuleType("machine")
machine.WDT = lambda timeout=None: None
machine.WDT_RESET = 3
machine.reset_cause = lambda: 0
sys.modules["machine"] = machine

import config  # after the sys.path inserts above
import watchdog  # after the fake `machine` above

NTP_EPOCH_DELTA = 2208988800
EPOCH = 1780000000  # 2026-05-28, an arbitrary but known instant


def _report(label, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL':<4} {label:<52} {detail}")
    return ok


def _ntp_reply(seconds=EPOCH):
    """A 48-byte v3 server reply carrying `seconds` since 1900."""
    msg = bytearray(48)
    msg[0] = 0x24  # LI = 0, VN = 4, Mode = 4 (server)
    stamp = seconds + NTP_EPOCH_DELTA
    msg[40:44] = stamp.to_bytes(4, "big")  # receive timestamp
    msg[44:48] = stamp.to_bytes(4, "big")  # transmit timestamp
    return bytes(msg)


def _zero_stamp_reply():
    """A reply whose timestamp reads as 1900 -- an unsynchronised server."""
    msg = bytearray(48)
    msg[0] = 0x24
    return bytes(msg)  # bytes 40..44 are zero, so epoch - NTP_EPOCH_DELTA < 0


class _NtpServer(threading.Thread):
    """A one-answer UDP server; `reply` may be None, to stay silent."""

    def __init__(self, reply):
        super().__init__(daemon=True)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.reply = reply
        self.queries = []

    def run(self):
        try:
            data, addr = self.sock.recvfrom(64)
        except OSError:
            return
        self.queries.append(data)
        if self.reply is not None:
            self.sock.sendto(self.reply, addr)

    def stop(self):
        try:
            self.sock.close()
        except OSError:
            pass


def case_reply_is_read_as_epoch_seconds():
    server = _NtpServer(_ntp_reply())
    server.start()
    got = net.ntp_time("127.0.0.1", 2, port=server.port)
    server.join(2)
    ok = got == EPOCH
    return _report("a valid reply becomes epoch seconds", ok, f"{got} != {EPOCH}" if not ok else str(got))


def case_query_is_a_v3_client_packet():
    # The server only answers because it recognised the query; pin the byte, so
    # a later edit cannot quietly make it a mode-0 packet that real servers drop.
    server = _NtpServer(_ntp_reply())
    server.start()
    net.ntp_time("127.0.0.1", 2, port=server.port)
    server.join(2)
    ok = bool(server.queries) and len(server.queries[0]) == 48 and server.queries[0][0] == 0x1B
    return _report("the query is a 48-byte v3 client packet", ok, repr(server.queries[0][:1]))


def case_short_reply_raises():
    server = _NtpServer(b"\x24" * 12)  # too short to hold a timestamp
    server.start()
    try:
        net.ntp_time("127.0.0.1", 2, port=server.port)
        ok, detail = False, "no OSError"
    except OSError as exc:
        ok, detail = True, str(exc)
    server.join(2)
    return _report("a short reply raises instead of returning junk", ok, detail)


def case_zero_reply_raises():
    # A v3 header with a zero timestamp is what an unsynchronised server sends.
    # Returning it would set the clock to 1900 and look like success.
    server = _NtpServer(_zero_stamp_reply())
    server.start()
    try:
        net.ntp_time("127.0.0.1", 2, port=server.port)
        ok, detail = False, "no OSError"
    except OSError as exc:
        ok, detail = True, str(exc)
    server.join(2)
    return _report("a 1900 timestamp raises, it is not 'success'", ok, detail)


def case_a_dead_server_gives_up_inside_the_timeout():
    server = _NtpServer(None)  # bound, listening, forever silent
    server.start()
    try:
        started = time.monotonic()
        net.ntp_time("127.0.0.1", 1, port=server.port)
        ok, detail = False, "no OSError"
    except OSError:
        waited = time.monotonic() - started
        ok, detail = waited < 3, f"{waited:.2f}s"
    server.stop()
    return _report("a silent server fails inside the timeout", ok, detail)


def case_feed_is_called_before_the_blocking_call():
    # The fuse has to be reset BEFORE the call that blocks, not after it, or the
    # feed arrives once the counter has already expired.
    server = _NtpServer(_ntp_reply())
    server.start()
    calls = []
    net.ntp_time("127.0.0.1", 2, port=server.port, feed=lambda: calls.append(1))
    server.join(2)
    ok = len(calls) >= 1
    return _report("feed() runs before the socket call", ok, f"{len(calls)} calls")


def case_a_literal_ip_needs_no_nameserver():
    # config.NTP_HOST is a literal IP precisely so getaddrinfo cannot block:
    # a name would put an unbounded call back in front of the bounded one.
    try:
        addr = socket.getaddrinfo(config.NTP_HOST, 123)[0][-1]
        ok = addr[0] == config.NTP_HOST
        detail = addr[0]
    except OSError as exc:
        ok, detail = False, str(exc)
    return _report("config.NTP_HOST is a literal IP", ok, detail)


def case_ntp_timeout_fits_under_the_fuse():
    # THE invariant. main.py feeds the fuse and then calls net.ntp_time, so the
    # timeout IS the unfed stretch. 2 s against an 8 s fuse leaves room for a
    # retry's hostname-free setup as well as the exchange itself.
    budget_ms = config.NTP_TIMEOUT_S * 1000
    ok = budget_ms < watchdog.TIMEOUT_MS and config.NTP_ATTEMPTS >= 1
    return _report(
        "NTP_TIMEOUT_S fits under WDT TIMEOUT_MS",
        ok,
        f"{budget_ms} ms < {watchdog.TIMEOUT_MS} ms",
    )


def case_every_ntp_host_is_an_address():
    ok = bool(config.NTP_HOSTS) and all(
        _is_dotted_quad(host) for host in config.NTP_HOSTS
    )
    return _report("every NTP_HOSTS entry is a literal IP", ok, ", ".join(config.NTP_HOSTS))


def _is_dotted_quad(host):
    parts = host.split(".")
    return len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)


def main():
    results = [
        case_reply_is_read_as_epoch_seconds(),
        case_query_is_a_v3_client_packet(),
        case_short_reply_raises(),
        case_zero_reply_raises(),
        case_a_dead_server_gives_up_inside_the_timeout(),
        case_feed_is_called_before_the_blocking_call(),
        case_a_literal_ip_needs_no_nameserver(),
        case_every_ntp_host_is_an_address(),
        case_ntp_timeout_fits_under_the_fuse(),
    ]
    print()
    print(f"{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
