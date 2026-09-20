#!/usr/bin/env python3
"""Regression tests for _recover(), the rollback decision. Host-only, no board.

    python3 tests/test_recover.py

The decision reads three small files on the board's filesystem, so laying those
files out and calling the real _recover() covers it without a board. Boot
sequencing - which is where the bugs have actually been - is exercised by
driving the board, not here.

`never came up` is now doing double duty, and deliberately so. Two different
failures reach _recover() in the same shape, with boot-ok behind and boot-try
spent:

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


def case(name, boot_ok, boot_try, want_rollback, want_version):
    tmp = tempfile.mkdtemp()
    try:
        lay_out_tree(tmp, boot_ok, boot_try)
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


def main():
    results = [
        # Proven before: nothing to do, and boot-try is retired.
        case("boot-ok matches -> keep", "0.2.0", "0.2.0", False, "0.2.0"),
        # The one that matters: it had its chance and never reported in. Covers
        # both a crash on the way up and a wedge in the loop.
        case("spent chance, no boot-ok -> roll back", "0.1.10", "0.2.0", True, "0.1.10"),
        # First boot of a release: its chance, and no judgement yet.
        case("first boot -> no judgement", "0.1.10", "0.1.10", False, "0.2.0"),
    ]
    print()
    print(f"{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
