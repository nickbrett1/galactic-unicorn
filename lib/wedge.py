"""The wedge journal: what the poller saw, in a file that outlives the reset.

The gap this closes is a division of knowledge. When the CYW43 "deaf radio"
wedge hits, the service can see THAT the panel went quiet - the poll heartbeat
stops (the service's `last_seen_s` grows) - but not WHY, because the only thing
the board could say it over is the radio that is broken. The classification of
each failed poll ("heap" or "link"), the run length that earned a radio cycle,
and whether that cycle actually worked are all board-side facts, and they are
lost twice over: the REPL scrolls away, and a wedge tends to end in a WDT hard
reset (`reset_cause=3`, a latch) that takes RAM counters with it.

So the board keeps its own bounded trace, the same shape as lib/wifihealth.py:
one line per event to a flash file, plus in-RAM counters for the current boot.
The RAM counters are what crosses back to the service - `summary()` rides the
next successful poll as an extra query parameter, so the service's wedge
timeline is joined by the board's own accounting of the same window.

WHAT IS WRITTEN (all to FILE, append-only, one line per event):

  * a HEADER line on the first event of a boot - the boot id and reset cause,
    so lines from a reset loop can be told apart from lines in one session;
  * one POLL line per failed poll - cause, exception, heap, link, status,
    state, and the running failure count;
  * one CYCLE line each time a radio cycle is asked for and each time its
    outcome is known (recovered or not);
  * one RECOVERED line when the poll starts answering again after a run.

COST: one file open per event - events are failures and cycles, not the poll
cadence, so a healthy board writes nothing here at all. Every method is
defensive: an instrument must never take down the thing it measures, and
`summary()` in particular runs inside the reporting path (memo 6.3.4) and so
allocates only a small, fixed string.
"""

import time

FILE = "wedge.log"

# Same cap and reasoning as wifihealth: a device with ~126 KB of heap and no log
# rotation would rather lose old lines than grow a file forever.
LIMIT = 8192


class Journal:
    """Counts poll failures by class and records them. Never raises."""

    def __init__(self, boot="?", log=None, path=FILE, limit=LIMIT):
        self._boot = str(boot)
        self._log = log
        self._path = path
        self._limit = limit
        # Per-boot counters (the durable history is the file itself).
        self.counts = {"heap": 0, "link": 0, "other": 0}
        self.cycles = 0
        self.recovered = 0
        self.peak_run = 0
        self.runs = 0
        self._run = 0
        self._started = time.ticks_ms()
        self._opened = False

    # --- what it records --------------------------------------------------

    def note_failure(self, cause, where, exc, free, link, status=None, state=None):
        """One failed poll. `cause` is classify_failure's verdict (remote.py)."""
        try:
            if cause in self.counts:
                self.counts[cause] += 1
            self._run += 1
            if self._run > self.peak_run:
                self.peak_run = self._run
            self._header()
            self._write(
                "poll fail cause="
                + str(cause)
                + " where="
                + str(where)
                + " exc="
                + repr(exc)
                + " heap="
                + str(free)
                + " link="
                + str(link)
                + " status="
                + str(status)
                + " state="
                + str(state)
                + " run="
                + str(self._run)
            )
        except Exception:  # noqa: BLE001 - recording must never raise
            pass

    def note_cycle(self, recovered):
        """A radio cycle was asked for (`recovered is None`) or finished."""
        try:
            if recovered is None:
                self._write("radio cycle requested after " + str(self._run) + " fails")
                self._run = 0
                self.runs += 1
                return
            self.cycles += 1
            if recovered:
                self.recovered += 1
            self._write(
                "radio cycle #"
                + str(self.cycles)
                + " recovered="
                + str(bool(recovered))
            )
        except Exception:  # noqa: BLE001
            pass

    def note_recovery(self, run):
        """The poll answered again after `run` consecutive failures."""
        try:
            self._write("poll recovered after " + str(run) + " consecutive failures")
            if run > self.peak_run:
                self.peak_run = run
            self._run = 0
        except Exception:  # noqa: BLE001
            pass

    def summary(self):
        """A compact per-boot tally for the poll report. Fixed small string.

        Shape: `heap<H>.link<L>.other<O>.cy<C>.rec<R>.pk<P>` where H/L/O count
        the failure classes, C/R the cycles attempted/recovered, and P the peak
        run. Deliberately dot-joined and short so it is safe as a query value
        and cheap to allocate in the reporting path.
        """
        try:
            return (
                "heap"
                + str(self.counts["heap"])
                + ".link"
                + str(self.counts["link"])
                + ".other"
                + str(self.counts["other"])
                + ".cy"
                + str(self.cycles)
                + ".rec"
                + str(self.recovered)
                + ".pk"
                + str(self.peak_run)
            )
        except Exception:  # noqa: BLE001 - a summary must never break a poll
            return "?"

    # --- the file ---------------------------------------------------------

    def _header(self):
        """One line per boot, written lazily on the first event.

        Lazy rather than at construction because the journal exists on a board
        that may never wedge: a healthy board should not pay a line a boot for a
        trace that would only ever say "nothing happened".
        """
        if self._opened:
            return
        self._opened = True
        cause = "?"
        try:
            import machine

            cause = machine.reset_cause()
        except Exception:  # noqa: BLE001, S110
            pass
        self._start_over()
        self._write("=== boot " + self._boot + " reset_cause=" + str(cause) + " ===")

    def _stamp(self):
        return "boot+" + str(time.ticks_diff(time.ticks_ms(), self._started) // 1000) + "s"

    def _write(self, text):
        line = "wedge: " + self._stamp() + " " + text
        if self._log is not None:
            try:
                self._log(line)
            except Exception:  # noqa: BLE001, S110 - the console is optional
                pass
        try:
            with open(self._path, "a") as fh:
                fh.write(line + "\n")
        except Exception:  # noqa: BLE001, S110 - logging must never be fatal
            pass

    def _start_over(self):
        """Cap the file, once per boot, before the header is written."""
        try:
            import os

            if os.stat(self._path)[6] <= self._limit:
                return
        except Exception:  # noqa: BLE001 - a missing file is the normal case
            return
        try:
            with open(self._path, "w") as fh:
                fh.write(
                    "wedge: === log hit the "
                    + str(self._limit)
                    + "-byte cap, starting over ===\n"
                )
        except Exception:  # noqa: BLE001, S110
            pass
