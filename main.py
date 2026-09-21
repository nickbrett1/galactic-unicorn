"""Firmware entry point.

MicroPython runs this file on boot. Imports resolve from the filesystem root
and from lib/, so reusable modules live in lib/ and are imported by their
module name.

Boot order: BOOT self-test (a colourful HELLO banner; the applied release is
logged and written to the wifihealth header) -> best-effort NTP -> the routine
loop. Nothing after the self-test
blocks on the network: the countdown is locally timed and runs with WiFi
switched off.
"""

import gc
import json
import time

import bigfont
import config
import updater
import watchdog
from ambient import Ambient
from buttons import Buttons
from display import (
    SWITCH_BRIGHTNESS_DOWN,
    SWITCH_BRIGHTNESS_UP,
    SWITCH_SLEEP,
    SWITCH_VOLUME_DOWN,
    SWITCH_VOLUME_UP,
    SWITCHES,
    Display,
)
from routine import Engine
from sound import Audio

# Instrumentation, not machinery: main.py can be hand-deployed over USB without
# it (lib/updater.py is deployed that way), so a missing module must degrade to
# "no trace" rather than break the boot.
try:
    import wifihealth
except ImportError:  # pragma: no cover - only on a hand-deployed tree
    wifihealth = None

ROUTINES_PATH = "routines.json"
LOOP_MS = 20

# How long the render loop must keep feeding the fuse before the release is
# allowed to call itself proven (see mark_boot_ok). Long enough that a release
# which wedges in the loop never reaches it, short enough that a power blip in
# the window is unlikely - and the window only exists on the first boot after
# an update is applied, because after that boot-ok.txt already matches.
BOOT_OK_SOAK_MS = 10000

# Every switch we care about, by the name the engine uses.
ALL_SWITCHES = {
    "A": SWITCHES["A"],
    "B": SWITCHES["B"],
    "C": SWITCHES["C"],
    "D": SWITCHES["D"],
    "SLEEP": SWITCH_SLEEP,
    "VOL_UP": SWITCH_VOLUME_UP,
    "VOL_DOWN": SWITCH_VOLUME_DOWN,
    "BRIGHT_UP": SWITCH_BRIGHTNESS_UP,
    "BRIGHT_DOWN": SWITCH_BRIGHTNESS_DOWN,
}


def load_routines(path=ROUTINES_PATH):
    """The routine table is config, not logic (memo section 5)."""
    with open(path) as f:
        data = json.load(f)
    return data.get("routines", []), data.get("buttons", {})


def sync_ntp(log):
    """Best-effort NTP, for the ambient clock only. Never raises."""
    if not config.WIFI_ENABLED or not config.WIFI_SSID:
        log(f"ntp: skipped (WIFI_ENABLED={config.WIFI_ENABLED}, ssid={config.WIFI_SSID})")
        return False
    try:
        import network

        wlan = network.WLAN(network.STA_IF)
        wlan.active(True)
        if not wlan.isconnected():
            log("ntp: joining wifi")
            wlan.connect(config.WIFI_SSID, config.WIFI_PASSWORD)
            started = time.ticks_ms()
            while not wlan.isconnected():
                if time.ticks_diff(time.ticks_ms(), started) > 15000:
                    log("ntp: wifi join timed out")
                    return False
                watchdog.feed()
                time.sleep_ms(200)
        log("ntp: wifi up, syncing")
        import ntptime

        ntptime.host = config.NTP_HOST
        # Retry: the FIRST query after association can time out - DNS/route are
        # not warm yet, and ntptime's built-in timeout is only 1 s. Without this
        # the clock silently degrades to the status pixel on a cold boot.
        for attempt in range(1, config.NTP_ATTEMPTS + 1):
            watchdog.feed()
            try:
                ntptime.settime()
                log(f"ntp: synced (attempt {attempt}) -> {time.localtime()}")
                return True
            except Exception as exc:  # noqa: BLE001 - retried below
                log(f"ntp: attempt {attempt}/{config.NTP_ATTEMPTS} failed ({exc})")
                time.sleep_ms(config.NTP_RETRY_MS)
        log("ntp: gave up - ambient degrades to the status pixel")
        return False
    # Blind except is deliberate: NTP is best-effort, and *any* failure must
    # degrade to the status pixel rather than take the display down.
    except Exception as exc:  # noqa: BLE001
        log(f"ntp: failed ({exc}) - ambient degrades to the status pixel")
        return False


def firmware_version():
    """The release the updater applied, read from version.txt at every boot.

    The boot log line and wifihealth's per-boot header both carry this, so the
    release the board is running is still self-evidencing - it just is not on
    the panel any more (see boot_banner). A USB deploy has no version.txt (only
    the updater writes it, last, on success), which reads "dev" - correctly,
    because nothing has been released onto it.
    """
    try:
        with open("version.txt") as fh:
            return fh.read().strip() or "dev"
    except OSError:
        return "dev"


def mark_boot_ok(log):
    """Record that this release really did come up.

    boot.py's updater compares this against version.txt on the next boot: a
    release that has had its chance and never wrote it gets the previous tree
    put back. So it is written HERE and only here.

    WHEN it is written is the whole point, and it is NOT the banner. The banner
    proves the app started; it does not prove the app stays started, and the
    difference is a release that builds its display, draws a frame and then
    wedges in the render loop. Writing this at the banner retired the release's
    one chance before the loop had proved anything, so a release that wedged
    every boot was judged healthy on the next boot and reset forever. The
    watchdog alone cannot fix that: it turns "wedged forever" into "reset-loop
    forever", which is not recovery. So the marker is delayed until the loop
    has actually been feeding the fuse for BOOT_OK_SOAK_MS.

    Not written from a timer callback, and not from boot.py: main.py owns it,
    and the updater only ever reads it.

    Losing the write is not worth failing over: the updater's one-chance rule
    would roll a perfectly good release back on the next boot, which is a much
    worse outcome than a stale marker, so a failure here is loud but survivable.
    """
    try:
        with open("boot-ok.txt", "w") as fh:
            fh.write(firmware_version() + "\n")
    except OSError as exc:  # never take the display down for this
        log(f"could not record boot-ok ({exc})")


# The banner word, one hue per letter. Bright and distinct - this is the
# friendliest thing the panel ever draws, and it is the first thing you see.
HELLO_WORD = "HELLO"
HELLO_COLORS = (
    (255, 32, 0),  # red
    (255, 140, 0),  # amber
    (255, 240, 0),  # yellow
    (0, 255, 48),  # green
    (0, 170, 255),  # blue
)
# Letters that have not lit yet: visible enough to read the word as arriving,
# dark enough that the lighting-up is worth watching.
HELLO_DIM = (20, 22, 30)
HELLO_STEP_MS = 110  # per-letter chase-in
HELLO_HOLD_MS = 1600  # the finished word, still and readable


def _draw_hello(display, x, lit):
    """Draw HELLO at (x, 0) with the first `lit` letters in colour.

    Drawn a glyph at a time (bigfont draws one colour per call), so each letter
    can carry its own hue. bigfont.draw_text returns the x past the glyph it
    drew, so the next letter starts GAP px further on.
    """
    display.clear()
    for i, ch in enumerate(HELLO_WORD):
        rgb = HELLO_COLORS[i % len(HELLO_COLORS)] if i < lit else HELLO_DIM
        x = bigfont.draw_text(display, x, 0, ch, rgb=rgb) + bigfont.GAP
    display.update()


def boot_banner(display, log):
    """Say hello: HELLO in big blocky colour, then hold it. Silent.

    Fills the panel height (bigfont's 11 px glyphs), so it reads from across
    the room. The letters arrive one at a time and then sit still - the earlier
    banner scrolled the firmware version past at 25 ms a frame, which was too
    fast to read and (at 8 px upscaled) too small to read anyway. The version
    still goes to the log, and wifihealth writes it into its per-boot header,
    so nothing is lost by keeping it off the panel.
    """
    version = firmware_version()
    log(f"BOOT {config.BOARD} / {config.CHIP} fw={version}")
    display.set_brightness(config.BRIGHTNESS_COUNTDOWN)
    x0 = max(0, (display.width - bigfont.text_width(HELLO_WORD)) // 2)
    for lit in range(1, len(HELLO_WORD) + 1):
        _draw_hello(display, x0, lit)
        time.sleep_ms(HELLO_STEP_MS)
    _draw_hello(display, x0, len(HELLO_WORD))
    time.sleep_ms(HELLO_HOLD_MS)
    display.clear()
    display.update()


def _checked_under_network_fuse(check, config):
    """Run the update check under a fuse sized for the network, not for the app.

    The render loop feeds an 8 s fuse every 20 ms, but this call leaves the
    loop: it spends its time inside `urequests.get` - DNS, TCP, the TLS
    handshake - where nothing can feed the fuse (updater._get says so itself).
    config.py documents the same attempt as blocking "up to ~30 s in a dead
    window", so on a stalled network the 8 s fuse does not make the check slow,
    it makes it a REBOOT: the board dies where it stands and comes back through
    boot.py.

    Measured on this board, 2026-09-21 - every reset in wifi.log is
    reset_cause=3 (WDT_RESET), so the board was never crashing, and the two
    unattended ones each landed about one UPDATE_RETRY_MS after a boot, exactly
    when this check runs:

        wifi: === boot fw=0.1.18 reset_cause=3 free=36416 ===     <- 15:07:43
        wifi: === boot fw=0.1.17 reset_cause=3 free=36480 ===     <- 13:14:24

    with update.log beside them showing the network stalling ("wifi attempt 1/3
    got no IP" x8, "update failed: OSError('http 504',)").

    So widen the fuse for the phase that cannot feed it, and put it back
    afterwards. The restore is in a `finally`: a check that raises must not
    leave the app running on the long fuse.
    """
    try:
        watchdog.arm(getattr(watchdog, "NETWORK_TIMEOUT_MS", watchdog.TIMEOUT_MS))
        check(config)
    finally:
        watchdog.arm(watchdog.TIMEOUT_MS)


def main():
    from sys import print_exception

    def log(message):
        print("unicorn:", message)

    # Only one thing survives a hard reset badly enough to be worth asking
    # about: the watchdog. A hard reset wipes the heap, the display and any
    # in-memory note of what happened, so this is the *only* evidence the
    # wedge protection ever fired - and without it a self-heal looks
    # indistinguishable from a power blip.
    #
    # reset_cause() is a LATCH, not a report on this boot: measured on the
    # board, CAUSE read 3 both before and after a soft reset. So this says the
    # watchdog has fired at some point since power-on, which is all it can
    # honestly claim - the old wording read as a statement about the boot just
    # finished, and that misled a diagnosis for a while.
    if watchdog.reset_was_watchdog():
        log("watchdog has fired since power-on (a latch, not a fact about this boot)")

    try:
        display = Display(config)
        routines, button_map = load_routines()
        log(f"loaded {len(routines)} routines, buttons={button_map}")
    # Config errors are worth crashing on: a display that boots to a blank
    # panel with no explanation is worse than one that fails loudly on USB.
    # (No BLE001 suppression needed - re-raising is not a blind catch.)
    except Exception as exc:
        print_exception(exc)
        raise

    boot_banner(display, log)

    audio = Audio(display, config)
    ambient = Ambient(display, config)
    ambient.ntp_ok = sync_ntp(log)

    # Started AFTER sync_ntp so the trace's first status line is the state the
    # app will actually run in, not the "idle" of a radio that has not been
    # brought up yet. Its per-boot header goes down either way - that line is
    # what makes a reset loop visible from the outside (see lib/wifihealth.py).
    health = wifihealth.start(config, log, firmware_version()) if wifihealth else None

    buttons = Buttons(
        display,
        ALL_SWITCHES,
        {
            "A": config.EXTEND_HOLD_MS,
            "B": config.EXTEND_HOLD_MS,
            "C": config.EXTEND_HOLD_MS,
            "D": config.D_CANCEL_HOLD_MS,
            "SLEEP": config.CANCEL_HOLD_MS,
            "VOL_DOWN": config.CANCEL_HOLD_MS,
        },
    )

    engine = Engine(display, buttons, audio, ambient, config, routines, button_map)
    log(f"entering loop (state={engine.state_name()})")

    # Collect proactively rather than only when an allocation fails: the
    # default (-1) lets the heap run to the wire, and an allocation failure at
    # the wrong moment takes the whole display down (it did - see ambient.py).
    gc.threshold(8192)

    # The fuse starts here for the app's sake, but be clear that it does not
    # END here: an armed WDT survives a soft reset, so the next boot's
    # updater runs under it too and has to feed it (lib/watchdog.py, and the
    # feed calls in updater._join_wifi / updater._download).
    if watchdog.arm():
        log(
            f"wedge protection: watchdog armed at {watchdog.TIMEOUT_MS} ms"
            f" ({getattr(watchdog, 'NETWORK_TIMEOUT_MS', watchdog.TIMEOUT_MS)} ms"
            " during update checks, which cannot feed it)"
        )
    else:
        log("wedge protection: no watchdog on this build (degraded, not broken)")

    boot_ok_written = False
    loop_started = time.ticks_ms()
    # boot.py already had its one look at the network this boot, so the first
    # in-loop attempt waits a full interval rather than doubling up on it.
    update_checked_at = loop_started

    # The loop must not die: the panel stopped responding once before, and
    # Ctrl-C showed the loop had exited silently. So a bad frame is reported
    # and swallowed, and the report itself is guarded so that reporting a
    # failure cannot kill the loop too. If this ever does exit, the
    # BaseException handler below records why to crash.log.
    while True:
        now = time.ticks_ms()
        # The fuse is fed here, every 20 ms, so a wedged engine.tick() - or a
        # wedged anything else on this thread - hard-resets the board into
        # boot.py's recovery instead of leaving a frozen panel on the wall.
        watchdog.feed()
        # One cheap look at the radio per second, written down where it survives
        # the session. This is the instrument for the one thing we still cannot
        # see from the outside: whether a DHCP failure leaves the board UP
        # without an IP, or RESETTING (see lib/wifihealth.py).
        if health is not None:
            health.sample()
        # Retire the release's one chance only after the loop has demonstrably
        # kept feeding the fuse. A release that wedges here never gets this far,
        # so boot-ok.txt stays behind and boot.py rolls it back on the next
        # boot. Done once, from the normal path, so it is a fact about the loop
        # rather than a timer that fired hopefully.
        if not boot_ok_written and time.ticks_diff(now, loop_started) >= BOOT_OK_SOAK_MS:
            boot_ok_written = True
            mark_boot_ok(log)
        # Retry the update from here, not just from boot.py: the network fails
        # in windows of minutes, so an update that missed its chance at boot
        # should still land when the network clears. Placed AFTER mark_boot_ok
        # on purpose - this can block the display for ~30 s in a dead window,
        # and a release must not have its soak interrupted by its own update
        # check. check_for_update resets the board if it applies anything.
        if (
            time.ticks_diff(now, update_checked_at)
            >= getattr(config, "UPDATE_RETRY_MS", 15 * 60 * 1000)
            # Never while a routine is live. check_for_update can block for
            # ~30 s, and with an 8 s fuse a stalled check is a reboot (see
            # _checked_under_network_fuse) - and a reboot unwinds the countdown,
            # so checking at the wrong moment costs a child their timer. This is
            # a deferral, not a skip: update_checked_at is left alone, so the
            # check runs on the first idle frame instead.
            and engine.routine is None
        ):
            update_checked_at = time.ticks_ms()
            # lib/updater.py is EXCLUDED from the pack, so a release can reach
            # the board before the updater that backs it - this call has to
            # tolerate an older updater rather than raise inside the loop. The
            # feature is optional; the display is not.
            check = getattr(updater, "check_for_update", None)
            if check is not None:
                _checked_under_network_fuse(check, config)
        try:
            engine.tick(now)
        # One bad frame must not kill a display someone is relying on.
        except Exception as exc:  # noqa: BLE001
            try:
                print_exception(exc)
            except Exception:  # noqa: BLE001, S110 - report must not kill us too
                pass
            time.sleep_ms(200)
        time.sleep_ms(LOOP_MS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        # Ctrl-C: a human at the REPL, or mpremote attaching over USB. This is
        # NOT a crash, and it must not be recorded as one - crash.log is the
        # only surviving record of why the loop actually died, and every
        # debugging session was overwriting it with an interrupted traceback.
        print("unicorn: interrupted (Ctrl-C) - not a crash, crash.log left alone")
    # We must know *why* the loop died, and the serial buffer is gone by the
    # time anyone looks - so record it to the filesystem where it survives.
    except BaseException as exc:
        from sys import print_exception

        print("unicorn: MAIN DIED:", repr(exc))
        try:
            with open("crash.log", "w") as f:
                f.write(f"mem_free={gc.mem_free()}\n")
                print_exception(exc, f)
        except Exception:  # noqa: BLE001, S110 - must not hide the original
            pass
        raise
