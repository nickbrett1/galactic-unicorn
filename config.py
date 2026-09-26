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

# Brightness ceiling. Full white at max draws just over 1 A, and COUNTDOWN
# ends with the whole panel lit green, so keep the ceiling sensible.
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
# Master audio switch: when False no synth channel is created at all, so
# nothing can play (the fanfare and the volume buttons both no-op). Audio is
# deliberately minimal when on: see lib/sound.py - the board is silent until
# a timer expires.
AUDIO_ENABLED = True

# ---------------------------------------------------------------------------
# Timings (milliseconds)
# ---------------------------------------------------------------------------

PROMPT_MS = 3000
HANDOFF_MS = 10000

# DEMO: compress every countdown to this many seconds, so the whole transition
# can be watched quickly. 0 = use each routine's real `minutes` (normal use).
# Was 10 for the 2026-09-19 first look; back to real time for the first
# release. Handy to set again when iterating on the countdown.
DEMO_SECONDS = 0

# D cancels instantly in every active state (decision 11). Set D_CANCEL_HOLD_MS
# above 0 to require a short hold instead - the recorded fallback if a toddler
# turns D into a toy. See the memo, section 4d button-behaviour box.
D_CANCEL_HOLD_MS = 0

# Parent's alternative escape hatch: hold SWITCH_SLEEP for this long.
CANCEL_HOLD_MS = 2000

# Hold a routine button during COUNTDOWN for this long -> +2 minutes.
EXTEND_HOLD_MS = 1000
EXTEND_MINUTES = 2

# ---------------------------------------------------------------------------
# Ambient clock (best-effort NTP; the countdown never touches the network)
# ---------------------------------------------------------------------------

# WiFi exists in phase 1 ONLY so the ambient clock can reach NTP. The countdown
# is locally timed and never touches the network. With no credentials present
# (the usual case) sync_ntp() skips immediately, so leaving this True is safe:
# it costs nothing until config_secrets.py exists.
WIFI_ENABLED = True
NTP_HOST = "pool.ntp.org"

# The first NTP query after association often fails (cold DNS/route, and
# ntptime's own timeout is 1 s), so retry a few times before giving up.
NTP_ATTEMPTS = 3
NTP_RETRY_MS = 1000

# Offset applied to UTC for the clock display. There is no timezone database on
# the board, so this is a fixed offset and must be changed by hand at the DST
# switch: Eastern is UTC-4 (EDT, summer) / UTC-5 (EST, winter).
UTC_OFFSET_S = -4 * 3600  # EDT; use -5 * 3600 after the DST switch

# ---------------------------------------------------------------------------
# Remote update (design memo: memos/CvaQ2nMNqaTvQbgYc8HJqW)
# ---------------------------------------------------------------------------

# Check the latest GitHub Release on every boot. Steady state is one small
# HTTPS request; only a version change downloads anything. The update runs from
# boot.py, BEFORE main.py, so a release that breaks main.py is repaired on the
# next boot rather than leaving the board dead - which is also why boot.py and
# lib/updater.py are excluded from the update itself.
UPDATE_ENABLED = True

# releases/latest/download/manifest.json is the one URL a device can fetch
# without knowing the version. The pack is fetched from the same directory.
UPDATE_MANIFEST_URL = (
    "https://github.com/nickbrett1/galactic-unicorn/releases/latest/download/manifest.json"
)

# boot.py gets ONE look at the network per boot, and the network here fails in
# windows of minutes rather than failing outright: measured on this board, six
# joins got an IP in ~3 s and six more, minutes later, got none at all. No
# boot-time budget can outlast that, so the loop retries on a slow timer and a
# window that opens an hour later is still caught. The cost is that an attempt
# blocks the display while it waits on the network (up to ~30 s in a dead
# window), which is why this is minutes and not seconds.
UPDATE_RETRY_MS = 15 * 60 * 1000

# ---------------------------------------------------------------------------
# Remote triggering (phase 2)
# ---------------------------------------------------------------------------

# Poll the LAN service for desired state. The poll is plain HTTP on the LAN
# (the service terminates no TLS on 3009), so it costs a query-param GET and a
# few hundred bytes back. Turning this off leaves the panel exactly as it was:
# buttons only, no network in the loop.
REMOTE_ENABLED = True

# The service's LAN address and port. A literal IP, so the poll does no DNS -
# the board is already on the same subnet, and a name would only add a lookup
# that can fail. Firewalled to the LAN; it is never reachable from the internet.
REMOTE_SERVICE_URL = "http://192.168.1.2:3009"

# The board-side clamp on the server's `next_poll_ms` (device-protocols.md
# section 6). The server sets the cadence; these are the floors and ceilings
# it is clamped to on THIS side (memo sections 6.3.5, 11.10). The server's own
# clamp is NEXT_POLL_MIN_MS/MAX_MS = 1000/10000 (O5), so these match it.
REMOTE_POLL_MIN_MS = 1000
REMOTE_POLL_MAX_MS = 10000

# The whole poll gets a tight budget (device-protocols.md section 8.1): the
# socket timeout bounds DNS+connect+read, so a dead service cannot stall a
# frame of the countdown by more than this.
REMOTE_TIMEOUT_S = 1.5

# Hard byte cap on the response body (device-protocols.md sections 1, 8.4). The
# body is a few hundred bytes; the cap is what stops a misbehaving or wrong
# server from handing the board a body it cannot hold. 1 KB is comfortably
# above the contract's "a few hundred bytes".
REMOTE_READ_CAP = 1024

# One join ATTEMPT for the remote path, with the updater's single-attempt
# budget. A join only ever happens while the panel is IDLE (never in COUNTDOWN
# or HANDOFF - see lib/remote.py), so the worst case is a bounded stall on an
# idle screen, not on a running timer.
REMOTE_WIFI_ATTEMPTS = 1
REMOTE_WIFI_ATTEMPT_MS = 10000

# The board's applied_gen high-water mark, persisted to flash (a few writes a
# day - wear is a non-issue; device-protocols.md section 3). It must survive a
# reboot in both directions so neither side can wedge the other (memo section
# 11.6). Board-local: not in the update pack.
REMOTE_APPLIED_GEN_FILE = "applied_gen.txt"

# Where a poll failure is written down. The transient REPL line is not enough:
# the failure mode this exists for - a heap failure that looks like a link
# failure - has to be readable after the session (memo section 11.15).
REMOTE_LOG_FILE = "remote.log"

# ---------------------------------------------------------------------------
# Secrets (gitignored; absent by default)
# ---------------------------------------------------------------------------

try:
    from config_secrets import WIFI_PASSWORD, WIFI_SSID
except ImportError:
    WIFI_SSID = None
    WIFI_PASSWORD = None

# Static addressing, when the network has reserved one for this board.
#
# A DHCP reservation is an instruction to the ROUTER ("give this MAC .63"), not
# to the board: the client still runs a fresh DHCP exchange on every boot,
# because MicroPython keeps no lease across a reset. And the exchange is exactly
# what hangs here - every log we have reads status=2 (associated, no IP), i.e.
# association succeeds in a second or two and DHCP never answers. Setting the
# address up front takes DHCP out of the boot path entirely: the board is online
# the moment it associates.
#
# Safe ONLY because of that same reservation - the router will not hand this
# address to anything else. All None (the default) means "use DHCP as before".
try:
    from config_secrets import STATIC_DNS, STATIC_GATEWAY, STATIC_IP, STATIC_MASK
except ImportError:
    STATIC_IP = None
    STATIC_MASK = None
    STATIC_GATEWAY = None
    STATIC_DNS = None

# The device token the service expects on every poll (device-protocols.md
# section 1). It is a shared LAN-only secret, defence in depth only (memo
# section 10) - never a real boundary, since anyone on the LAN can press the
# physical button - but it still must never be in the committed tree or in the
# update pack. Absent -> the poll is disabled rather than sent unauthenticated.
try:
    from config_secrets import REMOTE_DEVICE_TOKEN
except ImportError:
    REMOTE_DEVICE_TOKEN = None
