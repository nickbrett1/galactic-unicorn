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

# The weather readout gets its own, brighter level. AMBIENT (0.10) is fine for
# the breathing status pixel and the clock, which are meant to disappear into
# the furniture, but at 0.10 the weather is "way too dim" to read across a room
# (the report that produced this) - and the phototransistor sends the board down
# the dark-room path against a steady light()=17 anyway, where AMBIENT was the
# only level it ever drew at. 0.40 was a first notch; 0.55 is the "a little dim
# perhaps, notch it up" setting, and still under BRIGHTNESS_MAX (0.65) with a
# margin for the LEDs.
BRIGHTNESS_WEATHER = 0.55

BRIGHTNESS_PROMPT = 0.45
BRIGHTNESS_COUNTDOWN = 0.45
BRIGHTNESS_HANDOFF = 0.60
BRIGHTNESS_MAX = 0.65

# Auto-dim by the onboard phototransistor. `light()` returns 0-4095.
# Below LIGHT_DARK the room is dark enough that the display should go OFF
# (this is exactly when bathtime happens - see section 4d rule 4).
LIGHT_DARK = 40
LIGHT_DIM = 400

# The weather reading is the one thing exempt from the dark-room blanking.
# The rule above exists so the panel reads as furniture and never lights a
# bedroom, and the clock and the breathing pixel still obey it. But the
# phototransistor does not know where the board actually sits: a shaded desk or
# a hand over the sensor reads as "dark" while the room is plainly lit (this
# board measures a steady light()=17), and the first thing that produced was "I
# don't see any weather on the board". So the weather stays visible in the dark
# - at the same AMBIENT brightness the lit-room path already uses, so it is a
# dim, unlit-in-a-dark-room readout rather than a new light source. Set this
# False to restore the old "dark room = lamp only" behaviour.
AMBIENT_WEATHER_IN_DARK = True

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

# The NTP server, as a LITERAL IP on purpose. `getaddrinfo` is the one call in
# the NTP path that cannot be bounded by a socket timeout, and with
# `pool.ntp.org` here a dead network made every boot's retry overshoot the
# watchdog fuse (measured 2026-09-27: reset ~1 s after the third failure,
# reset_cause=3, on every boot). A literal IP takes DNS out of the path
# entirely, exactly as config.UPDATE_MANIFEST_URL does with the LAN service.
#
# 162.159.200.1 is Cloudflare's anycast time service (time.cloudflare.com), and
# 216.239.35.0 is Google's (time.google.com). Both are anycast, so no single
# datacentre is a dependency. Tried in order, one attempt each.
NTP_HOSTS = ("162.159.200.1", "216.239.35.0")
NTP_HOST = NTP_HOSTS[0]  # kept: anything reading a single host still works

# The bound on ONE NTP exchange - the send and the read alike
# (lib/net.py:ntp_time sets it on the socket). NTP retries from main.py feed
# the watchdog and then call out, so this is the longest stretch that runs
# unfed; it MUST stay comfortably under lib/watchdog.py's TIMEOUT_MS, which is
# what tests/test_net_ntp.py asserts. 2 s against an 8 s fuse leaves room for
# a slow radio without ever reaching it.
NTP_TIMEOUT_S = 2

# A failing NTP exchange is now bounded and cheap, so a few retries cost
# seconds rather than a reboot; the first query after association can still
# fail while the route is cold.
NTP_ATTEMPTS = 3
NTP_RETRY_MS = 1000

# How many polls in a row may fail while the radio still claims to be UP before
# the remote poller cycles the interface itself (lib/remote.py:_note_failure,
# lib/net.py:radio_reset).
#
# The state this exists for is the CYW43 going deaf: isconnected() True,
# status 3, a valid lease, a healthy rssi - and every socket call dead with
# OSError(110), the board not even answering ICMP (measured 2026-09-27; see
# net.radio_reset for what clears it and why neither kind of reset does).
#
# 3 at the 1 s poll floor is a cycle a few seconds into a wedge, which is early
# enough that a remote Cancel still lands during a countdown. 0 disables it.
RADIO_RESET_AFTER = 3

# How hard the cycle may try before the board gives up and just keeps polling.
#
# A cycle that fails is not a cycle that nearly worked. Measured 2026-09-28,
# board fw=0.1.39: inside one wedge, 22 of 23 cycles failed and the poller
# never once recorded a recovery - and because each cycle TEARS THE INTERFACE
# DOWN, running one every ~8 s is a radio that is never left quiet long enough
# to climb out on its own. The recovery becomes the thing preventing recovery.
#
# RADIO_RESET_MAX bounds how many consecutive cycles may FAIL before the board
# stops cycling and just keeps polling on the ordinary cadence; any poll that
# succeeds clears the run, so the next wedge starts with a full allowance.
# RADIO_RESET_COOLDOWN_MS bounds the rate even while they are still failing.
# Both 0 restores the old unbounded behaviour; 0 is how the rest of config
# spells "off", and it must stay expressible.
RADIO_RESET_COOLDOWN_MS = 120000
RADIO_RESET_MAX = 3

# The cycle's own budget, and its proof. The interface is given RADIO_DOWN_MS
# to actually report itself down before the cycle is called a no-op, and the
# confirming TCP round trip - the only evidence the wedge cannot fake - is
# given RADIO_PROBE_MS. Both stay well inside the 8 s watchdog fuse.
RADIO_SETTLE_MS = 1000
RADIO_DOWN_MS = 3000
RADIO_PROBE_MS = 2000

# Offset applied to UTC for the clock display. There is no timezone database on
# the board, so this is a fixed offset and must be changed by hand at the DST
# switch: Eastern is UTC-4 (EDT, summer) / UTC-5 (EST, winter).
UTC_OFFSET_S = -4 * 3600  # EDT; use -5 * 3600 after the DST switch

# ---------------------------------------------------------------------------
# Weather on the idle screen (ambient)
# ---------------------------------------------------------------------------

# Show the current NYC weather on the idle screen. This is what idle MEANS now:
# the weather takes the whole screen permanently, and the clock is only the
# fallback for the moment before the first reading lands (see lib/ambient.py).
WEATHER_ENABLED = True

# WHERE THE READING COMES FROM: Open-Meteo, over PLAIN HTTP.
#
# The board cannot complete a TLS handshake (see UPDATE_MANIFEST_URL), so the
# source must speak plain HTTP. Open-Meteo does, needs no API key, and answers
# this request with a ~200-byte JSON body:
#
#   {"current":{"temperature_2m":14.3,"weather_code":3}}
#
# The location is fixed by lat/lon (Manhattan). Change the pair to move it; the
# URL is built from them. `temperature_unit=celsius` is explicit because the
# panel shows Celsius.
WEATHER_LATITUDE = 40.7128
WEATHER_LONGITUDE = -74.0060
WEATHER_URL = (
    "http://api.open-meteo.com/v1/forecast?latitude="
    + str(WEATHER_LATITUDE)
    + "&longitude="
    + str(WEATHER_LONGITUDE)
    + "&current=temperature_2m,weather_code&temperature_unit=celsius"
)

# How often the reading is refreshed, and how soon after a failure to try
# again. Weather changes slowly, and every fetch is a bounded wifi join + GET
# on an otherwise idle screen, so the refresh is minutes apart. A failure is
# retried sooner, but not in a tight loop.
WEATHER_POLL_MS = 15 * 60 * 1000
WEATHER_RETRY_MS = 60 * 1000

# The bound on EVERY socket operation of the fetch - connect and each read
# alike (lib/net.py:http_get sets it on the socket), so a dead host cannot
# stall a frame by more than this. The body is small, so 3 s is generous.
WEATHER_TIMEOUT_S = 3

# Hard cap on the response body (the real body is ~200 bytes), so a wrong or
# hostile host cannot hand the board a document it cannot hold.
WEATHER_READ_CAP = 1024

# One wifi join ATTEMPT for the weather path, with the remote poller's single
# attempt: a join only ever happens while the panel is IDLE (never in
# COUNTDOWN or HANDOFF - see main.py's gate), so the worst case is a bounded
# stall on an idle screen.
WEATHER_WIFI_ATTEMPTS = 1
WEATHER_WIFI_ATTEMPT_MS = 10000

# Where a fetch failure is written down (bounded, like remote.log). The
# transient REPL line is not enough: a failure has to be readable after the
# session.
WEATHER_LOG_FILE = "weather.log"

# ---------------------------------------------------------------------------
# Remote update (design memo: memos/CvaQ2nMNqaTvQbgYc8HJqW)
# ---------------------------------------------------------------------------

# Check the latest GitHub Release on every boot. Steady state is one small
# HTTPS request; only a version change downloads anything. The update runs from
# boot.py, BEFORE main.py, so a release that breaks main.py is repaired on the
# next boot rather than leaving the board dead - which is also why boot.py and
# lib/updater.py are excluded from the update itself.
UPDATE_ENABLED = True

# The manifest and the pack come from the LAN service, over PLAIN HTTP.
#
# They used to come from `releases/latest/download/manifest.json` on GitHub -
# i.e. over HTTPS - and this board cannot complete a TLS handshake. Measured on
# the board 2026-09-26: DNS github.com (17 ms), TCP :443 (16 ms), LAN plain HTTP
# and even *internet* plain HTTP (example.com, 95 ms) all work, while EVERY
# HTTPS attempt failed - instantly as OSError(12,), or by blocking long enough
# to trip the hardware watchdog and hard-reset the board. The code's own
# classify_failure calls OSError(12) "heap", and that label is wrong here: the
# TLS buffers allocate fine (~25 KB) and a 64 KB contiguous block allocates, so
# it is the handshake itself, deeper in mbedTLS/lwIP.
#
# So the check could never read a manifest at all: 0 occurrences of the then-
# latest version in 89 KB of update.log, while "no update: <old> is already
# running" appeared 103 times. The board had not once seen a manifest in its
# life.
#
# The fix is to stop making the one device that cannot do TLS the device that
# must. The service on the LAN (galactic-unicorn-remote) fetches the release
# over HTTPS on the board's behalf, verifies it, and serves the manifest and the
# pack over plain HTTP under /firmware/*. That host is the one the board already
# talks to every second for the remote poll, so nothing new is exposed and no
# TLS stack is needed on the board. Integrity is unchanged: the manifest still
# carries a sha256 for the pack and a sha256 per file, and the updater checks
# them exactly as before (lib/updater.py), so a corrupt or truncated transfer
# still cannot reach the live tree.
#
# The pack is fetched from the same directory as the manifest (updater._update
# derives the base by dropping the last path segment), so this is the only
# string that has to change to move the source again.
UPDATE_MANIFEST_URL = "http://192.168.1.2:3009/firmware/manifest.json"

# The bound on EVERY socket operation of an update fetch - connect and each
# individual read alike (lib/net.py:http_get sets it on the socket).
#
# This is what makes the check fit under the watchdog instead of needing a
# longer fuse. The fuse CANNOT be lengthened on this board (measured:
# `machine.WDT(timeout=30000)` fires in under 11 s, not at 30 s), so
# lib/watchdog.py no longer offers a longer one - and the network phase has to
# be short rather than "protected". With this timeout the longest stretch that
# cannot feed the fuse is one 3 s read, comfortably inside the ~8 s app fuse.
UPDATE_TIMEOUT_S = 3

# boot.py gets ONE look at the network per boot, and the network here fails in
# windows of minutes rather than failing outright: measured on this board, six
# joins got an IP in ~3 s and six more, minutes later, got none at all. No
# boot-time budget can outlast that, so the loop retries on a slow timer and a
# window that opens an hour later is still caught. The cost is that an attempt
# blocks the display while it waits on the network - the join is fed to the
# watchdog while it waits (lib/net.py:wait_for_ip), and the fetch itself is
# bounded by UPDATE_TIMEOUT_S - which is why this is minutes and not seconds.
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
