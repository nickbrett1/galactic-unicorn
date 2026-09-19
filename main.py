"""Firmware entry point.

MicroPython runs this file on boot. Imports resolve from the filesystem root
and from lib/, so reusable modules live in lib/ and are imported by their
module name (see lib/example.py).
"""

import config
from example import describe_board


def main():
    print(describe_board(config.BOARD))


if __name__ == "__main__":
    main()
