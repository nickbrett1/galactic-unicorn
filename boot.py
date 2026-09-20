"""MicroPython runs this before main.py on every boot.

It runs the latest-release update and NOTHING else. This file is deliberately tiny and
deliberately does not touch the display, the buttons or the routine engine:
whatever breaks in a release, boot.py still runs, so a bad release is repaired
on the next boot instead of bricking the board. That is the whole safety story
of the OTA design.

Two rules, both load-bearing:

  1. Never add anything here that can stop main.py from running. Returning
     normally is the only correct exit; a failure must be swallowed.
  2. boot.py (and lib/updater.py) are EXCLUDED from the firmware pack. They are
     the only code that can repair everything else, so they must not be
     deliverable over the same channel they repair. They change only over USB.

If the wifi join succeeds here, main.py's NTP step reuses the live connection
(its sync_ntp only joins when not already connected), so this costs no second
join.
"""

try:
    import updater

    updater.run()
# BaseException, not Exception: a Ctrl-C from an attached mpremote (or a
# brown-out) raises KeyboardInterrupt, which Exception does NOT catch - and an
# interrupt during the updater's network phase would then abort the whole boot
# and main.py would never run. That is rule 1 above being broken by the one
# thing most likely to happen while someone is standing over the board.
except BaseException as exc:  # noqa: BLE001
    print("boot: updater skipped:", repr(exc))
