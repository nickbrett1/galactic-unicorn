#!/usr/bin/env bash
# One-command deploy. Resolves secrets from Doppler, pushes ALL firmware to the
# board, and restarts it. This is the whole loop - you do not run gen-secrets
# or mpremote by hand.
#
#     ./scripts/deploy.sh
#
# Why this is a host-side step: `config_secrets.py` executes ON the Pico W, and
# the board has no `doppler` CLI, no API token and no TLS stack for it. Doppler
# can only be asked from the container, so the secret is materialised here and
# then copied across.
set -euo pipefail

cd "$(dirname "$0")/.."

# mpremote lives in the project venv, not on the container's PATH (the bare
# name resolves to nothing -> exit 127). Put it on PATH so every direct call
# below - and find-board.sh - uses the same working binary.
export PATH="$PWD/.venv/bin:$PATH"

# 1. Secrets: Doppler -> config_secrets.py (gitignored).
./scripts/gen-secrets.sh

# 2. Find the board (probes candidates; rejects the LG monitor's node).
port=$(scripts/find-board.sh)
echo "deploy: board on ${port}"

# An armed watchdog outlives the mpremote session that interrupts main.py: on
# disconnect the board is left at the REPL with an 8 s fuse burning and no
# code to feed it, so it resets and re-enumerates USB a few seconds later. A
# single mpremote call can therefore land in that gap and die with "failed to
# access <port>". That is transient, not a real error, so retry long enough to
# ride out a reset (the node name is stable across re-enumeration). A genuine
# failure (bad path, ImportError) still surfaces once the retries are spent.
TRIES=60
mpr() {
  local n=0
  until mpremote connect "$port" "$@"; do
    n=$((n+1))
    if (( n >= TRIES )); then
      echo "deploy: mpremote $* failed after ${n} tries" >&2
      return 1
    fi
    sleep 1
  done
}

# 3. Push the firmware. `:lib` may already exist, so ignore mkdir's error.
# boot.py is deliberately NOT in the update pack, so it only ever changes here.
# Neither is lib/updater.py (the pack builder excludes it - a remote updater
# that can replace itself is how you ship a brick). That makes THIS script the
# only way either of them ever reaches the board, including the watchdog feeds
# inside updater._join_wifi / updater._download.
mpremote connect "$port" fs mkdir :lib 2>/dev/null || true

# An armed watchdog outlives a soft reset (lib/watchdog.py), so a board parked
# at the REPL still has an 8 s fuse burning - and Ctrl-C is what put it there.
# Re-arm (which merely reloads the counter) before each push, or a slow copy
# gets cut off halfway through and leaves a half-written tree. Done inline with
# machine.WDT rather than via lib/watchdog.py, so it also works on a board
# whose lib/ predates the module.
feed() {
  local n=0
  until mpremote connect "$port" exec 'import machine; machine.WDT(timeout=8000)' 2>/dev/null; do
    n=$((n+1)); (( n >= TRIES )) && return 0; sleep 1
  done
}

# ORDER MATTERS, and it is lib/ BEFORE main.py. main.py does `import watchdog`
# at the top, so a board that receives the new main.py before the new lib/ dies
# on ImportError - and because an armed fuse outlives a reset, it dies *every*
# 8 s, which is a slow and confusing way to discover a missing file. lib/ first,
# and the entry point last, means there is no moment at which main.py can run
# against a lib/ it cannot satisfy.
feed
mpr fs cp boot.py config.py routines.json :
feed
mpr fs cp config_secrets.py :
feed
mpr fs cp lib/*.py :lib/
# main.py last, and on its own line, for the reason above.
feed
mpr fs cp main.py :

# 4. Stamp version.txt with the release this tree IS.
#
# boot.py and lib/updater.py are in the pack's exclude list, so a USB deploy is
# how the board gets a tree that no release's pack describes - and version.txt
# is the ONLY thing the updater compares the manifest against (`version ==
# current` -> "no update", lib/updater.py:_update). Left at whatever the last
# OTA wrote, the board would see a newer manifest on its next check and re-apply
# a release over a tree that is already ahead of it: a pointless reboot, and, if
# that release predates these files, a silent undo of them.
#
# Defaults to the tag on HEAD (`v0.1.29` -> `0.1.29`); pass one to override.
VERSION="${1:-$(git describe --tags --abbrev=0 2>/dev/null | sed -e 's/^v//')}"
if [ -n "$VERSION" ]; then
  feed
  mpr exec "f = open('version.txt', 'w'); f.write('$VERSION\n'); f.close(); print('version.txt ->', '$VERSION')"
  echo "deploy: stamped version.txt = ${VERSION}"
else
  echo "deploy: no tag to stamp from; the board will read 'dev' until an OTA lands" >&2
fi

# 5. Restart. Soft reset re-runs main.py without re-enumerating USB, which
#    matters here: a USB re-enumeration can drop the container's serial node.
mpr soft-reset

echo "deploy: done - watch for 'unicorn: ntp: synced' on the board's REPL"
