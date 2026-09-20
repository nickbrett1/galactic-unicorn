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
# boot.py first: it runs before main.py and is where the remote updater hooks
# in. It is deliberately NOT in the update pack, so it only ever changes here.
mpremote connect "$port" fs mkdir :lib 2>/dev/null || true
mpremote connect "$port" fs cp boot.py config.py main.py routines.json :
mpremote connect "$port" fs cp config_secrets.py :
mpremote connect "$port" fs cp lib/*.py :lib/

# 4. Restart. Soft reset re-runs main.py without re-enumerating USB, which
#    matters here: a USB re-enumeration can drop the container's serial node.
mpremote connect "$port" soft-reset

echo "deploy: done - watch for 'unicorn: ntp: synced' on the board's REPL"
