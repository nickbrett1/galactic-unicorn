#!/usr/bin/env python3
"""Tests for the remote poll's PURE helpers (lib/remote.py).

    python3 -m pytest tests/test_remote.py

The network loop itself cannot run without a board - it has no emulator (memo
section 6.4) - so the parts that CAN be pinned on the host are pinned here:
the cadence clamp, the URL split, the query the board actually sends, the
response split, and the heap-vs-link classifier that decides what a failure
line means. lib/remote.py imports nothing board-only at module level (no
machine, no network), so it imports and runs under CPython.

Kept MicroPython-subset-safe on purpose (the same subset lib/remote.py lives
in): no f-strings, no walrus, no type annotations, py3.4-ish syntax.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

import remote

# -- the cadence clamp (server sets it; the board only bounds it) -------------

def test_clamp_poll_ms_inside_and_at_the_bounds():
    assert remote.clamp_poll_ms(2000, 1000, 10000) == 2000
    assert remote.clamp_poll_ms(1000, 1000, 10000) == 1000
    assert remote.clamp_poll_ms(10000, 1000, 10000) == 10000


def test_clamp_poll_ms_below_floor_and_above_ceiling():
    assert remote.clamp_poll_ms(1, 1000, 10000) == 1000
    assert remote.clamp_poll_ms(999999, 1000, 10000) == 10000


def test_clamp_poll_ms_missing_or_garbage_falls_back_to_the_ceiling():
    # No cadence at all -> the idle interval, never a busy-poll at zero.
    assert remote.clamp_poll_ms(None, 1000, 10000) == 10000
    assert remote.clamp_poll_ms("nonsense", 1000, 10000) == 10000


# -- the service URL is a literal IP, and plain HTTP --------------------------

def test_split_url_host_and_port():
    assert remote.split_url("http://192.168.1.2:3009") == ("192.168.1.2", 3009)
    assert remote.split_url("http://192.168.1.2:3009/") == ("192.168.1.2", 3009)
    assert remote.split_url("http://192.168.1.2") == ("192.168.1.2", 80)


def test_split_url_refuses_https_and_empty():
    # https would silently become a plain-HTTP connection: refuse it.
    assert remote.split_url("https://192.168.1.2:3009") == (None, None)
    assert remote.split_url("") == (None, None)


# -- the query the board actually sends ---------------------------------------

def _report(**over):
    base = {
        "boot": "9f3c1a22",
        "fw": "0.2.0",
        "applied_gen": 18,
        "state": "ambient",
    }
    base.update(over)
    return base


def test_poll_path_has_the_static_parameters():
    path = remote.poll_path(_report(), "tok")
    assert path.startswith("/device/poll?")
    assert "token=tok" in path
    assert "boot=9f3c1a22" in path
    assert "fw=0.2.0" in path
    assert "applied_gen=18" in path
    assert "state=ambient" in path
    # Ambient carries no routine/remaining (device-protocols.md section 1).
    assert "routine=" not in path
    assert "remaining_s=" not in path


def test_poll_path_includes_routine_and_remaining_when_present():
    report = _report(state="countdown", routine="cleanup", remaining_s=214,
                     rssi=-41, uptime_s=3820)
    path = remote.poll_path(report, "tok")
    assert "routine=cleanup" in path
    assert "remaining_s=214" in path
    assert "rssi=-41" in path
    assert "uptime_s=3820" in path


# -- the response split --------------------------------------------------------

def test_split_response_reads_status_and_body():
    raw = b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n\r\n{\"gen\":1}"
    code, body = remote.split_response(raw)
    assert code == 200
    assert body == b'{"gen":1}'


def test_split_response_rejects_a_body_with_no_headers():
    code, body = remote.split_response(b"garbage")
    assert code == 0
    assert body == b""


# -- heap vs link: the whole reason a failure line carries context -------------

def test_classify_failure_heap_forms():
    # A C-side allocation failure surfaces as ENOMEM, not MemoryError.
    assert remote.classify_failure(MemoryError("x"), 100, True) == "heap"
    assert remote.classify_failure(OSError(12), 100, True) == "heap"


def test_classify_failure_link_forms():
    assert remote.classify_failure(OSError(-2), 40000, False) == "link"
    # No exception but no link: the join-deferred path.
    assert remote.classify_failure(None, 40000, False) == "link"


def test_classify_failure_other():
    assert remote.classify_failure(ValueError("bad json"), 40000, True) == "other"
