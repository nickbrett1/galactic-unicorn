"""MicroPython runs this before main.py on every boot.

It runs the leader update and NOTHING else. This file is deliberately tiny and
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
except Exception as exc:  # noqa: BLE001 - must never stop main.py from running
    print("boot: updater skipped:", repr(exc))
