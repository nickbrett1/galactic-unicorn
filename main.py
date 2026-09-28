"""Firmware entry point.

MicroPython runs this file on boot. Imports resolve from the filesystem root
and from lib/, so reusable modules live in lib/ and are imported by their
module name.

Boot order: a white power pixel the instant the panel exists (boot.py lights it
before its own look at the network, in case that takes twenty seconds) -> a
loading frame, the unlit HELLO, while the modules import behind it -> BOOT
self-test (the same word coloured in letter by letter; the applied release is
logged and written to the wifihealth header) -> best-effort NTP -> the routine
loop. Nothing after the self-test blocks on the network: the countdown is
locally timed and runs with WiFi switched off.
"""

import gc
import json
import time

import bigfont
import config
import updater
import watchdog
from display import (
    SWITCH_BRIGHTNESS_DOWN,
    SWITCH_BRIGHTNESS_UP,
    SWITCH_SLEEP,
    SWITCH_VOLUME_DOWN,
    SWITCH_VOLUME_UP,
    SWITCHES,
    Display,
)

# Everything else main.py needs is imported INSIDE main(), behind the loading
# frame - see the note there for what that is worth, and the measurement behind
# it. `display` cannot move with them: ALL_SWITCHES is built here, from the
# switch constants, and scripts/showcase.py imports those names from main.

ROUTINES_PATH = "routines.json"
LOOP_MS = 20

# How long the render loop must keep feeding the fuse before the release is
# allowed to call itself proven (see mark_boot_ok). Long enough that a release
# which wedges in the loop never reaches it, short enough that a power blip in
# the window is unlikely - and the window only exists on the first boot after
# an update is applied, because after that boot-ok.txt already matches.
BOOT_OK_SOAK_MS = 10000

# Building the remote can fail transiently right after boot: it is constructed
# while the boot-time allocations are still settling, and it HAS raised
# MemoryError there ("allocating 640 bytes" at /lib/remote.py line 50,
# 2026-09-27). Giving up permanently silently disabled the remote for the rest
# of the session, so the attach is retried a few times from the loop with a
# gc.collect() first - the boot garbage is what the allocation was competing
# with. Bounded, and it says when it stops trying.
REMOTE_ATTACH_RETRIES = 3
REMOTE_ATTACH_RETRY_MS = 10000


def _attach_remote(engine, config, log, fw):
    """Build the remote. Returns (remote_or_None, retryable). Never raises.

    `retryable` is False when retrying cannot possibly help - no lib/remote.py
    on this build, or a remote that built fine - so the loop stops asking.
    """
    try:
        from remote import Remote

        remote = Remote(engine, config, log, fw=fw)
        log("remote: " + remote.describe())
        return remote, False
    except ImportError:
        log("remote: no remote module on this build")
        return None, False
    except Exception as exc:  # noqa: BLE001 - the display must survive a bad remote
        from sys import print_exception

        print_exception(exc)
        return None, True

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


def _set_clock(epoch_s):
    """Set the RP2040's RTC from Unix seconds (UTC).

    The last thing `ntptime.settime()` did, kept faithfully so nothing
    downstream changes: the RTC holds UTC and the display applies
    config.UTC_OFFSET_S itself (lib/ambient.py). The weekday is 1..7 with
    Monday as 1, which is why it is `tm[6] + 1` -- time.localtime's weekday is
    0..6 with Monday as 0.
    """
    import machine

    tm = time.localtime(epoch_s)
    machine.RTC().datetime((tm[0], tm[1], tm[2], tm[6] + 1, tm[3], tm[4], tm[5], 0))


def sync_ntp(log):
    """Best-effort NTP, for the ambient clock only. Never raises."""
    if not config.WIFI_ENABLED or not config.WIFI_SSID:
        log(f"ntp: skipped (WIFI_ENABLED={config.WIFI_ENABLED}, ssid={config.WIFI_SSID})")
        return False
    try:
        import network

        wlan = network.WLAN(network.STA_IF)
        wlan.active(True)
        # Same reserved address as the updater uses, so NTP does not pay for a
        # DHCP exchange either. Applied whether or not the radio says it is
        # already connected: after a soft reset it says it is, while the
        # resolver is empty, and skipping it here is what made NTP fail every
        # attempt with (-2). Imported lazily: this module is optional.
        try:
            import updater

            updater.apply_static_ip(wlan, config)
        except Exception:  # noqa: BLE001, S110 - best effort, DHCP still works
            pass
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
        # NOT `ntptime.settime()`. That builds its own socket, picks its own
        # timeout, and resolves a hostname -- and getaddrinfo has no timeout at
        # all, so a dead NTP server left this loop's blocking call longer than
        # the fuse that had just been fed for it (measured 2026-09-27: a hard
        # reset about a second after the third failure, every boot,
        # reset_cause=3). lib/net.py:ntp_time does the same query with an
        # explicit socket timeout, so the FAILING path is bounded too -- which
        # is the whole point, because the failing path is the one that has to
        # degrade to the status pixel instead of rebooting the board.
        import net

        hosts = getattr(config, "NTP_HOSTS", None) or (config.NTP_HOST,)
        timeout_s = getattr(config, "NTP_TIMEOUT_S", 2)
        for attempt in range(1, config.NTP_ATTEMPTS + 1):
            host = hosts[(attempt - 1) % len(hosts)]
            watchdog.feed()
            try:
                _set_clock(net.ntp_time(host, timeout_s, feed=watchdog.feed))
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
    chance before the loop had proved anything, so a release that wedged
    every boot was judged healthy on the next boot and reset forever. The
    watchdog alone cannot fix that: it turns "wedged forever" into "reset-loop
    forever", which is not recovery. So the marker is delayed until the loop
    has actually been feeding the fuse for BOOT_OK_SOAK_MS.

    Not written from a timer callback, and not from boot.py: main.py owns it,
    and the updater only ever reads it.

    Losing the write is not worth failing over here: each boot that ends
    without it spends one of the release's BOOT_FAILS_MAX chances, so a
    one-off failure is absorbed and the display is never taken down for a
    marker. Only a release that fails to write it on every boot up to the
    limit is rolled back, which is the right outcome anyway.
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
    display.power_pixel()  # the corner lamp survives the banner
    for i, ch in enumerate(HELLO_WORD):
        rgb = HELLO_COLORS[i % len(HELLO_COLORS)] if i < lit else HELLO_DIM
        x = bigfont.draw_text(display, x, 0, ch, rgb=rgb) + bigfont.GAP
    display.update()


def loading_frame(display):
    """Put the finished word on the panel, unlit, while the imports load.

    This is the frame that bridges boot.py's power pixel and the banner's
    chase-in, and it exists because of where those two sit in time. boot.py
    hands over a panel with one pixel lit while it takes its look at the
    network; the banner cannot start until everything below is imported, which
    is another ~700 ms of blocking work. Drawing the whole word here - dark,
    but readable - means the panel shows you what is coming instead of holding
    a single pixel for a beat longer than it has to, and it is exactly the
    frame the chase-in then colours, so the panel never goes back to black in
    between.

    Set at countdown brightness rather than ambient: this is the boot saying
    something, not furniture. Silent, and one blocking write of one frame.
    """
    display.set_brightness(config.BRIGHTNESS_COUNTDOWN)
    display.clear()
    display.power_pixel()
    x = max(0, (display.width - bigfont.text_width(HELLO_WORD)) // 2)
    for ch in HELLO_WORD:
        x = bigfont.draw_text(display, x, 0, ch, rgb=HELLO_DIM) + bigfont.GAP
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
    # Deliberately NOT cleared on the way out. The next thing that draws is the
    # ambient clock's first frame, and between here and there sit the audio and
    # ambient constructors and sync_ntp - which is a wifi join, up to ~15 s of
    # it. Clearing would hand the panel over to that wait in black, which is the
    # same "and then it's gone" the dot used to do. Finishing on the word and
    # letting the loop take it from underneath keeps the panel lit all the way
    # to the resting state.


def _run_update_check(check, config):
    """Run the update check. It fits under the app fuse now, so nothing is armed.

    This used to be `_checked_under_network_fuse`: it widened the watchdog for
    the network phase (`NETWORK_TIMEOUT_MS = 30000`), because the check left the
    render loop and spent its time inside a blocking `urequests.get` that cannot
    feed the fuse - so on a stalled network the 8 s fuse did not make the check
    slow, it made it a REBOOT. The reset log agreed: every reset was
    reset_cause=3 (WDT_RESET), and the unattended ones each landed one
    UPDATE_RETRY_MS after a boot, exactly when this check runs.

    Measured on this board, 2026-09-26: the widening does not work. An explicit
    `machine.WDT(timeout=30000)` fires in under 11 s, not at 30 s - so the check
    ran under a fuse the code only believed was long. Worse than useless: it hid
    the real budget.

    So the check is made to fit instead, and there is nothing to arm or restore:
    the manifest and the pack come over plain HTTP from the LAN service
    (config.UPDATE_MANIFEST_URL), a literal IP, and every socket operation has
    an explicit timeout (config.UPDATE_TIMEOUT_S) so the longest stretch that
    cannot feed the fuse is one 3 s read - comfortably inside the ~8 s fuse.
    The deferral gate that keeps the check out of a running countdown is
    unchanged, and it is still the thing that protects a child's timer.
    """
    # Collect first. Measured 2026-09-27: the IN-LOOP check failed with
    # MemoryError('memory allocation failed, allocating 3840 bytes') while
    # gc.mem_free() read 93488. The loop's heap is fragmented by the live
    # display, engine and remote, and the manifest fetch/parse wants one
    # contiguous block the free list cannot supply. The SAME check at boot
    # (boot.py, fresh heap) fetched the same 2.2 KB manifest without trouble,
    # and _attach_remote already collects for exactly this reason. One collect
    # before the fetch consolidates the free list while the loop is idle here.
    gc.collect()
    check(config)


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
        loading_frame(display)
        # The panel is lit the moment it exists, and everything expensive is
        # loaded BEHIND that light rather than in front of it. These are the
        # modules deferred out of the top of the file, measured cold on the
        # board with each module dropped from sys.modules first:
        #
        #     routine 322 ms   buttons 84 ms   ambient 80 ms
        #     sound    73 ms   wifihealth 135 ms
        #
        # ~700 ms of a boot that used to be dark, which is most of what "it
        # takes a second or two" actually was after boot.py's own look at the
        # network. A failure to import is still loud: it lands in the handler
        # below, which is the documented behaviour for a broken deploy.
        from ambient import Ambient
        from buttons import Buttons
        from routine import Engine
        from sound import Audio

        # Instrumentation, not machinery: main.py can be hand-deployed over USB
        # without it (lib/updater.py is deployed that way), so a missing module
        # must degrade to "no trace" rather than break the boot.
        try:
            import wifihealth
        except ImportError:  # pragma: no cover - only on a hand-deployed tree
            wifihealth = None

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
    # The idle screen's weather. Best-effort and OPTIONAL, exactly like the
    # remote: absent, disabled or failing, the ambient clock is unchanged. It
    # is constructed even when disabled so the log always says which way it is.
    try:
        from weather import Weather

        weather = Weather(config, log)
        log("weather: " + weather.describe())
    except Exception as exc:  # noqa: BLE001 - the display must survive a bad weather
        print_exception(exc)
        weather = None
    ambient = Ambient(display, config, weather)
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

    # The phase-2 remote: a second producer of the same button events. It is
    # constructed even when disabled, so the log always says which way it is -
    # "disabled (no device token)" and "polling http://..." are very different
    # things to find on a board that is not responding to the phone.
    #
    # Built through _attach_remote so the same attempt can be repeated from the
    # loop: this construction happens right after the boot-time allocations and
    # HAS failed with MemoryError (2026-09-27, "memory allocation failed,
    # allocating 640 bytes" at /lib/remote.py line 50). The old code gave up
    # then and there, and the log never mentioned the remote again - a single
    # unlucky boot silently cost remote control for the whole session.
    remote, remote_retryable = _attach_remote(engine, config, log, firmware_version())
    remote_retries = REMOTE_ATTACH_RETRIES if (remote is None and remote_retryable) else 0
    remote_retry_at = time.ticks_ms()

    # Hand the remote the weather the idle screen is showing, so its poll can
    # report the panel's own reading (lib/remote.py) - the remote page and the
    # Homepage tile then draw the SAME indicator rather than asking a weather
    # API themselves and drifting from the panel. Done here, not at
    # construction, because the remote is built before the weather is; and
    # again in the retry path below, because a late attach gets the same
    # treatment as a boot-time one. A weather of None is normal and simply
    # means the poll carries no temp_c/condition.
    if remote is not None:
        remote.weather = weather

    # Collect proactively rather than only when an allocation fails: the
    # default (-1) lets the heap run to the wire, and an allocation failure at
    # the wrong moment takes the whole display down (it did - see ambient.py).
    gc.threshold(8192)

    # The fuse starts here for the app's sake, but be clear that it does not
    # END here: an armed WDT survives a soft reset, so the next boot's
    # updater runs under it too and has to feed it (lib/watchdog.py, and the
    # feed calls in updater._join_wifi / updater._download).
    if watchdog.arm():
        # One number now, not two: the update check runs on the same fuse as
        # everything else, because it is bounded to fit under it (see
        # _run_update_check). WDT_MAX_MS is the ceiling arm() will honour.
        log(f"wedge protection: watchdog armed at {watchdog.TIMEOUT_MS} ms")
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
        # A radio cycle the poller asked for is performed HERE, at the top of
        # the loop, never from inside poll_if_due: the poll runs immediately
        # before engine.tick(), and a CYW43 teardown while the matrix's PIO/DMA
        # is mid-frame was measured to do nothing at all (the association never
        # dropped and the radio stayed deaf). At the top of the loop the last
        # frame has finished writing, so the cycle meets an idle display. It is
        # bounded and feeds the fuse throughout, like everything else here.
        # Guarded with getattr, like the updater call below: an older lib/remote.py
        # without the deferred-cycle seam must not raise inside the loop.
        if remote is not None:
            take_cycle = getattr(remote, "take_cycle_request", None)
            if take_cycle is not None and take_cycle():
                remote.cycle_radio()
        # Retry a remote that failed to build at boot (see _attach_remote).
        # Bounded, spaced out, and honest when it stops: no remote control for
        # a session must never be something the log fails to mention. This sits
        # before the poll use below, so a remote that appears works the same
        # frame it appears.
        if (
            remote is None
            and remote_retries > 0
            and time.ticks_diff(now, remote_retry_at) >= REMOTE_ATTACH_RETRY_MS
        ):
            remote_retry_at = now
            remote_retries -= 1
            gc.collect()
            remote, retryable = _attach_remote(engine, config, log, firmware_version())
            if remote is not None:
                # Same hand-off as at boot: a remote that finally built must
                # report the weather too, or a late attach would silently stop
                # the page updating until the next reset.
                remote.weather = weather
            if remote is None:
                if not retryable:
                    remote_retries = 0
                elif remote_retries == 0:
                    log("remote: gave up building the remote this session")
        # One cheap look at the radio per second, written down where it survives
        # the session. This is the instrument for the one thing we still cannot
        # see from the outside: whether a DHCP failure leaves the board UP
        # without an IP, or RESETTING (see lib/wifihealth.py).
        if health is not None:
            health.sample()
        # Retire the release's chance only after the loop has demonstrably
        # kept feeding the fuse. A release that wedges here never gets this far,
        # so boot-ok.txt stays behind and boot.py counts the boot against it -
        # rolling it back only once BOOT_FAILS_MAX such boots have passed.
        # Done once, from the normal path, so it is a fact about the loop
        # rather than a timer that fired hopefully.
        if not boot_ok_written and time.ticks_diff(now, loop_started) >= BOOT_OK_SOAK_MS:
            boot_ok_written = True
            mark_boot_ok(log)
        # Retry the update from here, not just from boot.py: DHCP on this
        # network fails in windows of minutes, so an update that missed its
        # chance at boot should still land when the network clears. Placed AFTER
        # mark_boot_ok on purpose - the join inside it can block the display for
        # up to a wifi attempt, and a release must not have its soak interrupted
        # by its own update check. check_for_update resets the board if it
        # applies anything.
        if (
            time.ticks_diff(now, update_checked_at)
            >= getattr(config, "UPDATE_RETRY_MS", 15 * 60 * 1000)
            # Never while a routine is live: a join, or a slow fetch, still
            # blocks this thread for a moment, and a reboot here would unwind
            # the countdown and cost a child their timer. Since 2026-09-26 the
            # check no longer needs a longer fuse (it is bounded to fit under
            # the 8 s one - _run_update_check), so this gate is now a courtesy
            # rather than a defence against a guaranteed reset. It stays: a
            # stalled join is still a stalled panel.
            and engine.routine is None
            # ... and never while the remote poller says a COUNTDOWN/HANDOFF is
            # live (device-protocols.md section 8.3): the remote defers it too,
            # so the rule is stated once on each side of the seam.
            and (remote is None or not remote.busy())
        ):
            update_checked_at = time.ticks_ms()
            # lib/updater.py is EXCLUDED from the pack, so a release can reach
            # the board before the updater that backs it - this call has to
            # tolerate an older updater rather than raise inside the loop. The
            # feature is optional; the display is not.
            check = getattr(updater, "check_for_update", None)
            if check is not None:
                _run_update_check(check, config)
        try:
            # The remote, if present, gets one bounded poll at most per
            # cadence. It emits the SAME button event the panel would, so the
            # tick below handles it exactly as a physical press - nothing about
            # the state machine changes.
            if remote is not None:
                remote.poll_if_due(now)
            # Weather only while the panel is IDLE: the fetch may join WiFi,
            # which can block for a wifi attempt, and that must never share the
            # thread with a running timer (the same rule the remote follows).
            # engine.routine is None exactly in AMBIENT/OFF - never in PROMPT,
            # COUNTDOWN or HANDOFF.
            if weather is not None and engine.routine is None:
                weather.poll_if_due(now)
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
    except SystemExit:
        # An in-loop update that applies calls updater._reset() ->
        # machine.soft_reset(), which asks for the reboot by RAISING
        # SystemExit. Without this clause that exception is caught by the
        # BaseException handler below and written up as "MAIN DIED" - so every
        # successful in-loop update left a crash.log claiming the loop had
        # crashed, poisoning the one file kept for finding out why it really
        # did. (Measured on the board 2026-09-27: a real apply wrote
        # crash.log with updater.py line 370, in _reset, SystemExit.)
        #
        # It is re-raised on purpose, and that is the difference from boot.py,
        # which swallows the same exception. Catching SystemExit CANCELS the
        # soft reset (measured: `try: machine.soft_reset() except SystemExit`
        # prints its handler and stays at the REPL, no reboot). boot.py can
        # swallow it because boot.py then just falls through and runs the
        # freshly-applied main.py; here there is nothing after this block, so
        # swallowing would cancel the reboot, end the module and drop the board
        # to the REPL with the loop gone - leaving the watchdog to hard-reset
        # it 8 s later. Letting it through reboots straight into the new tree.
        print("unicorn: reboot to finish an update - not a crash, crash.log left alone")
        raise
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
