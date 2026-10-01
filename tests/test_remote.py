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


def test_poll_path_carries_the_reset_cause():
    # Optional: how the previous boot ended (machine.reset_cause()). The
    # service may ignore it; it is carried so a WDT latch is visible remotely.
    path = remote.poll_path(_report(reset_cause=3), "tok")
    assert "reset_cause=3" in path


def test_poll_path_omits_the_reset_cause_when_unknown():
    # The port may not expose reset_cause() at all - the parameter is optional.
    assert "reset_cause=" not in remote.poll_path(_report(), "tok")


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
    # Unknown free keeps the old verdict rather than guessing.
    assert remote.classify_failure(OSError(12), None, True) == "heap"


def test_classify_failure_enomem_with_a_healthy_heap_is_not_the_heap():
    # The measured HTTPS case (config.py, 2026-09-26): OSError(12) with a
    # healthy heap. The refusal was mbedTLS/lwIP, so "heap" was the wrong window.
    healthy = remote.HEAP_ENOMEM_FLOOR + 1000
    assert remote.classify_failure(OSError(12), healthy, True) == "other"
    # ...and with the link down, the link is the better answer, not "other".
    assert remote.classify_failure(OSError(12), healthy, False) == "link"
    # Exactly at the floor is still a shortfall: the bound is inclusive-low.
    at_floor = remote.HEAP_ENOMEM_FLOOR - 1
    assert remote.classify_failure(OSError(12), at_floor, True) == "heap"


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


# -- the recovery is SPENT when it fails, so it cannot run forever -------------
#
# Measured 2026-09-28, fw=0.1.39: 22 of 23 cycles failed inside one wedge, one
# every ~8 s, and the poller never recorded a recovery. A cycle takes the
# interface DOWN to do its job, so an unthrottled one is a radio that never gets
# a quiet second - the recovery becomes what prevents recovery.

def test_recovery_is_available_bounds_the_count():
    assert remote.recovery_is_available(0, 3, None, 0) is True
    assert remote.recovery_is_available(2, 3, None, 0) is True
    # Three failures in a row and the board stops cycling until a poll works.
    assert remote.recovery_is_available(3, 3, None, 0) is False
    assert remote.recovery_is_available(9, 3, None, 0) is False


def test_recovery_is_available_bounds_the_rate():
    assert remote.recovery_is_available(0, 0, 119999, 120000) is False
    assert remote.recovery_is_available(0, 0, 120000, 120000) is True
    # No cycle yet this session: the rate bound has nothing to bite on.
    assert remote.recovery_is_available(0, 0, None, 120000) is True


def test_recovery_bounds_zero_disable_them():
    # 0 disables, and it must not be the most aggressive setting.
    assert remote.recovery_is_available(99, 0, 0, 0) is True
    assert remote.recovery_is_available(99, None, 0, None) is True


class _UpWlan:
    """A radio that claims to be up - the state _note_failure acts on."""

    def isconnected(self):
        return True


class _FailureStub:
    """Only what _note_failure touches, so the throttle seam can be pinned."""

    _recovery_available = remote.Remote._recovery_available

    def __init__(self, fails=0, failed_cycles=0, last_cycle_at=None):
        self.radio_reset_after = 3
        self.radio_reset_max = 3
        self.radio_reset_cooldown_ms = 120000
        self._fails_since_ok = fails
        self._failed_cycles = failed_cycles
        self._last_cycle_at = last_cycle_at
        self._spent_warned = False
        self.cycles = 0
        self.logs = []

    def _radio(self):
        return _UpWlan()

    def _cycle_radio(self):
        self.cycles += 1
        self._last_cycle_at = time.ticks_ms()

    def _net_log(self, message):
        self.logs.append(message)


def test_note_failure_cycles_once_at_the_threshold():
    stub = _FailureStub(fails=2)
    remote.Remote._note_failure(stub)
    assert stub.cycles == 1
    assert stub._fails_since_ok == 3


def test_note_failure_stops_once_the_allowance_is_spent():
    stub = _FailureStub(fails=2, failed_cycles=3)
    remote.Remote._note_failure(stub)
    assert stub.cycles == 0
    assert stub._fails_since_ok == 3
    assert any("spent" in line for line in stub.logs)
    # Warned once, not once per poll: a board that has stopped trying must not
    # look like a board that is still recovering.
    remote.Remote._note_failure(stub)
    assert len(stub.logs) == 1


def test_note_failure_waits_out_the_cool_down():
    stub = _FailureStub(fails=2, last_cycle_at=time.ticks_ms())
    remote.Remote._note_failure(stub)
    assert stub.cycles == 0
    assert any("spent" in line for line in stub.logs)


def test_note_failure_does_not_count_a_down_link():
    class _DownWlan:
        def isconnected(self):
            return False

    class _DownStub(_FailureStub):
        def _radio(self):
            return _DownWlan()

    stub = _DownStub(fails=2)
    remote.Remote._note_failure(stub)
    assert stub.cycles == 0
    # An honestly-down link is join_wifi's problem, not the wedge's.
    assert stub._fails_since_ok == 0


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


def test_poll_path_carries_the_weather_reading():
    # The page and the tile read the panel's own weather off the wire, so the
    # two cannot disagree with what is on the display.
    path = remote.poll_path(_report(temp_c=15, condition="cloud"), "tok")
    assert "temp_c=15" in path
    assert "condition=cloud" in path


def test_poll_path_carries_a_below_zero_temperature():
    path = remote.poll_path(_report(temp_c=-3, condition="snow"), "tok")
    assert "temp_c=-3" in path
    assert "condition=snow" in path


def test_poll_path_omits_the_weather_when_there_is_no_reading():
    # No reading is not a failure: the poll is still valid and simply carries
    # no weather, which the page renders as no glyph.
    path = remote.poll_path(_report(), "tok")
    assert "temp_c=" not in path
    assert "condition=" not in path


# -- the idle banner the board is drawing (device-protocols.md section 3.0) ----

def test_poll_path_carries_the_message_id():
    path = remote.poll_path(_report(message_id=4), "tok")
    assert "message_id=4" in path


def test_poll_path_omits_the_message_id_when_no_banner():
    assert "message_id=" not in remote.poll_path(_report(), "tok")


class _BannerSelf:
    """The attributes the banner glue touches, so it is testable alone."""

    def __init__(self):
        self.message_id = None
        self.message_text = None
        self.message_at = 0
        self.message_shown = None


def test_update_banner_adopts_then_keeps_the_id():
    s = _BannerSelf()
    remote.Remote._update_banner(s, {"message": {"id": 4, "text": "Hi"}}, "ambient")
    assert s.message_id == 4
    assert s.message_text == "Hi"
    started = s.message_at
    # The same id re-sent on the next 2 s poll must NOT restart the scroll.
    remote.Remote._update_banner(s, {"message": {"id": 4, "text": "Hi"}}, "ambient")
    assert s.message_at == started


def test_update_banner_restarts_on_a_new_id():
    s = _BannerSelf()
    remote.Remote._update_banner(s, {"message": {"id": 4, "text": "Hi"}}, "ambient")
    remote.Remote._update_banner(s, {"message": {"id": 5, "text": "Bye"}}, "ambient")
    assert s.message_id == 5
    assert s.message_text == "Bye"


def test_update_banner_drops_when_the_slot_is_gone():
    s = _BannerSelf()
    remote.Remote._update_banner(s, {"message": {"id": 4, "text": "Hi"}}, "ambient")
    remote.Remote._update_banner(s, {"action": "none"}, "ambient")
    assert s.message_id is None
    assert s.message_text is None


def test_update_banner_drops_when_the_panel_leaves_ambient():
    # Idle-only: a countdown starting must stop the scroll and stop the ack.
    s = _BannerSelf()
    remote.Remote._update_banner(s, {"message": {"id": 4, "text": "Hi"}}, "ambient")
    remote.Remote._update_banner(s, {"message": {"id": 4, "text": "Hi"}}, "countdown")
    assert s.message_id is None
    assert s.message_text is None


def test_message_done_retires_and_does_not_resume_on_a_repeat_poll():
    # One-shot: once the panel has shown the id, a poll still relaying that same
    # slot (the service holds it until its TTL) must not restart the scroll.
    s = _BannerSelf()
    remote.Remote._update_banner(s, {"message": {"id": 4, "text": "Hi"}}, "ambient")
    remote.Remote.message_done(s)
    assert s.message_id is None
    assert s.message_text is None
    assert s.message_shown == 4
    remote.Remote._update_banner(s, {"message": {"id": 4, "text": "Hi"}}, "ambient")
    assert s.message_id is None
    assert s.message_text is None


def test_message_done_still_adopts_a_new_id():
    # A fresh message after a shown one is a new thing to show.
    s = _BannerSelf()
    remote.Remote._update_banner(s, {"message": {"id": 4, "text": "Hi"}}, "ambient")
    remote.Remote.message_done(s)
    remote.Remote._update_banner(s, {"message": {"id": 5, "text": "Bye"}}, "ambient")
    assert s.message_id == 5
    assert s.message_text == "Bye"
