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
"""

import gc
import json
import os
import struct
import time

VERSION_FILE = "version.txt"
PACK_PATH = ":incoming.pack"
NEXT_DIR = ":next"
CHUNK = 1024
WIFI_JOIN_MS = 15000
LOG_FILE = "update.log"
MANIFEST_LIMIT = 16384


def _log(message, exc=None):
    line = message if exc is None else message + ": " + repr(exc)
    print("update:", line)
    try:
        with open(LOG_FILE, "a") as fh:
            fh.write(line + "\n")
    except Exception:  # noqa: BLE001, S110 - logging must never be fatal
        pass


def _hexdigest(digest):
    import ubinascii

    return ubinascii.hexlify(digest.digest()).decode()


def _local_version():
    try:
        with open(VERSION_FILE) as fh:
            return fh.read().strip()
    except Exception:  # noqa: BLE001
        return ""


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


def _update(config):
    base = config.UPDATE_MANIFEST_URL.rsplit("/", 1)[0]
    manifest = json.loads(_get_text(config.UPDATE_MANIFEST_URL))
    version = manifest["version"]
    if version == _local_version():
        return False
    pack = manifest["pack"]
    size = _download(base + "/" + pack["file"], PACK_PATH, pack["sha256"])
    written = _unpack(manifest["files"])
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


def run():
    """Called from boot.py. Never raises; returns True only if it applied an update."""
    try:
        import config

        if not getattr(config, "UPDATE_ENABLED", False):
            return False
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
