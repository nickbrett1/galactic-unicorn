"""MicroPython runs this before main.py on every boot.

It lights one pixel and runs the latest-release update, and NOTHING else. It is
deliberately free of the buttons and the routine engine: whatever breaks in a
release, boot.py still runs, so a bad release is repaired on the next boot
instead of bricking the board. That is the whole safety story of the OTA design.

Two rules, both load-bearing:

  1. Never add anything here that can stop main.py from running. Returning
     normally is the only correct exit; a failure must be swallowed.
  2. boot.py (and lib/updater.py) are EXCLUDED from the firmware pack. They are
     the only code that can repair everything else, so they must not be
     deliverable over the same channel they repair. They change only over USB.

If the wifi join succeeds here, main.py's NTP step reuses the live connection
(its sync_ntp only joins when not already connected), so this costs no second
join.

The one exception to "does not touch the display" is the power pixel below, and
it is written to keep both rules intact rather than to bend them - see its own
comment for why the panel would otherwise be black for twenty seconds, and why
the pixel cannot be drawn from main.py instead.
"""

# One white LED in the top-left corner, lit before the update phase and left
# lit for main.py to take over. Same LED, same corner as
# lib/display.py's POWER_PIXEL; the coordinate is repeated rather than
# imported because display.py is pack-delivered and boot.py must know nothing
# about the pack (rule 2).
#
# WHY HERE AND NOT IN main.py: this is the longest dark stretch of the boot.
# Measured on this board, 2026-09-21, on a cold radio:
#
#     recover   = 7 ms
#     join_wifi = 20070 ms     (three 10 s DHCP attempts; two got no IP)
#
# and config.py documents the same phase as "up to ~30 s in a dead window".
# main.py cannot run until all of it is over, so an indicator living in main.py
# appears after twenty seconds and then the HELLO banner replaces it - which is
# precisely what was reported: "it takes a really long time for the white icon
# to appear and then it's gone".
#
# WHY IT DOES NOT BREAK RULE 1: everything is wrapped, so a missing module, a
# display that will not allocate, or a Ctrl-C from an attached mpremote all end
# up at the `except` and the boot carries on to updater.run() and then to
# main.py. Nothing here can raise past this function, and this function cannot
# fail to return.
#
# WHY IT DOES NOT BREAK RULE 2: the only modules it touches are `galactic` and
# `picographics`, which are FROZEN INTO THE FIRMWARE IMAGE and are never
# delivered over OTA. It deliberately does NOT import lib/display.py, or
# main.py, or anything else the pack can change - a release cannot take the
# power light away, because the power light does not read a single byte of
# release code.
POWER_PIXEL_X = 0
POWER_PIXEL_Y = 0
# Mirrors config.BRIGHTNESS_COUNTDOWN. Hard-coded for the same reason the
# coordinate is: config.py is pack-delivered too.
POWER_PIXEL_BRIGHTNESS = 0.45


def _power_pixel():
    """Light the power pixel. Never raises, never blocks, never returns a value.

    Constructs its own GalacticUnicorn and PicoGraphics rather than borrowing
    main.py's Display, because there is no Display yet at this point in the
    boot - the whole reason this exists is that main.py has not started.
    Measured on the board: a second GalacticUnicorn is harmless (it carries no
    Python-level timer, so nothing is left ticking twice), and the pixel it
    draws stays on the panel until main.py's first frame clears it.
    """
    try:
        try:
            from galactic import GalacticUnicorn  # stock 1.19.1 firmware
        except ImportError:
            from galactic_unicorn import GalacticUnicorn  # newer builds
        from picographics import DISPLAY_GALACTIC_UNICORN, PicoGraphics

        gu = GalacticUnicorn()
        graphics = PicoGraphics(display=DISPLAY_GALACTIC_UNICORN)
        try:
            gu.set_brightness(POWER_PIXEL_BRIGHTNESS)
        except Exception:  # noqa: BLE001, S110 - a dim lamp still beats none
            pass
        graphics.set_pen(graphics.create_pen(255, 255, 255))
        graphics.pixel(POWER_PIXEL_X, POWER_PIXEL_Y)
        gu.update(graphics)
    except BaseException as exc:  # noqa: BLE001 - rule 1: main.py MUST still run
        print("boot: no power pixel:", repr(exc))


# First, before the network, and outside the updater's handler below: it cannot
# raise (see _power_pixel), and if it ever did it would be a power light that
# failed, not a skipped update. Whatever else this boot does, the panel says
# "powered" from here on.
_power_pixel()

try:
    import updater

    updater.run()
# BaseException, not Exception: a Ctrl-C from an attached mpremote (or a
# brown-out) raises KeyboardInterrupt, which Exception does NOT catch - and an
# interrupt during the updater's network phase would then abort the whole boot
# and main.py would never run. That is rule 1 above being broken by the one
# thing most likely to happen while someone is standing over the board.
except SystemExit:
    # Not a failure: updater._reset() raises SystemExit to ask for the reboot
    # that starts the firmware it just applied. Reporting it as a skipped
    # update is what made every SUCCESSFUL apply look like a problem:
    #
    #     update: applied 0.1.12 (65181 bytes, 12 files)
    #     boot: updater skipped: SystemExit()
    #
    # Two lines that read as a contradiction, and the first one is the true one.
    pass
except BaseException as exc:  # noqa: BLE001
    print("boot: updater skipped:", repr(exc))
