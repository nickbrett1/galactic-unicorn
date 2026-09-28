#!/usr/bin/env python3
"""Regression tests for _recover(), the rollback decision. Host-only, no board.

    python3 tests/test_recover.py

The decision reads three small files on the board's filesystem, so laying those
files out and calling the real _recover() covers it without a board. Boot
sequencing - which is where the bugs have actually been - is exercised by
driving the board, not here.

`did not come up in N boots` is doing double duty, and deliberately so. Two
different failures reach _recover() in the same shape, with boot-ok behind and
its boot chances spent:

  * a release that dies on the way up, before it can write boot-ok
  * a release that starts, draws its banner and then wedges, so the loop never
    soaks long enough to write boot-ok and the watchdog resets the board

Both are "it never proved itself", so both roll back, and the marker being
written by the render loop rather than at the banner is what makes the second
one visible. A release that ran fine and wedged later has already written
boot-ok, so it is not judged here at all - a hang after days must not discard
a release that has been working.
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

import updater


def lay_out_tree(
    tmp,
    boot_ok,
    boot_try,
    version="0.2.0",
    prev_version="0.1.10",
    boot_fails=None,
    boot_wdt=None,
):
    """A board-ish tree: a running release plus the rollback slot that judges it."""
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    os.chdir(tmp)

    with open(updater.VERSION_FILE, "w") as fh:
        fh.write(version)
    with open(updater.BOOT_OK_FILE, "w") as fh:
        fh.write(boot_ok)
    if boot_try is not None:
        with open(updater.BOOT_TRY_FILE, "w") as fh:
            fh.write(boot_try)
    if boot_fails is not None:
        with open(updater.BOOT_FAILS_FILE, "w") as fh:
            fh.write(str(boot_fails))
    if boot_wdt is not None:
        with open(updater.BOOT_WDT_FILE, "w") as fh:
            fh.write(str(boot_wdt))

    # The rollback copy, hashed exactly as the board would have written it -
    # _rollback verifies every hash before it moves anything.
    os.makedirs(updater.PREV_DIR)
    body = b"PREVIOUS MAIN\n"
    with open(os.path.join(updater.PREV_DIR, "main.py"), "wb") as fh:
        fh.write(body)
    info = {
        "version": prev_version,
        "files": [
            {
                "path": "main.py",
                "sha256": hashlib.sha256(body).hexdigest(),
                "size": len(body),
            }
        ],
        "managed": ["main.py"],
    }
    with open(updater.PREV_INFO, "w") as fh:
        json.dump(info, fh)


def state(name):
    """Contents of a state file, or None - _write appends a newline, _read strips."""
    try:
        with open(name) as fh:
            return fh.read().strip()
    except OSError:
        return None


def case(name, boot_ok, boot_try, want_rollback, want_version, boot_fails=None):
    tmp = tempfile.mkdtemp()
    try:
        lay_out_tree(tmp, boot_ok, boot_try, boot_fails=boot_fails)
        rolled_back = updater._recover()
        version = state(updater.VERSION_FILE)
        ok = rolled_back == want_rollback and version == want_version
        verdict = "PASS" if ok else "FAIL"
        print(
            f"{verdict:<4} {name:<46} rollback={rolled_back!s:<6} "
            f"version={version:<8} want={want_rollback}/{want_version}"
        )
        return ok
    finally:
        os.chdir("/")
        shutil.rmtree(tmp, ignore_errors=True)


def case_proven_slot_dropped_in_session():
    """A proven release's slot is dropped by the RUNNING app, not only at boot.

    _recover drops it on the boot after boot-ok is written, and nothing else
    did - so until the next reboot the ~167 KB slot sat there for the whole
    session, which is exactly the flash the in-loop update needs. Measured on
    the board 2026-09-27: free 424 KB clean, 44 KB with the slot resident.
    """
    tmp = tempfile.mkdtemp()
    try:
        lay_out_tree(tmp, "0.2.0", None)
        updater._drop_proven_rollback()
        gone = not os.path.exists(updater.PREV_DIR) and not os.path.exists(
            updater.PREV_INFO
        )
        verdict = "PASS" if gone else "FAIL"
        print(f"{verdict:<4} {'proven slot is dropped in-session too':<46} gone={gone}")
        return gone
    finally:
        os.chdir("/")
        shutil.rmtree(tmp, ignore_errors=True)


def case_unproven_slot_is_kept_in_session():
    """... but an unproven release must NOT lose the copy it may roll back to."""
    tmp = tempfile.mkdtemp()
    try:
        lay_out_tree(tmp, "0.1.10", "0.2.0")  # boot-ok still names the previous
        updater._drop_proven_rollback()
        kept = os.path.exists(updater.PREV_DIR) and os.path.exists(updater.PREV_INFO)
        verdict = "PASS" if kept else "FAIL"
        print(f"{verdict:<4} {'unproven slot is kept in-session':<46} kept={kept}")
        return kept
    finally:
        os.chdir("/")
        shutil.rmtree(tmp, ignore_errors=True)


def case_proven_drops_slot():
    """A proven release must not keep its rollback copy.

    The copy is re-created before the next apply, so once boot-ok matches the
    running version it is dead weight. On this board's 768 KB filesystem a
    permanently resident ~170 KB slot was itself enough to make every later
    update fail with OSError(28), ENOSPC (measured 2026-09-27).
    """
    tmp = tempfile.mkdtemp()
    try:
        lay_out_tree(tmp, "0.2.0", None)
        updater._recover()
        gone = not os.path.exists(updater.PREV_DIR) and not os.path.exists(
            updater.PREV_INFO
        )
        verdict = "PASS" if gone else "FAIL"
        print(f"{verdict:<4} {'proven release drops its rollback slot':<46} gone={gone}")
        return gone
    finally:
        os.chdir("/")
        shutil.rmtree(tmp, ignore_errors=True)


def case_no_blacklist():
    """A rolled-back release must stay adoptable, and nothing else must be left.

    This is a regression test for a real deadlock. On hardware the board applied
    v0.1.11, was interrupted before main.py could soak long enough to write
    boot-ok, rolled back - and wrote a bad.txt. _update then refused 0.1.11
    before it so much as looked at the network, so a perfectly good release was
    unreachable until bad.txt was deleted by hand. Three rounds of that.

    There is deliberately no such file now: the fix for a bad release is a new
    release on top of it. The hasattr check is the point of the test - it fails
    if the blacklist is ever reintroduced.
    """
    tmp = tempfile.mkdtemp()
    try:
        lay_out_tree(tmp, "0.1.10", "0.2.0", boot_fails=updater.BOOT_FAILS_MAX)
        updater._recover()
        leftovers = sorted(
            name
            for name in os.listdir(tmp)
            if "bad" in name
            or name
            in (
                updater.BOOT_TRY_FILE,
                updater.BOOT_FAILS_FILE,
                updater.PREV_INFO,
            )
        )
        ok = not leftovers and not hasattr(updater, "BAD_FILE")
        verdict = "PASS" if ok else "FAIL"
        print(
            f"{verdict:<4} {'rollback leaves nothing blocking':<46} "
            f"leftovers={leftovers} BAD_FILE={hasattr(updater, 'BAD_FILE')}"
        )
        return ok
    finally:
        os.chdir("/")
        shutil.rmtree(tmp, ignore_errors=True)


def case_release_gets_several_boots():
    """A release is not discarded until it has failed to prove itself MAX boots.

    One chance was too few. On 2026-09-27 a single flaky first boot - a low-heap
    MemoryError, a radio wedge that stalled the loop past the soak, a reset
    inside the soak - rolled back a release that was fine, twice. The attempt
    is now a COUNT: every boot that starts the release and does not write
    boot-ok spends one, and only the boot AFTER the last one rolls it back.

    Driven through the real pair boot.py uses (recover then mark_attempt) with
    none of them writing boot-ok, so every boot is a failed one.
    """
    tmp = tempfile.mkdtemp()
    try:
        lay_out_tree(tmp, "0.1.10", None)
        rolled_at = None
        for boot in range(1, updater.BOOT_FAILS_MAX + 3):
            if updater._recover():
                rolled_at = boot
                break
            updater._mark_attempt()
        want = updater.BOOT_FAILS_MAX + 1
        ok = rolled_at == want
        ok = ok and state(updater.VERSION_FILE) == "0.1.10"
        ok = ok and not os.path.exists(updater.BOOT_FAILS_FILE)
        verdict = "PASS" if ok else "FAIL"
        print(
            f"{verdict:<4} {'release runs MAX failed boots, rolled back at MAX+1':<46} "
            f"rolled_at={rolled_at} want={want}"
        )
        return ok
    finally:
        os.chdir("/")
        shutil.rmtree(tmp, ignore_errors=True)


def case_late_proof_keeps_release():
    """A release proven on its last allowed boot is kept, not rolled back.

    The counter must not make the protocol forget the successful case: a
    release that soaks and writes boot-ok on its final chance is exactly as
    proven as one that did it on the first, and _recover must retire its state
    rather than judge it again.
    """
    tmp = tempfile.mkdtemp()
    try:
        lay_out_tree(tmp, "0.1.10", None)
        for _ in range(updater.BOOT_FAILS_MAX - 1):
            assert updater._recover() is False
            updater._mark_attempt()
        # main.py finally feeds the fuse long enough and writes the marker.
        with open(updater.BOOT_OK_FILE, "w") as fh:
            fh.write("0.2.0\n")
        rolled = updater._recover()
        ok = rolled is False and state(updater.VERSION_FILE) == "0.2.0"
        ok = ok and not os.path.exists(updater.BOOT_FAILS_FILE)
        ok = ok and not os.path.exists(updater.BOOT_TRY_FILE)
        verdict = "PASS" if ok else "FAIL"
        print(
            f"{verdict:<4} {'a late proof keeps the release':<46} "
            f"rolled={rolled} version={state(updater.VERSION_FILE)}"
        )
        return ok
    finally:
        os.chdir("/")
        shutil.rmtree(tmp, ignore_errors=True)


def case_wdt_boot_does_not_spend_a_fail():
    """A watchdog-cut predecessor must NOT charge the release a boot-fails boot.

    This is the field bug (2026-09-27): machine.reset_cause() == 3 means the
    PREVIOUS boot was killed by the WDT latch, not by the release failing to
    prove itself - a reset can land between _mark_attempt and main.py's boot-ok
    write, and 0.1.38 reached 2 of 3 fails purely from those. Here the release
    already sits at 2 fails; a WDT boot must leave that exactly where it was.
    """
    tmp = tempfile.mkdtemp()
    saved = updater._reset_cause
    try:
        lay_out_tree(tmp, "0.1.10", "0.2.0", boot_fails=2)
        updater._reset_cause = lambda: updater.RESET_CAUSE_WDT
        updater._mark_attempt()
        fails = state(updater.BOOT_FAILS_FILE)
        ok = fails == "2"
        verdict = "PASS" if ok else "FAIL"
        print(
            f"{verdict:<4} {'WDT-cut boot does not spend a boot-fail':<46} "
            f"boot-fails={fails!s:<4} want=2"
        )
        return ok
    finally:
        updater._reset_cause = saved
        os.chdir("/")
        shutil.rmtree(tmp, ignore_errors=True)


def case_wdt_boot_bumps_wdt_counter():
    """... it is counted in the separate boot-wdt counter instead."""
    tmp = tempfile.mkdtemp()
    saved = updater._reset_cause
    try:
        lay_out_tree(tmp, "0.1.10", "0.2.0", boot_fails=2, boot_wdt=1)
        updater._reset_cause = lambda: updater.RESET_CAUSE_WDT
        updater._mark_attempt()
        wdt = state(updater.BOOT_WDT_FILE)
        fails = state(updater.BOOT_FAILS_FILE)
        ok = wdt == "2" and fails == "2"
        verdict = "PASS" if ok else "FAIL"
        print(
            f"{verdict:<4} {'WDT-cut boot bumps boot-wdt alone':<46} "
            f"boot-wdt={wdt!s:<4} boot-fails={fails!s:<4} want=2/2"
        )
        return ok
    finally:
        updater._reset_cause = saved
        os.chdir("/")
        shutil.rmtree(tmp, ignore_errors=True)


def case_ordinary_boot_still_spends_a_fail():
    """An ordinary unproven boot must count exactly as it always did.

    The whole mechanism exists for this case, so the WDT carve-out must not
    touch it: a boot that was NOT watchdog-cut still increments boot-fails, and
    must not touch boot-wdt.
    """
    tmp = tempfile.mkdtemp()
    saved = updater._reset_cause
    try:
        lay_out_tree(tmp, "0.1.10", "0.2.0", boot_fails=1)
        updater._reset_cause = lambda: None  # not RESET_CAUSE_WDT
        updater._mark_attempt()
        fails = state(updater.BOOT_FAILS_FILE)
        wdt = state(updater.BOOT_WDT_FILE)
        ok = fails == "2" and wdt is None
        verdict = "PASS" if ok else "FAIL"
        print(
            f"{verdict:<4} {'ordinary unproven boot still spends a fail':<46} "
            f"boot-fails={fails!s:<4} want=2"
        )
        return ok
    finally:
        updater._reset_cause = saved
        os.chdir("/")
        shutil.rmtree(tmp, ignore_errors=True)


def case_wdt_hang_still_rolls_back():
    """The trap: a HANG is also a WDT reset, so it must still roll back.

    A release that wedges is reset by the watchdog every boot, exactly like a
    release cut down by infrastructure. If WDT resets were ignored outright, a
    hanging release would never roll back - an infinite reboot loop, strictly
    worse than the original bug. So boot-wdt has its own, higher ceiling, and
    _recover rolls back when it is reached. Driven through the real pair
    (recover then mark_attempt) with every boot WDT-cut and no boot-ok.
    """
    tmp = tempfile.mkdtemp()
    saved = updater._reset_cause
    try:
        lay_out_tree(tmp, "0.1.10", None)
        updater._reset_cause = lambda: updater.RESET_CAUSE_WDT
        rolled_at = None
        for boot in range(1, updater.BOOT_WDT_MAX + 3):
            if updater._recover():
                rolled_at = boot
                break
            updater._mark_attempt()
        want = updater.BOOT_WDT_MAX + 1
        ok = rolled_at == want
        ok = ok and state(updater.VERSION_FILE) == "0.1.10"
        ok = ok and not os.path.exists(updater.BOOT_WDT_FILE)
        ok = ok and not os.path.exists(updater.BOOT_FAILS_FILE)
        verdict = "PASS" if ok else "FAIL"
        print(
            f"{verdict:<4} {'a WDT hang still rolls back at WDT_MAX+1':<46} "
            f"rolled_at={rolled_at} want={want}"
        )
        return ok
    finally:
        updater._reset_cause = saved
        os.chdir("/")
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    results = [
        # Proven before: nothing to do, and boot-try is retired.
        case("boot-ok matches -> keep", "0.2.0", "0.2.0", False, "0.2.0"),
        # The one that matters: every judged boot spent and never reported in.
        # Covers both a crash on the way up and a wedge in the loop.
        case(
            "chances spent, no boot-ok -> roll back",
            "0.1.10",
            "0.2.0",
            True,
            "0.1.10",
            boot_fails=updater.BOOT_FAILS_MAX,
        ),
        # ... but a release is not discarded until the chances actually run out.
        case("one bad boot -> still kept", "0.1.10", "0.2.0", False, "0.2.0", boot_fails=1),
        # First boot of a release: its first chance, and no judgement yet.
        case("first boot -> no judgement", "0.1.10", "0.1.10", False, "0.2.0"),
        case_proven_drops_slot(),
        case_proven_slot_dropped_in_session(),
        case_unproven_slot_is_kept_in_session(),
        case_no_blacklist(),
        case_release_gets_several_boots(),
        case_late_proof_keeps_release(),
        # A watchdog reset is infrastructure, not a verdict; but a WDT hang is
        # still a verdict, just a later one.
        case_wdt_boot_does_not_spend_a_fail(),
        case_wdt_boot_bumps_wdt_counter(),
        case_ordinary_boot_still_spends_a_fail(),
        case_wdt_hang_still_rolls_back(),
    ]
    print()
    print(f"{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
