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

# 1. Secrets: Doppler -> config_secrets.py (gitignored).
./scripts/gen-secrets.sh

# 2. Find the board (probes candidates; rejects the LG monitor's node).
port=$(scripts/find-board.sh)
echo "deploy: board on ${port}"

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
feed() { mpremote connect "$port" exec 'import machine; machine.WDT(timeout=8000)' 2>/dev/null || true; }

# ORDER MATTERS, and it is lib/ BEFORE main.py. main.py does `import watchdog`
# at the top, so a board that receives the new main.py before the new lib/ dies
# on ImportError - and because an armed fuse outlives a reset, it dies *every*
# 8 s, which is a slow and confusing way to discover a missing file. lib/ first,
# and the entry point last, means there is no moment at which main.py can run
# against a lib/ it cannot satisfy.
feed
mpremote connect "$port" fs cp boot.py config.py routines.json :
feed
mpremote connect "$port" fs cp config_secrets.py :
feed
mpremote connect "$port" fs cp lib/*.py :lib/
# main.py last, and on its own line, for the reason above.
feed
mpremote connect "$port" fs cp main.py :

# 4. Restart. Soft reset re-runs main.py without re-enumerating USB, which
#    matters here: a USB re-enumeration can drop the container's serial node.
mpremote connect "$port" soft-reset

echo "deploy: done - watch for 'unicorn: ntp: synced' on the board's REPL"
