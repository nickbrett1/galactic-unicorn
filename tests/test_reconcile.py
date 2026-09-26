#!/usr/bin/env python3
"""Tests for the pure reconciliation decision (lib/reconcile.py).

    python3 -m pytest tests/test_reconcile.py

Phase C, deliverable C1: the board-independent half. This is the only part of
the firmware that can be tested without a flash and a look (memo section 6.4),
so it carries the whole of the reconcile semantics: gen ordering, equality,
TTL expiry, the boot-id rule, the inert-during-countdown rule, and
cancel-equals-D.

Kept MicroPython-subset-safe on purpose (the same subset lib/reconcile.py
lives in): no f-strings, no walrus, no type annotations, py3.4-ish syntax.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

import reconcile


def _slot(gen, action, routine=None, expires_at=None):
    d = {"gen": gen, "action": action}
    if routine is not None:
        d["routine"] = routine
    if expires_at is not None:
        d["expires_at"] = expires_at
    return d


# -- gen ordering, both directions, and equality -----------------------------

def test_is_gen_applied_both_directions_and_equality():
    # Behind the board's mark -> applied. Ahead -> not applied. Equal -> applied.
    assert reconcile.is_gen_applied(18, 18) is True
    assert reconcile.is_gen_applied(19, 18) is True
    assert reconcile.is_gen_applied(17, 18) is False


def test_reseed_gen_both_directions():
    # Normal case: the fresh server sits one above the board's mark.
    assert reconcile.reseed_gen(17) == 18
    # R6: even when the board's gen is ahead of the server's, reseed must not
    # go backwards -- it is always applied_gen + 1.
    assert reconcile.reseed_gen(18) == 19


def test_decide_applies_a_newer_gen():
    panel = {"boot": "b1", "last_boot": "b1", "state": "ambient", "routine": None}
    kind, detail = reconcile.decide(_slot(19, "start", "bathtime"), 18, panel, 1000)
    assert (kind, detail) == ("apply", "bathtime")


def test_decide_ignores_an_older_gen():
    panel = {"boot": "b1", "last_boot": "b1", "state": "ambient", "routine": None}
    kind, detail = reconcile.decide(_slot(17, "start", "bathtime"), 18, panel, 1000)
    assert (kind, detail) == ("ignore", "stale_gen")


def test_decide_equality_is_a_noop():
    # applied_gen == gen is not re-applied: the panel is not told twice.
    panel = {"boot": "b1", "last_boot": "b1", "state": "ambient", "routine": None}
    kind, detail = reconcile.decide(_slot(18, "start", "bathtime"), 18, panel, 1000)
    assert (kind, detail) == ("ignore", "stale_gen")


# -- TTL expiry: dropped, never queued ---------------------------------------

def test_is_expired_at_and_past_the_deadline():
    slot = _slot(19, "start", "bathtime", expires_at=1045)
    assert reconcile.is_expired(slot, 1044) is False
    assert reconcile.is_expired(slot, 1045) is True
    assert reconcile.is_expired(slot, 1046) is True
    assert reconcile.is_expired(None, 1045) is True


def test_decide_drops_an_expired_slot_never_queues():
    panel = {"boot": "b1", "last_boot": "b1", "state": "ambient", "routine": None}
    slot = _slot(19, "start", "bathtime", expires_at=1045)
    kind, detail = reconcile.decide(slot, 18, panel, 1046)
    assert (kind, detail) == ("ignore", "expired")


def test_decide_still_applies_a_slot_inside_its_ttl():
    panel = {"boot": "b1", "last_boot": "b1", "state": "ambient", "routine": None}
    slot = _slot(19, "start", "bathtime", expires_at=1045)
    kind, detail = reconcile.decide(slot, 18, panel, 1044)
    assert (kind, detail) == ("apply", "bathtime")


# -- new boot id clears pending desired --------------------------------------

def test_clear_on_boot_drops_on_a_new_boot_id():
    slot = _slot(19, "start", "bathtime")
    assert reconcile.clear_on_boot(slot, "b2", "b1") is None


def test_clear_on_boot_passes_through_same_boot_and_missing_boot():
    slot = _slot(19, "start", "bathtime")
    assert reconcile.clear_on_boot(slot, "b1", "b1") is slot
    assert reconcile.clear_on_boot(slot, "b1", None) is slot
    assert reconcile.clear_on_boot(slot, None, "b1") is slot


def test_decide_clears_a_pending_slot_after_a_reboot():
    # The same desired gen would otherwise re-run after a reboot.
    panel = {"boot": "b2", "last_boot": "b1", "state": "ambient", "routine": None}
    kind, detail = reconcile.decide(_slot(19, "start", "bathtime"), 18, panel, 1000)
    assert (kind, detail) == ("ignore", "boot_changed")


# -- ROUTINE events are inert during COUNTDOWN -------------------------------

def test_is_event_live_routine_inert_during_countdown_and_handoff():
    assert reconcile.is_event_live("ambient", "booktime") is True
    assert reconcile.is_event_live("prompt", "booktime") is True
    assert reconcile.is_event_live("countdown", "booktime") is False
    assert reconcile.is_event_live("handoff", "booktime") is False


def test_decide_refuses_a_routine_start_during_countdown():
    # A remote "book time" while bathtime counts down does nothing: no silent
    # switch, no lost countdown.
    panel = {"boot": "b1", "last_boot": "b1", "state": "countdown", "routine": "bathtime"}
    kind, detail = reconcile.decide(_slot(19, "start", "booktime"), 18, panel, 1000)
    assert (kind, detail) == ("noop", "inert")


def test_decide_allows_a_start_while_ambient_or_prompt():
    ambient = {"boot": "b1", "last_boot": "b1", "state": "ambient", "routine": None}
    prompt = {"boot": "b1", "last_boot": "b1", "state": "prompt", "routine": "bathtime"}
    assert reconcile.decide(_slot(19, "start", "bathtime"), 18, ambient, 1000) == ("apply", "bathtime")
    assert reconcile.decide(_slot(19, "start", "booktime"), 18, prompt, 1000) == ("apply", "booktime")


# -- cancel == the D event, live in PROMPT / COUNTDOWN / HANDOFF ---------------

def test_cancel_maps_to_the_reset_event():
    assert reconcile.event_for_desired(_slot(19, "cancel")) == "reset"


def test_event_for_desired_maps_start_and_none():
    assert reconcile.event_for_desired(_slot(19, "start", "cleanup")) == "cleanup"
    assert reconcile.event_for_desired(_slot(19, "none")) is None
    assert reconcile.event_for_desired(None) is None


def test_event_for_desired_refuses_a_routine_outside_the_wire_vocabulary():
    # Exactly four events (device-protocols.md section 2): a `start` naming
    # anything else yields no event, so the poll cannot log "applied" for a
    # routine the button map would silently drop.
    assert reconcile.event_for_desired(_slot(19, "start", "tidyup")) is None
    assert reconcile.event_for_desired(_slot(19, "start", "reset")) is None
    assert reconcile.event_for_desired(_slot(19, "start")) is None


def test_decide_is_a_noop_for_a_routine_outside_the_wire_vocabulary():
    panel = {"boot": "b1", "last_boot": "b1", "state": "ambient", "routine": None}
    kind, detail = reconcile.decide(_slot(19, "start", "tidyup"), 18, panel, 1000)
    assert (kind, detail) == ("noop", "no_desired")


def test_reset_is_live_in_every_active_state_and_a_noop_in_ambient():
    assert reconcile.is_event_live("prompt", "reset") is True
    assert reconcile.is_event_live("countdown", "reset") is True
    assert reconcile.is_event_live("handoff", "reset") is True
    assert reconcile.is_event_live("ambient", "reset") is False


def test_decide_cancels_in_every_active_state():
    for state in ("prompt", "countdown", "handoff"):
        panel = {"boot": "b1", "last_boot": "b1", "state": state, "routine": "bathtime"}
        kind, detail = reconcile.decide(_slot(19, "cancel"), 18, panel, 1000)
        assert (kind, detail) == ("apply", "reset")


def test_decide_cancel_in_ambient_is_a_noop():
    panel = {"boot": "b1", "last_boot": "b1", "state": "ambient", "routine": None}
    kind, detail = reconcile.decide(_slot(19, "cancel"), 18, panel, 1000)
    assert (kind, detail) == ("noop", "nothing_to_cancel")


# -- the four-event vocabulary ------------------------------------------------

def test_event_vocabulary_is_exactly_four_events():
    assert reconcile.DEVICE_EVENTS == ("bathtime", "booktime", "cleanup", "reset")
    assert reconcile.ROUTINE_IDS == ("bathtime", "booktime", "cleanup")


# -- the server-side conflict table (mirrored, so both sides agree) -----------

def test_resolve_command_conflict_table():
    # ambient + start -> set
    assert reconcile.resolve_command("start", "bathtime", {"state": "ambient"}) == (
        "set", "start", "bathtime")
    # counting X + start X -> noop already_running
    assert reconcile.resolve_command(
        "start", "bathtime", {"state": "countdown", "routine": "bathtime"}) == (
        "noop", "already_running")
    # counting X + start Y -> conflict
    assert reconcile.resolve_command(
        "start", "booktime", {"state": "countdown", "routine": "bathtime"}) == (
        "conflict", "bathtime")
    # counting + cancel -> set cancel
    assert reconcile.resolve_command(
        "cancel", None, {"state": "countdown", "routine": "bathtime"}) == (
        "set", "cancel")
    # ambient + cancel -> noop nothing_to_cancel
    assert reconcile.resolve_command("cancel", None, {"state": "ambient"}) == (
        "noop", "nothing_to_cancel")
    # handoff + cancel -> set cancel
    assert reconcile.resolve_command(
        "cancel", None, {"state": "handoff", "routine": "bathtime"}) == (
        "set", "cancel")
    # unreachable + start -> refuse_offline
    assert reconcile.resolve_command(
        "start", "bathtime", {"online": False, "last_seen_s": 180}) == (
        "refuse_offline", 180)


# -- cadence ------------------------------------------------------------------

def test_next_poll_ms_demand_and_idle_and_clamps():
    assert reconcile.next_poll_ms(pending_apply=True) == 2000
    assert reconcile.next_poll_ms(active_countdown=True) == 2000
    assert reconcile.next_poll_ms(subscribers=1) == 2000
    assert reconcile.next_poll_ms() == 5000
    assert reconcile.next_poll_ms(min_ms=6000) == 6000
    assert reconcile.next_poll_ms(max_ms=1000) == 1000


# -- the observed-state report builder ---------------------------------------

def test_build_report_fills_every_field():
    report = {}
    out = reconcile.build_report(
        report, "9f3c1a22", "0.2.0", 18, "countdown", "bathtime", 214, -41, 3820)
    assert out is report
    assert report == {
        "boot": "9f3c1a22",
        "fw": "0.2.0",
        "applied_gen": 18,
        "state": "countdown",
        "routine": "bathtime",
        "remaining_s": 214,
        "rssi": -41,
        "uptime_s": 3820,
    }


def test_build_report_omits_absent_optional_fields():
    report = reconcile.build_report({}, "b1", "0.2.0", 3, "ambient")
    assert report == {"boot": "b1", "fw": "0.2.0", "applied_gen": 3, "state": "ambient"}
    assert "routine" not in report
    assert "remaining_s" not in report


def test_build_report_reuse_leaves_no_stale_fields():
    # The dict is allocated once and reused per poll; a cleared optional field
    # must not survive from the previous poll.
    report = {}
    reconcile.build_report(report, "b1", "0.2.0", 18, "countdown", "bathtime", 214)
    reconcile.build_report(report, "b1", "0.2.0", 18, "ambient")
    assert report == {"boot": "b1", "fw": "0.2.0", "applied_gen": 18, "state": "ambient"}


def test_build_report_keeps_zero_valued_optionals():
    # 0 is a value, not "absent": remaining_s 0 and rssi 0 must be reported,
    # or the server would read a finished countdown / a pinned signal as stale.
    report = reconcile.build_report(
        {}, "b1", "0.2.0", 0, "handoff", "cleanup", 0, 0, 0)
    assert report == {
        "boot": "b1",
        "fw": "0.2.0",
        "applied_gen": 0,
        "state": "handoff",
        "routine": "cleanup",
        "remaining_s": 0,
        "rssi": 0,
        "uptime_s": 0,
    }


def test_build_report_reuse_clears_routine_but_keeps_rssi():
    # routine/remaining_s are countdown-only; rssi/uptime are always present.
    # A reused dict must drop the former and keep the latter.
    report = {}
    reconcile.build_report(report, "b1", "0.2.0", 18, "countdown", "booktime", 5, -41, 9)
    reconcile.build_report(report, "b1", "0.2.0", 19, "prompt", None, None, -42, 10)
    assert report == {
        "boot": "b1",
        "fw": "0.2.0",
        "applied_gen": 19,
        "state": "prompt",
        "rssi": -42,
        "uptime_s": 10,
    }
    assert "routine" not in report
    assert "remaining_s" not in report
