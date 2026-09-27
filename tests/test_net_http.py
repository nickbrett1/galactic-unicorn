#!/usr/bin/env python3
"""Host tests for net.http_get / net.split_url. No board, no network.

    python3 tests/test_net_http.py

Why this file exists: the update path used to run through `urequests`, which
builds its own socket and so cannot be bounded by the caller - and it was that
unbounded call which used to block past the hardware watchdog's fuse and
hard-reset the board (see lib/watchdog.py and tests/test_watchdog.py).
`net.http_get` is the bounded replacement: a raw socket with `settimeout`, so
connect AND every read fail inside the timeout instead of whenever the network
gives up. These tests drive it against a real local HTTP server on the host.

They also pin the two things that are easy to get wrong in a hand-rolled
client: a small body that arrives in the SAME read as the response head (the
whole manifest is smaller than one read, so this is the normal case, not an
edge case), and `read_cap` stopping a body that is larger than the board can
hold.
"""

import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

import net

ROUTES = {}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        spec = ROUTES.get(self.path)
        if spec is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        status, body = spec
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):  # keep the test output clean
        pass


def _serve():
    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


def _report(label, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL':<4} {label:<52} {detail}")
    return ok


def case_split_url():
    ok = (
        net.split_url("http://192.168.1.2:3009/firmware/manifest.json")
        == ("192.168.1.2", 3009, "/firmware/manifest.json")
        and net.split_url("http://host") == ("host", 80, "/")
        and net.split_url("https://github.com/x") == (None, None, None)
        and net.split_url("") == (None, None, None)
        and net.split_url("http://:3009/x") == (None, None, None)
        and net.split_url("http://host:notaport/x") == (None, None, None)
    )
    return _report("split_url handles ip/port/path and rejects TLS", ok, "")


def case_get_small_body_in_one_read():
    # The manifest is smaller than one socket read, so its body normally arrives
    # in the same read as the head. Dropping that `leftover` would silently
    # truncate every manifest - so this is the case that matters most.
    ROUTES["/small"] = (200, b'{"version":"0.1.29"}')
    status, body = net.http_get("127.0.0.1", PORT, "/small", 3)
    ok = status == 200 and body == b'{"version":"0.1.29"}'
    return _report("a small body arriving with the head is kept", ok, f"{status} {body!r}")


def case_get_sink_streams_the_body():
    # The pack is ~140 KB and must never be held whole; with a sink it is handed
    # out in pieces and the return is the total byte count, not the body.
    payload = b"x" * 5000
    ROUTES["/big"] = (200, payload)
    got = []

    def sink(data):
        got.append(data)

    status, total = net.http_get("127.0.0.1", PORT, "/big", 3, sink=sink)
    joined = b"".join(got)
    ok = status == 200 and total == len(payload) and joined == payload
    return _report(
        "a sink receives the whole body in pieces",
        ok,
        f"{status} total={total} pieces={len(got)}",
    )


def case_read_cap_stops_a_large_body():
    # A wrong or hostile server must not be able to hand the board a body it
    # cannot hold: the read stops at read_cap.
    ROUTES["/huge"] = (200, b"y" * 100000)
    status, body = net.http_get("127.0.0.1", PORT, "/huge", 3, read_cap=1000)
    ok = status == 200 and len(body) == 1000
    return _report("read_cap truncates the body", ok, f"len={len(body)}")


def case_non_200_is_reported_not_raised():
    # http_get returns the status; the caller decides what a non-200 means. It
    # must not raise for a well-formed error response.
    ROUTES["/missing"] = (404, b"nope")
    status, body = net.http_get("127.0.0.1", PORT, "/missing", 3)
    ok = status == 404 and body == b"nope"
    return _report("a non-200 status is returned, not raised", ok, f"{status}")


def case_unreachable_port_raises_inside_the_timeout():
    # The whole point of the bound: connect fails within the timeout rather than
    # hanging. Port 1 on loopback is closed, so connect gets a refusal.
    import time

    started = time.monotonic()
    try:
        net.http_get("127.0.0.1", 1, "/x", 2)
        ok = False
    except OSError:
        ok = time.monotonic() - started < 2.5
    return _report(
        "a refused connect raises inside the timeout",
        ok,
        f"{time.monotonic() - started:.2f}s",
    )


def case_getaddrinfo_is_used_for_a_literal_ip():
    # A literal IP is what the board uses (no DNS on the poll or the update).
    # getaddrinfo on a literal must resolve locally and instantly.
    ok = bool(socket.getaddrinfo("192.168.1.2", 3009))
    return _report("a literal IP resolves without a nameserver", ok, "192.168.1.2")


def main():
    global PORT
    server, PORT = _serve()
    try:
        results = [
            case_split_url(),
            case_get_small_body_in_one_read(),
            case_get_sink_streams_the_body(),
            case_read_cap_stops_a_large_body(),
            case_non_200_is_reported_not_raised(),
            case_unreachable_port_raises_inside_the_timeout(),
            case_getaddrinfo_is_used_for_a_literal_ip(),
        ]
    finally:
        server.shutdown()
    print()
    print(f"{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
