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
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

# MicroPython's time helpers, so poll_if_due's cadence arithmetic runs under
# CPython too (the board has ticks_ms/ticks_diff; the host does not).
if not hasattr(time, "ticks_ms"):
    time.ticks_ms = lambda: int(time.monotonic() * 1000)
    time.ticks_diff = lambda a, b: a - b
    time.sleep_ms = lambda ms: time.sleep(ms / 1000.0)

import remote  # after the time shim above

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


# -- cycling the radio out of a wedge (should_cycle_radio) --------------------
#
# The wedge this policy is for, measured 2026-09-27 on this board: the radio
# reports connected=True, status=3, a valid lease and rssi -39, and every
# socket call still dies with OSError(110) - the board does not answer ICMP
# either. `wlan.active(False)`/`active(True)` clears it; neither a soft reset
# nor `machine.reset()` does (the CYW43 has its own supply), so the app has to
# do it. Only the DECISION is pinned here - the cycle needs a board.

def test_should_cycle_radio_only_at_the_threshold():
    assert remote.should_cycle_radio(1, 3) is False
    assert remote.should_cycle_radio(2, 3) is False
    assert remote.should_cycle_radio(3, 3) is True
    assert remote.should_cycle_radio(9, 3) is True


def test_should_cycle_radio_zero_threshold_disables_recovery():
    # 0 means "never", not "immediately": the disabling case must not be the
    # most aggressive one.
    assert remote.should_cycle_radio(0, 0) is False
    assert remote.should_cycle_radio(5, 0) is False
    assert remote.should_cycle_radio(5, None) is False


# -- the cycle is DEFERRED to the render loop, never run inside the poll ------

class _CycleStub:
    """Only what _cycle_radio and take_cycle_request touch.

    Taking the interface down while the matrix's PIO/DMA is mid-frame was
    measured to do nothing at all on this board, so the poll must not do the
    cycling itself - it asks, and main.py performs it at the top of the loop.
    These tests pin that seam: asking touches no network and clears the run
    counter, and one request is taken exactly once.
    """

    def __init__(self):
        self.radio_reset_after = 3
        self.cycle_pending = False
        self._fails_since_ok = 3
        self.journal = None  # _cycle_radio notes the request (lib/wedge.py)
        self.logs = []

    def _net_log(self, message):
        self.logs.append(message)


def test_cycle_radio_asks_rather_than_cycling():
    stub = _CycleStub()
    remote.Remote._cycle_radio(stub)
    assert stub.cycle_pending is True
    assert stub._fails_since_ok == 0
    assert any("needs a cycle" in line for line in stub.logs)


def test_take_cycle_request_is_taken_exactly_once():
    stub = _CycleStub()
    assert remote.Remote.take_cycle_request(stub) is False
    remote.Remote._cycle_radio(stub)
    assert remote.Remote.take_cycle_request(stub) is True
    assert remote.Remote.take_cycle_request(stub) is False


# -- a silent success would hide a recovery, so the first one speaks ----------

class _PollStub:
    """Only what Remote.poll_if_due touches, so the seam can be pinned."""

    def __init__(self, fails=0, boom=None):
        self._disabled_reason = None
        self.next_poll_at = 0
        self.floor_ms = 1000
        self._fails_since_ok = fails
        self.journal = None  # poll_if_due notes a recovery (lib/wedge.py)
        self._boom = boom
        self.logs = []

    def _poll(self, now):
        if self._boom is not None:
            raise self._boom

    def _net_log(self, message):
        self.logs.append(message)

    def _log_failure(self, exc, what, now):
        self.logs.append("failed:" + what)

    def _note_failure(self):
        self._fails_since_ok += 1

    def _schedule(self, now, ms):
        self.next_poll_at = now + ms


def test_poll_success_after_failures_is_logged_once():
    stub = _PollStub(fails=3)
    remote.Remote.poll_if_due(stub, 1000)
    assert stub._fails_since_ok == 0
    assert stub.logs == ["poll recovered after 3 consecutive failures"]
    # The next success has nothing to report and must stay silent.
    remote.Remote.poll_if_due(stub, 3000)
    assert stub.logs == ["poll recovered after 3 consecutive failures"]


def test_poll_failure_still_counts_and_reschedules():
    stub = _PollStub(boom=OSError(110))
    remote.Remote.poll_if_due(stub, 1000)
    assert stub._fails_since_ok == 1
    assert "failed:poll" in stub.logs
    assert stub.next_poll_at == 2000
