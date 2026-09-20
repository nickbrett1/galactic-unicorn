"""Firmware entry point.

MicroPython runs this file on boot. Imports resolve from the filesystem root
and from lib/, so reusable modules live in lib/ and are imported by their
module name.

Boot order: BOOT self-test (banner "v<version>" - the release applied by the
updater) -> best-effort NTP -> the routine loop. Nothing after the self-test
blocks on the network: the countdown is locally timed and runs with WiFi
switched off.
"""

import gc
import json
import time

import config
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

ROUTINES_PATH = "routines.json"
LOOP_MS = 20

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

    The boot banner shows this, which makes the banner self-evidencing: the
    number on the panel is the release boot.py just pulled over the air. A USB
    deploy has no version.txt (only the updater writes it, last, on success),
    which reads "dev" - correctly, because nothing has been released onto it.
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
    put back. So it is written HERE and only here - immediately after the
    banner, the first moment the app has demonstrably started (display built,
    routines parsed, a frame drawn) - and never earlier, because it means
    "this came up" and nothing else.

    Losing the write is not worth failing over: the updater's one-chance rule
    would roll a perfectly good release back on the next boot, which is a much
    worse outcome than a stale marker, so a failure here is loud but survivable.
    """
    try:
        with open("boot-ok.txt", "w") as fh:
            fh.write(firmware_version() + "\n")
    except OSError as exc:  # never take the display down for this
        log(f"could not record boot-ok ({exc})")


def boot_banner(display, log):
    """BOOT / self-test. Also the phase-0 'hello' target."""
    version = firmware_version()
    log(f"BOOT {config.BOARD} / {config.CHIP} fw={version}")
    display.set_brightness(config.BRIGHTNESS_COUNTDOWN)
    msg = "v" + version
    width = display.text_width(msg, scale=1)
    for x in range(display.width, -width, -1):
        display.clear()
        display.text(msg, x, (display.height - 8) // 2, rgb=(0, 150, 200), scale=1)
        # A payload bar under it proves the whole width renders.
        display.rect(0, display.height - 2, display.width, 1, (0, 60, 90))
        display.update()
        time.sleep_ms(25)
    display.clear()
    display.update()


def main():
    from sys import print_exception

    def log(message):
        print("unicorn:", message)

    # Only one thing survives a hard reset badly enough to be worth asking
    # about: the watchdog. A hard reset wipes the heap, the display and any
    # in-memory note of what happened, so this is the *only* evidence the
    # wedge protection ever fired - and without it a self-heal looks
    # indistinguishable from a power blip.
    if watchdog.reset_was_watchdog():
        log("last restart: WATCHDOG FIRED - the app stopped feeding it")

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
    mark_boot_ok(log)

    audio = Audio(display, config)
    ambient = Ambient(display, config)
    ambient.ntp_ok = sync_ntp(log)

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
        log(f"wedge protection: watchdog armed at {watchdog.TIMEOUT_MS} ms")
    else:
        log("wedge protection: no watchdog on this build (degraded, not broken)")

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
