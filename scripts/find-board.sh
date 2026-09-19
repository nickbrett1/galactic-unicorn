#!/usr/bin/env bash
# Resolve the MicroPython board's serial port inside this devcontainer.
#
# F3/F9: never hard-code a port. The name encodes USB topology (so it changes
# on replug) and the node's major is dynamically assigned by OrbStack.
# F11: more than one usbmodem node can exist, so enumerate and PROBE.
# F6:  access is exclusive. While this runs it holds the port, and while the
#      host holds it this cannot probe it - close Thonny first.
set -euo pipefail
shopt -s nullglob

candidates=()
for node in /dev/tty.usbmodem* /dev/tty.usbserial*; do
  [[ -c "$node" && -r "$node" ]] && candidates+=("$node")
done

if (( ${#candidates[@]} == 0 )); then
  echo "no candidate serial ports found." >&2
  echo "  - is the board plugged into the host? (OrbStack forwards it into the VM automatically)" >&2
  echo "  - did this shell start before the dialout grant? open a fresh terminal." >&2
  echo "  - is the port held on the macOS side? access is exclusive - close Thonny." >&2
  exit 1
fi

# A MicroPython REPL answers immediately; other serial devices do not.
responsive=()
for node in "${candidates[@]}"; do
  if timeout 5 mpremote connect "$node" exec 'pass' >/dev/null 2>&1; then
    responsive+=("$node")
  fi
done

if (( ${#responsive[@]} == 1 )); then echo "${responsive[0]}"; exit 0; fi
if (( ${#responsive[@]} > 1 )); then
  echo "several ports answered as MicroPython: ${responsive[*]}" >&2; exit 2
fi

# Nothing answered. A single candidate is still the best guess: the board may
# be running non-MicroPython firmware, or be mid-reset.
if (( ${#candidates[@]} == 1 )); then echo "${candidates[0]}"; exit 0; fi

echo "no candidate answered as a MicroPython REPL; candidates: ${candidates[*]}" >&2
exit 3
