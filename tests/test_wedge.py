#!/usr/bin/env python3
"""Regression tests for the wedge journal. Host-only, no board.

    python3 tests/test_wedge.py

The journal exists so that a CYW43 wedge is diagnosable AFTER the fact: the
service can only see that the poll heartbeat stopped, and a wedge usually ends
in a WDT hard reset that wipes RAM, so the classification of each failed poll
and the radio-cycle outcomes have to be on flash. So the behaviours under test
are the ones that carry that reading - a header per boot, a line per event with
the class and the run length, a recoverable tally - plus the bound that keeps
the file from growing forever.

MicroPython's time.ticks_ms has no host equivalent, so the clock is shimmed
before importing the module, exactly as test_wifihealth.py does.
"""

import os
import shutil
import sys
import tempfile
import time

# --- the board's clock, on the host. MUST precede `import wedge`.
_TICKS = [0]
time.ticks_ms = lambda: _TICKS[0]
time.ticks_diff = lambda now, then: now - then

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

import wedge

FAILED = []


def tick(ms):
    _TICKS[0] += ms


def lines():
    with open(wedge.FILE) as fh:
        return [line for line in fh.read().splitlines() if line.strip()]


def with_tmp(name, body):
    """Run one case in a scratch cwd, like test_wifihealth does."""
    tmp = tempfile.mkdtemp()
    cwd = os.getcwd()
    try:
        os.chdir(tmp)
        return body()
    finally:
        os.chdir(cwd)
        shutil.rmtree(tmp, ignore_errors=True)


def report(name, ok, detail=""):
    if not ok:
        FAILED.append(name)
    print(f"{'PASS' if ok else 'FAIL':<4} {name:<52} {detail}")
    return ok


def case_header_per_boot():
    """One header line, written on the first event, naming boot and reset cause."""

    def body():
        j = wedge.Journal("b1", log=lambda *_: None)
        j.note_failure("link", "poll", "OSError(110)", 100000, True)
        j.note_failure("link", "poll", "OSError(110)", 100000, True)
        ls = lines()
        headers = [line for line in ls if "=== boot b1" in line]
        return report(
            "one header per boot, on first event only",
            len(headers) == 1 and "reset_cause=" in headers[0],
            f"headers={len(headers)}",
        )

    return with_tmp("hdr", body)


def case_class_counts_and_run():
    """Each failure lands in its class, and the run/peak track the streak."""

    def body():
        j = wedge.Journal("b1", log=lambda *_: None)
        j.note_failure("heap", "poll", "OSError(12)", 3000, True)
        j.note_failure("link", "poll", "OSError(110)", 100000, True)
        j.note_failure("link", "poll", "OSError(110)", 100000, True)
        j.note_failure("other", "poll", "ValueError()", 100000, True)
        ok = (
            j.counts["heap"] == 1
            and j.counts["link"] == 2
            and j.counts["other"] == 1
            and j.peak_run == 4
        )
        return report("failures classified and tallied", ok, f"counts={j.counts}")

    return with_tmp("cls", body)


def case_cycle_and_recovery():
    """A requested cycle ends the run; a completed cycle counts and is scored."""

    def body():
        j = wedge.Journal("b1", log=lambda *_: None)
        for _ in range(3):
            j.note_failure("link", "poll", "OSError(110)", 100000, True)
        j.note_cycle(None)  # requested, after 3 fails
        run_after_request = j._run
        j.note_cycle(False)  # did not recover
        j.note_cycle(None)
        j.note_cycle(True)  # recovered
        ok = (
            run_after_request == 0
            and j.cycles == 2
            and j.recovered == 1
            and j.runs == 2
        )
        return report(
            "cycle requested resets run; cycles scored",
            ok,
            f"cycles={j.cycles} rec={j.recovered} runs={j.runs}",
        )

    return with_tmp("cyc", body)


def case_summary_shape():
    """The poll-report summary is the agreed compact shape and never raises."""

    def body():
        j = wedge.Journal("b1", log=lambda *_: None)
        j.note_failure("link", "poll", "OSError(110)", 1, True)
        j.note_cycle(None)
        j.note_cycle(True)
        s = j.summary()
        ok = s == "heap0.link1.other0.cy1.rec1.pk1"
        return report("summary is the compact tally", ok, s)

    return with_tmp("sum", body)


def case_recovery_line():
    """A recovery is recorded with the length of the run it closed."""

    def body():
        j = wedge.Journal("b1", log=lambda *_: None)
        j.note_failure("link", "poll", "OSError(110)", 100000, True)
        j.note_failure("link", "poll", "OSError(110)", 100000, True)
        j.note_recovery(2)
        rec = [line for line in lines() if "recovered after 2" in line]
        return report("recovery line names the run", len(rec) == 1, f"rec={len(rec)}")

    return with_tmp("rec", body)


def case_cap():
    """Past the cap the file starts over rather than growing without bound."""

    def body():
        # Write the file past the cap before the first event, so the lazy
        # _start_over() (run once, before the header) has to act.
        with open(wedge.FILE, "w") as fh:
            fh.write("x" * (wedge.LIMIT + 1))
        j = wedge.Journal("b1", log=lambda *_: None)
        j.note_failure("link", "poll", "OSError(110)", 1, True)
        ls = lines()
        capped = any("hit the" in line and "cap" in line for line in ls)
        return report("file is capped, once per boot", capped, f"lines={len(ls)}")

    return with_tmp("cap", body)


def case_never_raises():
    """An instrument must not take down the thing it measures."""

    def body():
        j = wedge.Journal("b1", log=None)  # no console at all
        # A cause outside the known set must not raise, and must not be counted.
        j.note_failure("bogus", "poll", None, None, None)
        s = j.summary()
        ok = s.startswith("heap0.link0.other0")
        return report("unknown class and no log do not raise", ok, s)

    return with_tmp("safe", body)


def main():
    _TICKS[0] = 0
    cases = [
        case_header_per_boot(),
        case_class_counts_and_run(),
        case_cycle_and_recovery(),
        case_summary_shape(),
        case_recovery_line(),
        case_cap(),
        case_never_raises(),
    ]
    print()
    print(f"{sum(cases)}/{len(cases)} passed")
    return 0 if all(cases) else 1


if __name__ == "__main__":
    sys.exit(main())
