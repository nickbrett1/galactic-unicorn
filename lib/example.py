"""Example firmware module.

Anything importable from firmware lives in lib/, which is on the board's
import path. Keep hardware access (machine, neopixel, ...) inside functions so
the module stays importable for linting and host-side tests.
"""


def describe_board(board):
    return "board: " + board
