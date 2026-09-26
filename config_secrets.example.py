"""Example device-local secrets. COPY TO config_secrets.py AND FILL IN.

`config_secrets.py` is gitignored; this example is the committed shape of it.

Nothing here is needed in phase 1 - WiFi exists only so the ambient clock can
reach NTP, and every other feature works with it switched off (memo section 6).
"""

WIFI_SSID = "your-ssid"
WIFI_PASSWORD = "your-password"

# Optional static addressing. Leave these out (or None) to use DHCP.
#
# Only safe because the router RESERVES this address for this board's MAC - a
# reservation stops the router handing it to anyone else, but it does not stop
# the board from having to ask every boot. Setting it here is what takes DHCP
# out of the boot path (see config.py). DNS must be a real resolver: the update
# check fetches a github.com URL.
STATIC_IP = "192.168.1.63"
STATIC_MASK = "255.255.255.0"
STATIC_GATEWAY = "192.168.1.1"
STATIC_DNS = "192.168.1.1"

# The shared LAN-only device token the remote service expects on every poll
# (device-protocols.md section 1). Defence in depth only; never a real
# boundary - anyone on the LAN could press the physical button (memo section
# 10). It must never enter the committed tree or the update pack, so it lives
# here and is materialised by scripts/gen-secrets.sh from Doppler. Absent ->
# the remote poll is disabled on the board rather than sent unauthenticated.
REMOTE_DEVICE_TOKEN = "your-device-token"
