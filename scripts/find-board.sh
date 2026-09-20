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
#
# RETRY, because a resetting board is invisible: an armed watchdog means the
# board now re-enumerates USB on its own schedule, so its node is simply absent
# for a few seconds at a time. Probing once conflates "not a REPL" with "not
# there yet" - and the fallback below then hands back whatever node IS present,
# which in this devcontainer is the LG monitor's USB serial. Re-scan and re-probe
# until a board answers, or until we have waited longer than a reset can last.
ATTEMPTS=6
for ((round = 1; round <= ATTEMPTS; round++)); do
  # Reset per round: a port that answered in round 1 and again in round 2 would
  # otherwise be counted twice and read as "several ports answered".
  candidates=()
  responsive=()
  for node in /dev/tty.usbmodem* /dev/tty.usbserial*; do
    [[ -c "$node" && -r "$node" ]] && candidates+=("$node")
  done

  for node in "${candidates[@]}"; do
    if timeout 5 mpremote connect "$node" exec 'pass' >/dev/null 2>&1; then
      responsive+=("$node")
    fi
  done

  if (( ${#responsive[@]} == 1 )); then echo "${responsive[0]}"; exit 0; fi
  if (( ${#responsive[@]} > 1 )); then
    echo "several ports answered as MicroPython: ${responsive[*]}" >&2; exit 2
  fi

  # `if`, not `(( ... )) && ...`: under `set -e` a false arithmetic test returns
  # status 1 and would exit the script on the final round - the one round where
  # falling through to the diagnostics below is the whole point.
  if (( round < ATTEMPTS )); then
    echo "find-board: round ${round}/${ATTEMPTS} found no REPL; retrying" >&2
    sleep 3
  fi
done

if (( ${#candidates[@]} == 1 )); then
  echo "find-board: ${candidates[0]} is the only node but never answered as a" >&2
  echo "  MicroPython REPL after ${ATTEMPTS} rounds. Refusing to guess: a lone" >&2
  echo "  node can be some other bridge (the LG monitor looks identical)." >&2
  echo "  Power-cycle the board, then re-run." >&2
  exit 3
fi

echo "no candidate answered as a MicroPython REPL; candidates: ${candidates[*]}" >&2
exit 3
