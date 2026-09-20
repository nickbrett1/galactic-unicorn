#!/usr/bin/env python3
"""Regression tests for _recover(), the rollback decision. Host-only, no board.

    python3 tests/test_recover.py

The state is three small files on the board's filesystem, so the whole decision
is testable here by laying those files out and calling the real _recover().
`machine.reset_cause()` is the one input that has to be simulated.

The case worth keeping is `wedge on first boot`: a release that reached
main.py's boot-ok write and THEN wedged. It wrote boot-ok, so the original
three-state rule called it healthy on the next boot and the board reset-looped
forever - the watchdog turning "wedged forever" into "reset-loop forever".
Machine.reset_cause() == WDT_RESET is the evidence that closes it.
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


def lay_out_tree(tmp, boot_ok, boot_try, version="0.2.0", prev_version="0.1.10"):
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


def case(name, boot_ok, boot_try, wdt, want_rollback, want_version):
    tmp = tempfile.mkdtemp()
    try:
        lay_out_tree(tmp, boot_ok, boot_try)
        updater._reset_was_watchdog = lambda: wdt
        rolled_back = updater._recover()
        version = state(updater.VERSION_FILE)
        ok = rolled_back == want_rollback and version == want_version
        verdict = "PASS" if ok else "FAIL"
        print(
            f"{verdict:<4} {name:<44} rollback={rolled_back!s:<6} "
            f"version={version:<8} want={want_rollback}/{want_version}"
        )
        return ok
    finally:
        os.chdir("/")
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    results = [
        # THE GAP. Came up - boot-ok written by this very boot - then wedged.
        case("wedge on first boot -> roll back", "0.2.0", "0.2.0", True, True, "0.1.10"),
        # Same files, no watchdog: a clean reboot is not evidence of anything bad.
        case("clean reboot, proven release -> keep", "0.2.0", "0.2.0", False, False, "0.2.0"),
        # boot-try retired, so it has survived at least one restart before. A
        # hang now must not condemn a release that has been running for days.
        case("wedge after proving itself -> keep", "0.2.0", None, True, False, "0.2.0"),
        # Never came up at all. Handled before the watchdog existed; must not regress.
        case("never came up -> roll back", "0.1.10", "0.2.0", False, True, "0.1.10"),
        # First boot of a release: its chance, and no judgement yet.
        case("first boot -> no judgement", "0.1.10", "0.1.10", False, False, "0.2.0"),
    ]
    print()
    print(f"{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
