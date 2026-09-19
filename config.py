"""Board configuration.

Keep tunables here rather than hard-coding them across modules.
"""

# Board this firmware targets (see the README for how it is driven).
BOARD = "galactic-unicorn"

# RP2 silicon variant this build is for (rp2040 | rp2350 | unknown). Selected
# with the BOARD but on a separate axis: the MicroPython .uf2 is chosen by
# chip, and one product can ship with more than one variant (a Galactic
# Unicorn has been sold as both an RP2040 and a Pico 2 W / RP2350 carrier).
# Confirm it from the board's own banner, never from the USB PID.
CHIP = "rp2040"
