#!/bin/bash
# MicroPython capability setup.
#
# Installs the board toolchain and resolves the port ONCE, at setup time, so a
# missing board surfaces here as a warning rather than the first time someone
# tries to flash something. This runs after the dialout grant is applied, so it
# exercises the real permission path.
#
# F6: access is exclusive. If the host (e.g. Thonny) holds the port, this
# cannot probe it - that is expected, not a failure.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$(dirname "$SCRIPT_DIR")"

echo "INFO: Installing MicroPython board packages (mpremote)..."
# shellcheck disable=SC2086
pip install --quiet --disable-pip-version-check mpremote || {
  echo "WARN: pip install of MicroPython packages failed; board tooling may be missing." >&2
}

# Resolve the port once. find-board.sh exits non-zero with a diagnostic on
# stderr when nothing answers, which is fine - the devcontainer is usable
# without hardware attached.
board="$(scripts/find-board.sh 2>/dev/null || true)"
if [ -n "$board" ]; then
  echo "INFO: MicroPython board detected at $board"
else
  echo "note: no board detected at setup time. That is a warning, not an error --"
  echo "      the devcontainer is usable without hardware attached."
fi
