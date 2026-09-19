"""Board configuration.

Keep tunables here rather than hard-coding them across modules.

This file is committed and holds NO secrets. WiFi credentials live in
`config_secrets.py`, which is gitignored (see Appendix C). The import at the
bottom fails soft, so the firmware runs with WiFi switched off.
"""

# Board this firmware targets (see the README for how it is driven).
BOARD = "galactic-unicorn"

# RP2 silicon variant this build is for (rp2040 | rp2350 | unknown). Selected
# with the BOARD but on a separate axis: the MicroPython .uf2 is chosen by
# chip, and one product can ship with more than one variant (a Galactic
# Unicorn has been sold as both an RP2040 and a Pico 2 W / RP2350 carrier).
# Confirm it from the board's own banner, never from the USB PID.
#
# Verified on this board 2026-09-19: os.uname().machine ==
# "Raspberry Pi Pico W with RP2040".
CHIP = "rp2040"

# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

# Brightness ceiling. Full white at max draws just over 1 A, and the HANDOFF
# screen is deliberately a big green fill, so keep the ceiling sensible.
BRIGHTNESS_AMBIENT = 0.10
BRIGHTNESS_PROMPT = 0.45
BRIGHTNESS_COUNTDOWN = 0.45
BRIGHTNESS_HANDOFF = 0.60
BRIGHTNESS_MAX = 0.65

# Auto-dim by the onboard phototransistor. `light()` returns 0-4095.
# Below LIGHT_DARK the room is dark enough that the display should go OFF
# (this is exactly when bathtime happens - see section 4d rule 4).
LIGHT_DARK = 40
LIGHT_DIM = 400

# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------

VOLUME = 0.5
AUDIO_ENABLED = True

# ---------------------------------------------------------------------------
# Timings (milliseconds)
# ---------------------------------------------------------------------------

PROMPT_MS = 3000
HANDOFF_MS = 10000

# D cancels instantly in every active state (decision 11). Set D_CANCEL_HOLD_MS
# above 0 to require a short hold instead - the recorded fallback if a toddler
# turns D into a toy. See the memo, section 4d button-behaviour box.
D_CANCEL_HOLD_MS = 0

# Parent's alternative escape hatch: hold SWITCH_SLEEP for this long.
CANCEL_HOLD_MS = 2000

# Hold a routine button during COUNTDOWN for this long -> +2 minutes.
EXTEND_HOLD_MS = 1000
EXTEND_MINUTES = 2

# Final stretch that pulses green and escalates.
FINAL_STRETCH_S = 30

# ---------------------------------------------------------------------------
# Ambient clock (best-effort NTP; the countdown never touches the network)
# ---------------------------------------------------------------------------

WIFI_ENABLED = False
NTP_HOST = "pool.ntp.org"
UTC_OFFSET_S = 0

# ---------------------------------------------------------------------------
# Secrets (gitignored; absent by default)
# ---------------------------------------------------------------------------

try:
    from config_secrets import WIFI_PASSWORD, WIFI_SSID
except ImportError:
    WIFI_SSID = None
    WIFI_PASSWORD = None
