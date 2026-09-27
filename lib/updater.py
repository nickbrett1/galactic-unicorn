"""Firmware update from the LAN service, driven by boot.py.

Design memo: memos/CvaQ2nMNqaTvQbgYc8HJqW (the original, GitHub-over-HTTPS
design) and the 2026-09-26 root-cause memo, which is why the transport changed.

WHERE THE FILES COME FROM: not GitHub. The board cannot complete a TLS
handshake (measured 2026-09-26: DNS and TCP:443 are fine, plain HTTP works even
to the internet, and every HTTPS attempt fails - instantly as OSError(12,), or
by blocking until the watchdog hard-resets the board). So the LAN service
(galactic-unicorn-remote) fetches the release over HTTPS on the board's behalf
and serves `manifest.json` and `firmware.pack` over plain HTTP under /firmware/*.
See config.UPDATE_MANIFEST_URL. Integrity is unchanged: the manifest carries a
sha256 for the pack and one per file, checked here exactly as before.

Why this lives in boot.py's path and not main.py: MicroPython runs boot.py
BEFORE main.py, so this code still runs when main.py is broken. A release that
breaks main.py is repaired on the next boot instead of dead-ending the board.
The corollary is the rule that matters: lib/updater.py and boot.py are EXCLUDED
from the pack, because they are the only thing that can repair everything else.

Every constraint below was measured on the real Pico W (MicroPython v1.29.0),
not assumed:

  * no uzlib        -> the payload is UNCOMPRESSED: a flat run of
                       length-prefixed records (see scripts/build-firmware-pack.py)
  * a ~47 KB body read whole raises MemoryError even with 126 KB free - a
                    single contiguous allocation failure. So the pack is
                    STREAMED straight into the file through a sink (net.http_get
                    with `sink`), never held whole.
  * hashlib.sha256 has no hexdigest() -> ubinascii.hexlify(h.digest())
  * `import os` does NOT bind os.path -> never use os.path here. A stray
    os.path.exists once ran after a successful apply and got reported as a
    failed update.
  * `usocket` has no module-level default timeout on this board, and urequests
    builds its own socket - so `urequests` cannot be bounded at all, which is
    what made the old check able to block past the watchdog's fuse. Hence
    net.http_get, a raw socket with `settimeout`, which CAN be bounded (connect
    and every read).

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

import json
import os
import struct

import net

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

# boot.py's budget is ONE of those, deliberately. Its check is the one that
# runs before main.py can draw, so every second of it is a second of a lit
# panel with nothing else on it: measured on this board, three attempts cost
# 32 s of "one pixel" before the HELLO banner, and on the second boot it cost
# 32 s and then failed anyway ("wifi attempt 1/3 got no IP" ... 3/3), which is
# what this phase usually does. Its first join also happens while the radio is
# coldest - the same join from the running app gets an IP in ~4 s - so this is
# the least productive network call in the firmware.
#
# Nothing is lost by shortening it. The update it was trying to fetch is
# retried from the render loop (check_for_update, on UPDATE_RETRY_MS) with the
# full WIFI_ATTEMPTS and no panel to darken; and recovery, which is the part
# that must not wait for a network, runs BEFORE this and touches no radio at
# all.
BOOT_WIFI_ATTEMPTS = 1
LOG_FILE = "update.log"
MANIFEST_LIMIT = 16384
# Hard ceiling on the pack body, so a wrong or hostile server cannot stream the
# board into a full flash. The real pack is ~140 KB; 512 KB is generous margin.
MAX_PACK_BYTES = 512 * 1024

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


# --- wifi, delegated to the shared helper ----------------------------------
# The associate/reconnect helper lives in lib/net.py now, so the updater and
# the phase-2 remote poller share one radio implementation (and cannot drift).
# These two wrappers keep updater's public names, signatures and log lines, so
# nothing that imports updater - boot.py, main.py, tests/test_static_ip.py -
# has to change, and updater's behaviour is byte-for-byte what it was.

def apply_static_ip(wlan, config):
    """updater's spelling of net.apply_static_ip, logging to update.log."""
    return net.apply_static_ip(wlan, config, log=_log)


def _join_wifi(config, attempts=WIFI_ATTEMPTS, attempt_ms=WIFI_ATTEMPT_MS):
    """updater's join: net.join_wifi with updater's budget and log."""
    return net.join_wifi(config, attempts, attempt_ms, log=_log, feed=_wdt_feed)


def _fetch(url, timeout_s, limit=MANIFEST_LIMIT):
    """GET a small text document over plain HTTP. Returns the decoded body.

    This replaces the old `_get`/`_get_text` pair, which drove `urequests`.
    urequests could not be bounded (it builds its own socket, and `usocket` has
    no module-level default timeout on this board), and it was the call that
    blocked past the watchdog's fuse and hard-reset the board. See lib/net.py's
    http_get for the bounded replacement and why it is safe to run under the app
    fuse. `limit` is a hard cap on the body, so a wrong or hostile server cannot
    hand the board a document it cannot hold.
    """
    host, port, path = net.split_url(url)
    if host is None:
        raise ValueError("update url is not plain http: " + url)
    status, body = net.http_get(
        host, port, path, timeout_s, feed=_wdt_feed, read_cap=limit
    )
    if status != 200:
        raise OSError("http " + str(status))
    return body.decode()


def _download(url, dest, expect_sha, timeout_s, limit=MAX_PACK_BYTES):
    """Stream url into dest, hashing as we go. Returns the size.

    Streamed through a `sink`, so the pack is never held whole - this board's
    heap is ~126 KB and a single 47 KB read already fails. The sha256 is checked
    against the manifest's value before the caller is allowed to touch the
    staged file, so a truncated or corrupt transfer cannot reach the live tree.
    """
    import hashlib

    host, port, path = net.split_url(url)
    if host is None:
        raise ValueError("update url is not plain http: " + url)
    digest = hashlib.sha256()

    def sink(data):
        fh.write(data)
        digest.update(data)

    with open(dest, "wb") as fh:
        status, total = net.http_get(
            host, port, path, timeout_s, feed=_wdt_feed, sink=sink, read_cap=limit
        )
    if status != 200:
        raise OSError("http " + str(status))
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
    timeout_s = getattr(config, "UPDATE_TIMEOUT_S", 3)
    manifest = json.loads(_fetch(config.UPDATE_MANIFEST_URL, timeout_s))
    version = manifest["version"]
    current = _local_version()
    if version == current:
        # Logged rather than returned silently. A check that found nothing and a
        # check that never ran looked identical in every log we have, and "the
        # update did not land" is the single hardest thing to diagnose on this
        # board - it has cost several sessions. One line per boot is a cheap
        # price for being able to see the check happen.
        _log("no update: " + version + " is already running")
        return False
    pack = manifest["pack"]
    size = _download(
        base + "/" + pack["file"], PACK_PATH, pack["sha256"], timeout_s
    )
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
        # The SHORT budget: this is the call that runs behind a nearly-blank
        # panel. See BOOT_WIFI_ATTEMPTS.
        if not _join_wifi(config, attempts=BOOT_WIFI_ATTEMPTS):
            _log("no wifi at boot, skipping update (the loop retries)")
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
