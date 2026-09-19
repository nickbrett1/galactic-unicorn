"""Example device-local secrets. COPY TO config_secrets.py AND FILL IN.

`config_secrets.py` is gitignored; this example is the committed shape of it.

Nothing here is needed in phase 1 - WiFi exists only so the ambient clock can
reach NTP, and every other feature works with it switched off (memo section 6).
"""

WIFI_SSID = "your-ssid"
WIFI_PASSWORD = "your-password"
