#!/usr/bin/env bash
# Resolve the tools a deploy needs -- and PROVE they run.
#
# `mpremote` and `pyserial` live in a venv, not on PATH, and two traps make a
# perfectly good board look dead (both measured, 2026-09-26):
#
#   1. A bare `mpremote` exits 127 ("command not found"), which is
#      indistinguishable from "this node is not a REPL".
#   2. The repo's own `.venv` is created INSIDE the devcontainer proper, and
#      OrbStack syncs it to the host with links that point into the container:
#      `.venv/bin/python3 -> /usr/local/python/current/bin/python3`, and
#      `.venv/bin/mpremote` shebangs `#!/workspaces/galactic-unicorn/.venv/...`.
#      Neither path exists on the Mac. The venv LOOKS installed and is entirely
#      dead -- worse than absent, because `[[ -x ]]` says yes.
#
# So a candidate is accepted only if it actually executes: the python must
# `import serial`, and the mpremote entry point must answer `--help`. Sourced by
# scripts/deploy.sh and scripts/find-board.sh (the same trap would otherwise be
# resolved twice, differently). Never executed directly.
#
# On success, exports:
#
#   PYTHON    a python that can `import serial` (find-board's REPL probe)
#   MPREMOTE  an mpremote entry point that runs
#
# Either may be the empty string; callers decide whether that is fatal.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# First candidate that exists AND runs. `$HOME/.venv-mpremote` is the host-side
# venv a human made by hand; PATH is the last resort.
first_working_python() {
  local cand
  for cand in "$ROOT/.venv/bin/python" "$HOME/.venv-mpremote/bin/python"; do
    if [[ -x "$cand" ]] && "$cand" -c 'import serial' >/dev/null 2>&1; then
      printf '%s\n' "$cand"
      return 0
    fi
  done
  if command -v python3 >/dev/null 2>&1 && python3 -c 'import serial' >/dev/null 2>&1; then
    command -v python3
    return 0
  fi
  return 1
}

first_working_mpremote() {
  local cand
  for cand in "$ROOT/.venv/bin/mpremote" "$HOME/.venv-mpremote/bin/mpremote"; do
    if [[ -x "$cand" ]] && "$cand" --help >/dev/null 2>&1; then
      printf '%s\n' "$cand"
      return 0
    fi
  done
  if command -v mpremote >/dev/null 2>&1 && mpremote --help >/dev/null 2>&1; then
    command -v mpremote
    return 0
  fi
  return 1
}

PYTHON="$(first_working_python || true)"
MPREMOTE="$(first_working_mpremote || true)"

export PYTHON MPREMOTE
