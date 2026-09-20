"""Firmware update from the latest GitHub Release, driven by boot.py.

Design memo: memos/CvaQ2nMNqaTvQbgYc8HJqW.

Why this lives in boot.py's path and not main.py: MicroPython runs boot.py
BEFORE main.py, so this code still runs when main.py is broken. A release that
breaks main.py is repaired on the next boot instead of dead-ending the board.
The corollary is the rule that matters: lib/updater.py and boot.py are EXCLUDED
from the pack, because they are the only thing that can repair everything else.

Every constraint below was measured on the real Pico W (MicroPython v1.29.0),
not assumed:

  * no uzlib        -> the payload is UNCOMPRESSED: a flat run of
                       length-prefixed records (see scripts/build-firmware-pack.py)
  * r.content on a ~47 KB body raises MemoryError even with 126 KB free - a
                    single contiguous allocation failure. So the body is
                    STREAMED with r.raw.read(CHUNK) straight into the file.
  * hashlib.sha256 has no hexdigest() -> ubinascii.hexlify(h.digest())
  * ssl.wrap_socket HANGS; ssl.SSLContext works.
  * `import os` does NOT bind os.path -> never use os.path here. A stray
    os.path.exists once ran after a successful apply and got reported as a
    failed update.
  * the flashed firmware ships no CA bundle, so the TLS channel is encrypted
    but NOT authenticated. Integrity rests on the manifest's sha256. This is a
    known, accepted compromise (the path is home-LAN -> GitHub on a device the
    owner controls), recorded in the memo - not an oversight.

Everything here is fail-soft: any failure leaves the current firmware exactly
as it was and returns, so main.py runs.

ROLLBACK: boot.py running first is what makes a bad release *repairable*, but
it is not by itself what makes it *recovered* - measured on hardware, a board
whose main.py raises just sits at the REPL forever, and repair then waits on
someone power-cycling it. So there is also a rollback slot:

  * before a release overwrites the tree, the tree is copied to :prev/
  * main.py writes boot-ok.txt once it has actually come up
  * the next boot compares the two; a release that has had its single chance
    and never reported in gets the previous tree put back, offline, so the
    board comes up on something that works.

Nothing is blacklisted. A release that fails this way is simply not running any
more, and the fix is to repair it and publish a new version on top - an owner
who can release can always outrun a bad release.

That covers the failure mode the drill exposed: a release that dies on a clean
Python exception, where nothing would otherwise ever reboot the board.
"""

import gc
import json
import os
import struct
import time

try:
    from watchdog import feed as _wdt_feed
except ImportError:  # a tree without lib/watchdog.py: no fuse to feed

    def _wdt_feed():
        pass

VERSION_FILE = "version.txt"
PACK_PATH = ":incoming.pack"
NEXT_DIR = ":next"
CHUNK = 1024
# One join ATTEMPT. Association is quick and reliable; it is IP acquisition
# that can hang for the whole attempt, so the useful knob is how many attempts
# we make, not how long a single one is (see _join_wifi).
WIFI_ATTEMPT_MS = 10000
WIFI_ATTEMPTS = 3
LOG_FILE = "update.log"
MANIFEST_LIMIT = 16384

# --- rollback slot ---------------------------------------------------------
# What each file means. All are tiny and all are device-written.
#   version.txt   the release that is running now (written LAST by an apply)
#   boot-ok.txt   the release that has PROVEN it comes up (written by main.py)
#   boot-try.txt  the release that has had its one chance at booting
#   prev.json     what the rollback copy is: {version, files, managed}
PREV_DIR = ":prev"
PREV_INFO = "prev.json"
BOOT_OK_FILE = "boot-ok.txt"
BOOT_TRY_FILE = "boot-try.txt"
UNKNOWN_VERSION = "dev"


def _log(message, exc=None):
    line = message if exc is None else message + ": " + repr(exc)
    print("update:", line)
    try:
        with open(LOG_FILE, "a") as fh:
            fh.write(line + "\n")
    except Exception:  # noqa: BLE001, S110 - logging must never be fatal
        pass


def _hexdigest(digest):
    try:
        import ubinascii

        return ubinascii.hexlify(digest.digest()).decode()
    except ImportError:  # pragma: no cover - host-side testing only
        # The board has no hexdigest(); the host has no ubinascii. Same value.
        return digest.hexdigest()


def _read(name):
    """Contents of a small state file, or "" if it is not there."""
    try:
        with open(name) as fh:
            return fh.read().strip()
    except Exception:  # noqa: BLE001 - absent and unreadable are the same here
        return ""


def _write(name, text):
    with open(name, "w") as fh:
        fh.write(text + "\n")


def _clear(name):
    try:
        os.remove(name)
    except OSError:
        pass


def _local_version():
    return _read(VERSION_FILE)


def _wdt_sleep(ms):
    """Sleep in fuse-sized steps. The fuse is 8 s and an attempt is 10 s."""
    started = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), started) < ms:
        _wdt_feed()
        time.sleep_ms(200)


def _wait_for_ip(wlan, budget_ms):
    """True once the station has an IP. Feeds the fuse while it waits."""
    started = time.ticks_ms()
    while not wlan.isconnected():
        if time.ticks_diff(time.ticks_ms(), started) > budget_ms:
            return False
        # The join can legitimately outlast the 8 s fuse, so this is
        # load-bearing, not tidiness: without it a slow join resets the board.
        _wdt_feed()
        time.sleep_ms(200)
    return True


def _join_wifi(config):
    if not config.WIFI_SSID:
        return False
    import network

    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if wlan.isconnected():
        return True
    # Association was never the problem: the board reaches "associated, no IP"
    # (status 2) within a second or two and then sits there while DHCP never
    # completes - for the WHOLE attempt. Measured on this board: boot.py's first
    # join burned the full timeout and logged "no wifi", while main.py's join
    # seconds later on the same radio got an IP in 4 s; a manual join got an IP
    # 6/6 on one run and 0/6 three minutes later. So a single attempt is a coin
    # flip, and a retry is what converts it - but the DHCP exchange has to be
    # restarted, which needs a disconnect first.
    for attempt in range(1, WIFI_ATTEMPTS + 1):
        if attempt > 1:
            try:
                wlan.disconnect()
            except Exception:  # noqa: BLE001, S110 - not connected is fine
                pass
            _wdt_sleep(500)
        try:
            wlan.connect(config.WIFI_SSID, config.WIFI_PASSWORD)
        except OSError as exc:
            _log("wifi connect raised", exc)
        if _wait_for_ip(wlan, WIFI_ATTEMPT_MS):
            return True
        _log(f"wifi attempt {attempt}/{WIFI_ATTEMPTS} got no IP")
    return False


def _get(url):
    """GET a URL and hand back the open response. Caller must close it."""
    import urequests

    # DNS + TCP + the TLS handshake all happen inside urequests.get and none of
    # it can feed the fuse, so do it on both sides of the call. This matters
    # only when the fuse is already armed - which it is whenever boot.py runs
    # after a SOFT reset, because an armed watchdog survives one - but there it
    # is the difference between a download and a reset mid-download. Measured:
    # an armed 8 s fuse killed a successful join's update with no output at all.
    _wdt_feed()
    # Collect BEFORE the call, not after it. The handshake allocates its
    # in/out buffers as single contiguous blocks, and the largest one this
    # heap will hand out is 16 KB - so the second of those is what a
    # fragmented heap refuses, and it refuses it as OSError(12) (ENOMEM)
    # rather than MemoryError, because the allocation happens in C.
    #
    # Measured on the board, running the check from the render loop rather
    # than from boot.py, where the heap is clean:
    #   update: update check failed, keeping current firmware: OSError(12,)
    # three times in a row, while the wifi line beside each one read
    # status=3(up) - so the network was up and the heap was the whole problem.
    # A collect immediately before the handshake made the same fetch succeed
    # on its first attempt, with biggest_block at 16384.
    gc.collect()
    response = urequests.get(url)
    _wdt_feed()
    if response.status_code != 200:
        code = response.status_code
        response.close()
        raise OSError("http " + str(code))
    return response


def _get_text(url, limit=MANIFEST_LIMIT):
    response = _get(url)
    try:
        # Read in small pieces rather than in one `limit`-byte allocation. The
        # manifest is well under a kilobyte, but `read(limit)` reserves the
        # whole 16 KB up front and that single contiguous allocation is what a
        # fragmented heap refuses first. Measured on the board, with the update
        # driven from the REPL instead of from boot.py:
        #   MemoryError('memory allocation failed, allocating 16384 bytes')
        # and the join that preceded it had succeeded, so the window was real
        # and the update was lost to the buffer, not the network.
        gc.collect()
        parts = []
        total = 0
        while total < limit:
            chunk = response.raw.read(256)
            if not chunk:
                break
            parts.append(chunk)
            total += len(chunk)
        return b"".join(parts).decode()
    finally:
        response.close()


def _download(url, dest, expect_sha):
    """Stream url into dest in chunks, hashing as we go. Returns the size."""
    import hashlib

    response = _get(url)
    digest = hashlib.sha256()
    total = 0
    try:
        with open(dest, "wb") as fh:
            while True:
                _wdt_feed()
                gc.collect()
                chunk = response.raw.read(CHUNK)
                if not chunk:
                    break
                fh.write(chunk)
                digest.update(chunk)
                total += len(chunk)
    finally:
        response.close()
    if _hexdigest(digest) != expect_sha:
        raise ValueError("pack sha256 mismatch")
    return total


def _mkdirs(path):
    parts = path.split("/")
    cur = ""
    for part in parts[:-1]:
        cur = part if not cur else cur + "/" + part
        if not cur:
            continue
        try:
            os.mkdir(cur)
        except OSError:
            pass


def _rmtree(path):
    try:
        entries = os.listdir(path)
    except OSError:
        try:
            os.remove(path)
        except OSError:
            pass
        return
    for name in entries:
        _rmtree(path + "/" + name)
    try:
        os.rmdir(path)
    except OSError:
        pass


def _unpack(files):
    """Split PACK_PATH into NEXT_DIR/, hashing and checking every file.

    Nothing here touches a live file: a corrupt or truncated pack fails while
    everything is still in :next, so the running firmware is never half-written.
    """
    import hashlib

    expected = {entry["path"]: entry["sha256"] for entry in files}
    seen = set()
    written = []
    with open(PACK_PATH, "rb") as fh:
        while True:
            header = fh.read(4)
            if not header:
                break
            if len(header) != 4:
                raise ValueError("truncated pack (path length)")
            path = fh.read(struct.unpack(">I", header)[0]).decode()
            header = fh.read(4)
            if len(header) != 4:
                raise ValueError("truncated pack (data length)")
            remaining = struct.unpack(">I", header)[0]
            dest = NEXT_DIR + "/" + path
            _mkdirs(dest)
            digest = hashlib.sha256()
            with open(dest, "wb") as out:
                while remaining > 0:
                    _wdt_feed()
                    chunk = fh.read(min(CHUNK, remaining))
                    if not chunk:
                        raise ValueError("truncated pack (data)")
                    out.write(chunk)
                    digest.update(chunk)
                    remaining -= len(chunk)
            if expected.get(path) != _hexdigest(digest):
                raise ValueError("sha256 mismatch for " + path)
            seen.add(path)
            written.append(path)
    if seen != set(expected):
        raise ValueError("pack does not match the manifest's file list")
    return written


def _cleanup():
    """Delete the staging files. Best-effort only.

    A leftover pack or :next costs flash, not correctness. This matters
    because the first end-to-end run proved the opposite: on this MicroPython
    build `import os` does NOT bind os.path, so a stray `os.path.exists` here
    raised AttributeError AFTER a fully successful apply, and the caller's
    handler reported the whole update as failed. Hence the explicit
    try-os.listdir probe (no os.path anywhere in this file).
    """
    try:
        os.remove(PACK_PATH)
    except OSError:
        pass
    try:
        os.listdir(NEXT_DIR)
    except OSError:
        return
    _rmtree(NEXT_DIR)


def _apply(written, version):
    for rel in written:
        # Renaming a file is fast, but `written` is the whole tree and this
        # runs under whatever fuse is already armed, so feed per file.
        _wdt_feed()
        os.rename(NEXT_DIR + "/" + rel, rel)
    # version.txt is written LAST: an interrupted apply re-runs the whole update
    # on the next boot rather than half-adopting it.
    with open(VERSION_FILE, "w") as fh:
        fh.write(version + "\n")


def _reset():
    import machine

    try:
        machine.soft_reset()
    except AttributeError:
        machine.reset()


def _has_rollback():
    """True if there is a rollback copy. Missing and unreadable are the same."""
    try:
        os.listdir(PREV_DIR)
        return True
    except OSError:
        return False


def _archive_current(version, managed):
    """Copy the live tree into :prev before a release overwrites it.

    This is the rollback slot, and it must live on FLASH: the whole point is to
    recover from a release that never comes up, and that must not depend on the
    network which delivered it, nor on the owner being in the room.

    `managed` is the incoming release's file list - exactly the set that is
    about to be replaced - and is recorded so a rollback can also delete any
    file the bad release ADDED, leaving the previous tree's shape and not a mix.
    """
    import hashlib

    _rmtree(PREV_DIR)
    entries = []
    for path in managed:
        _wdt_feed()
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            continue  # not on the device yet, so there is nothing to put back
        dest = PREV_DIR + "/" + path
        _mkdirs(dest)
        with open(dest, "wb") as out:
            out.write(data)
        entries.append({"path": path, "sha256": _hexdigest(hashlib.sha256(data))})
    with open(PREV_INFO, "w") as fh:
        json.dump({"version": version, "files": entries, "managed": list(managed)}, fh)
    return entries


def _rollback():
    """Put the previous tree back. Offline, and verified before anything moves.

    Same discipline as an apply: hash the whole copy first, then rename, then
    update version.txt. A corrupt rollback copy must not be half-applied on top
    of a tree that is already broken.
    """
    import hashlib

    with open(PREV_INFO) as fh:
        info = json.load(fh)
    entries = info["files"]
    restored = set()
    for entry in entries:
        _wdt_feed()
        with open(PREV_DIR + "/" + entry["path"], "rb") as fh:
            data = fh.read()
        if _hexdigest(hashlib.sha256(data)) != entry["sha256"]:
            raise ValueError("rollback copy of " + entry["path"] + " is corrupt")
        restored.add(entry["path"])
    # Files the failed release added are removed, so the tree is the previous
    # release's shape rather than a mixture of the two.
    for path in info.get("managed", []):
        if path not in restored:
            _clear(path)
    for entry in entries:
        dest = PREV_DIR + "/" + entry["path"]
        _mkdirs(entry["path"])
        os.rename(dest, entry["path"])
    _write(VERSION_FILE, info["version"] or UNKNOWN_VERSION)
    _clear(BOOT_TRY_FILE)
    _clear(PREV_INFO)
    _rmtree(PREV_DIR)
    return info["version"]


def _recover():
    """Decide whether the running release deserves to stay. Offline.

    Returns True if the previous tree was restored. Three states, judged from
    two small files:

      boot-ok == version   it has come up before; nothing to do
      boot-try != version  this is its FIRST boot - give it the one chance
      boot-try == version  it has had that chance and still has not reported
                           in, so it never came up: put the previous tree back

    The whole thing is gated on a rollback copy existing. That is not just an
    optimisation: it also means the protocol only engages for trees this
    updater installed, so a tree deployed over USB - which may predate
    boot-ok.txt entirely - is never judged by rules it cannot satisfy.
    """
    version = _read(VERSION_FILE)
    if not version:
        return False  # not OTA-managed at all yet
    if _read(BOOT_OK_FILE) == version:
        if _read(BOOT_TRY_FILE):  # proven: stop calling it a pending attempt
            _clear(BOOT_TRY_FILE)
        return False
    if not _has_rollback():
        return False
    if _read(BOOT_TRY_FILE) != version:
        return False  # has not had its chance yet
    previous = _rollback()
    _log(
        "rolled back from "
        + version
        + " (it never came up) to "
        + (previous or UNKNOWN_VERSION)
    )
    return True


def _update(config):
    base = config.UPDATE_MANIFEST_URL.rsplit("/", 1)[0]
    manifest = json.loads(_get_text(config.UPDATE_MANIFEST_URL))
    version = manifest["version"]
    current = _local_version()
    if version == current:
        return False
    pack = manifest["pack"]
    size = _download(base + "/" + pack["file"], PACK_PATH, pack["sha256"])
    written = _unpack(manifest["files"])
    # Only now - new pack downloaded, every file verified in :next/ - spend the
    # flash on a rollback copy of the tree we are about to replace.
    _archive_current(current or UNKNOWN_VERSION, [e["path"] for e in manifest["files"]])
    _apply(written, version)
    _log("applied " + version + " (" + str(size) + " bytes, " + str(len(written)) + " files)")
    # Housekeeping must never be able to undo a good apply: the firmware is
    # already in place and version.txt already stamped by this point, so a
    # failure here is cosmetic and must be reported as such, not as a failed
    # update (which is exactly what the first end-to-end run got wrong).
    try:
        _cleanup()
    except Exception as exc:  # noqa: BLE001
        _log("cleanup failed (harmless, firmware is in place)", exc)
    return True


def _mark_attempt():
    """Record that the running release has had its one chance at coming up.

    Written at the very END of boot.py's work - once the update phase is over
    and main.py is about to run - and never earlier. That ordering is
    load-bearing: when this was written at the START of the boot, an interrupt
    during the updater's network phase (which is most of boot.py's runtime, and
    is exactly where an attached mpremote sends Ctrl-C) consumed the chance
    without main.py ever being reached, and the next boot rolled a perfectly
    good release back. Measured, on the board, v0.1.7 -> v0.1.6.
    """
    version = _read(VERSION_FILE)
    if not version or not _has_rollback():
        return  # protocol not engaged: never judge a tree we cannot put back
    if _read(BOOT_OK_FILE) != version:
        _write(BOOT_TRY_FILE, version)


def check_for_update(config):
    """Try the whole update once, from the running app. Never raises.

    boot.py only gets one look at the network per boot, and this network fails
    in WINDOWS of minutes rather than failing outright - measured on the board,
    six joins got an IP in ~3 s and six more, minutes later, got none. A
    boot-time budget cannot outlast that, so the loop retries on a slow timer,
    where a window that opens an hour later is still caught.

    Deliberately does NOT run recovery. Putting a previous tree back is boot.py's
    job, and a running app is by definition a tree that already came up; judging
    it here could roll back a release that is working perfectly well.

    Returns True only if it applied an update, and resets on the way out so the
    new tree actually runs - _apply renames files over the live tree, so the
    interpreter would otherwise keep running the old code it already imported.
    """
    try:
        if not getattr(config, "UPDATE_ENABLED", False):
            return False
        if not _join_wifi(config):
            return False
        applied = _update(config)
    except Exception as exc:  # noqa: BLE001 - an update must not stop the display
        _log("update check failed, keeping current firmware", exc)
        return False
    if applied:
        _reset()
    return applied


def run():
    """Called from boot.py. Never raises; returns True only if it applied an update."""
    applied = False
    try:
        applied = _run()
    finally:
        # Whatever path _run took, boot.py is finishing and main.py is next, so
        # THIS is the boot that counts as the release's chance to prove itself.
        try:
            _mark_attempt()
        except Exception:  # noqa: BLE001, S110 - bookkeeping must not stop main.py
            pass
    return applied


def _run():
    try:
        import config

        if not getattr(config, "UPDATE_ENABLED", False):
            return False
    except Exception as exc:  # noqa: BLE001 - must never stop main.py
        _log("cannot start update, skipping", exc)
        return False

    # Phase 1: recovery. FIRST, and deliberately before the wifi join - a board
    # whose release never came up must be repaired without the network that
    # delivered it, and without waiting on one that may not be there.
    try:
        if _recover():
            return False  # previous tree is back; this boot can run it as-is
    except Exception as exc:  # noqa: BLE001 - a failed recovery still boots
        _log("recovery failed", exc)

    # Phase 2: update.
    try:
        if not _join_wifi(config):
            _log("no wifi, skipping update")
            return False
    except Exception as exc:  # noqa: BLE001 - must never stop main.py
        _log("cannot start update, skipping", exc)
        return False
    try:
        applied = _update(config)
    except Exception as exc:  # noqa: BLE001 - any failure keeps the current firmware
        _log("update failed, keeping current firmware", exc)
        try:
            _cleanup()
        except Exception:  # noqa: BLE001, S110
            pass
        return False
    if applied:
        _reset()
    return applied
