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
    and never reported in gets the previous tree put back, offline, and is
    recorded in bad.txt so it is never adopted again.

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
    from watchdog import reset_was_watchdog as _reset_was_watchdog
except ImportError:  # a tree without lib/watchdog.py: no fuse to feed, none to report

    def _wdt_feed():
        pass

    def _reset_was_watchdog():
        # Correct default, not a shrug: no lib/watchdog.py means no fuse was
        # ever armed by this tree, so a watchdog reset cannot be its doing and
        # _recover must not change its verdict on the strength of one.
        return False

VERSION_FILE = "version.txt"
PACK_PATH = ":incoming.pack"
NEXT_DIR = ":next"
CHUNK = 1024
WIFI_JOIN_MS = 15000
LOG_FILE = "update.log"
MANIFEST_LIMIT = 16384

# --- rollback slot ---------------------------------------------------------
# What each file means. All are tiny and all are device-written.
#   version.txt   the release that is running now (written LAST by an apply)
#   boot-ok.txt   the release that has PROVEN it comes up (written by main.py)
#   boot-try.txt  the release that has had its one chance at booting
#   bad.txt       a release that failed and was rolled back; never applied again
#   prev.json     what the rollback copy is: {version, files, managed}
PREV_DIR = ":prev"
PREV_INFO = "prev.json"
BOOT_OK_FILE = "boot-ok.txt"
BOOT_TRY_FILE = "boot-try.txt"
BAD_FILE = "bad.txt"
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


def _join_wifi(config):
    if not config.WIFI_SSID:
        return False
    import network

    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if wlan.isconnected():
        return True
    wlan.connect(config.WIFI_SSID, config.WIFI_PASSWORD)
    started = time.ticks_ms()
    while not wlan.isconnected():
        if time.ticks_diff(time.ticks_ms(), started) > WIFI_JOIN_MS:
            return False
        # The join can legitimately take 15 s and the fuse is 8 s, so this is
        # load-bearing, not tidiness: without it a slow join resets the board.
        _wdt_feed()
        time.sleep_ms(200)
    return True


def _get(url):
    """GET a URL and hand back the open response. Caller must close it."""
    import urequests

    response = urequests.get(url)
    if response.status_code != 200:
        code = response.status_code
        response.close()
        raise OSError("http " + str(code))
    return response


def _get_text(url, limit=MANIFEST_LIMIT):
    response = _get(url)
    try:
        return response.raw.read(limit).decode()
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


def _rollback(failed):
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
    _write(BAD_FILE, failed)
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

    "Comes up" is deliberately two separate claims, and boot-try being cleared
    the moment boot-ok is honoured is what separates them:

      the first boot    proves a release REACHES the point where main.py can
                        write boot-ok
      the first RESTART proves it STAYS ALIVE, because only then is boot-ok
                        honoured and boot-try retired

    That second claim needs evidence, and machine.reset_cause() supplies it: a
    watchdog reset means the boot that wrote boot-ok got that far and then
    stopped feeding, so it does not get to call itself healthy. Without this,
    a version that wedges every boot wrote boot-ok on its way up and then reset
    forever - the wedge protection turning "wedged forever" into "reset-loop
    forever", which is not recovery. Measured 2026-09-20: boot-ok == version,
    boot-try == version, WDT_RESET -> roll back.

    The whole thing is gated on a rollback copy existing. That is not just an
    optimisation: it also means the protocol only engages for trees this
    updater installed, so a tree deployed over USB - which may predate
    boot-ok.txt entirely - is never judged by rules it cannot satisfy.
    """
    version = _read(VERSION_FILE)
    if not version:
        return False  # not OTA-managed at all yet
    if _read(BOOT_OK_FILE) == version:
        if not _reset_was_watchdog():
            if _read(BOOT_TRY_FILE):  # proven: stop calling it a pending attempt
                _clear(BOOT_TRY_FILE)
            return False
        # Fall through: this release has NOT proved it stays alive. Leave
        # boot-try alone - it is the record of the chance being spent.
        _log("watchdog fired with boot-ok set: " + version + " did not stay alive")
    if not _has_rollback():
        return False
    if _read(BOOT_TRY_FILE) != version:
        return False  # has not had its chance yet
    previous = _rollback(version)
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
    # A release that already failed to come up must never be adopted again, or
    # the board would roll back to it and forward onto it forever.
    if version == _read(BAD_FILE):
        _log("skipping " + version + ": already rolled back from it")
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
