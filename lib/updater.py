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
                    STREAMED straight into the tree through a sink (net.http_get
                    with `sink`): never held whole, and never resident on flash
                    either - see _PackSink for the second half, which is what
                    makes a full release fit at all.
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

  * before a release overwrites the tree, the files it is about to replace are
    MOVED into :prev/ (renamed, never copied: a copy is a third resident tree,
    and live + :next + :prev does not fit on this board's 768 KB filesystem)
  * main.py writes boot-ok.txt once it has actually come up
  * the next boot compares the two; a release that has had its single chance
    and never reported in gets the previous tree put back, offline, so the
    board comes up on something that works.
  * an apply that is interrupted is put back too: it announces itself in
    applying.txt before the first file moves, so a boot that finds that marker
    knows the live tree has holes in it and restores :prev even though
    version.txt and boot-ok.txt both still name the old, proven release.

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
# Kept, and still swept, but nothing writes it any more: the pack is staged
# straight into :next/ now (_PackSink). An older firmware can leave one behind,
# and on this board's 768 KB flash a single leftover 191 KB pack is enough to
# turn one ENOSPC into a permanent failure loop - which is exactly what was
# measured, so _cleanup still removes it.
PACK_PATH = ":incoming.pack"
NEXT_DIR = ":next"
# Hashing (a pack file, a rollback copy, the live tree) streams through this, so
# no step ever holds a whole file: on this board the heap is ~126 KB and the
# biggest managed file is ~30 KB, but it is the ALLOCATION that matters, not the
# file - a single contiguous read is what fails. Same reason the pack download
# streams through a sink.
SHA_CHUNK = 1024
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
# update.log is appended to for the life of the board and was never rotated:
# measured 2026-09-27 it had reached 112 KB on a filesystem with only ~360 KB
# free, i.e. a third of the budget spent on history - and the update then
# failed with OSError(28), ENOSPC. Past this size the file starts over, the
# same bargain lib/remote.py:LOG_LIMIT takes: losing old lines beats an
# unbounded file on a small flash.
LOG_LIMIT = 16384
MANIFEST_LIMIT = 16384
# Hard ceiling on the pack body, so a wrong or hostile server cannot stream the
# board into a full flash. The real pack is ~140 KB; 512 KB is generous margin.
MAX_PACK_BYTES = 512 * 1024

# --- rollback slot ---------------------------------------------------------
# What each file means. All are tiny and all are device-written.
#   version.txt     the release that is running now (written LAST by an apply)
#   boot-ok.txt     the release that has PROVEN it comes up (written by main.py)
#   boot-try.txt    the release whose boot chances are being spent right now
#   boot-fails.txt  how many judged boots that release has spent, still unproven
#   boot-wdt.txt    how many of those boots were cut short by the watchdog
#   prev.json       what the rollback copy is: {version, files, managed}
#
# A release is NOT discarded on one bad boot. It gets BOOT_FAILS_MAX boots that
# end without a boot-ok before it is rolled back. One was too few: on
# 2026-09-27 a single flaky first boot - a low-heap MemoryError, a radio wedge
# that stalled the loop past the soak, or a reset inside the window - rolled
# back a release that was perfectly good, twice.
PREV_DIR = ":prev"
PREV_INFO = "prev.json"
# Written before the first file moves in an apply, cleared once version.txt has
# been stamped. It is the difference between "an apply finished" and "an apply
# was interrupted", which the version files alone cannot express once files are
# MOVED into :prev rather than copied (see _plan_rollback and _apply).
APPLYING_FILE = "applying.txt"
BOOT_OK_FILE = "boot-ok.txt"
BOOT_TRY_FILE = "boot-try.txt"
BOOT_FAILS_FILE = "boot-fails.txt"
# A boot that ends without boot-ok is not always the release's fault. If the
# board was cut down from underneath it - a watchdog latch landing between
# _mark_attempt and main.py's boot-ok write - then the release never got the
# chance it was being charged for. Measured in the field 2026-09-27: release
# 0.1.38 reached 2 of 3 fails purely from watchdog resets, and one more would
# have rolled back a working release. machine.reset_cause(), read at the top of
# a boot, says how the PREVIOUS boot ended; cause 3 is the RP2040's WDT latch
# (the same test lib/watchdog.reset_was_watchdog makes). Those boots are counted
# separately instead, under a higher ceiling.
BOOT_FAILS_MAX = 3
RESET_CAUSE_WDT = 3
# The watchdog counter. It is HIGHER than BOOT_FAILS_MAX on purpose and must
# stay that way: a release that HANGS is also killed by the watchdog, so if a
# WDT reset never counted at all a genuinely broken release would never roll
# back - an infinite reboot loop, strictly worse than the bug being fixed. The
# watchdog path therefore still rolls back, just later; 6 gives a working
# release cut down by incidental resets real headroom while still bounding a
# hang to a handful of boots.
BOOT_WDT_FILE = "boot-wdt.txt"
BOOT_WDT_MAX = 6
UNKNOWN_VERSION = "dev"


def _log(message, exc=None):
    line = message if exc is None else message + ": " + repr(exc)
    print("update:", line)
    try:
        try:
            size = os.stat(LOG_FILE)[6]
        except OSError:
            size = 0
        if size > LOG_LIMIT:
            os.remove(LOG_FILE)
        with open(LOG_FILE, "a") as fh:
            fh.write(line + "\n")
    except Exception:  # noqa: BLE001, S110 - logging must never be fatal
        pass


def _log_traceback(exc):
    """Append a full traceback for `exc` to update.log.

    _log only records repr(exc), which for a MemoryError is a single line with
    no frame - and a bare "allocating 4352 bytes" is not enough to find which
    statement asked for it. This is temporary instrumentation: it is the reason
    we can pin an in-loop OTA failure at all, because the allocation happens
    deep inside a helper with no other way to see the frame. Best effort, like
    every other log here: a failure to log must never be the failure.
    """
    try:
        import sys

        size = 0
        try:
            size = os.stat(LOG_FILE)[6]
        except OSError:
            pass
        if size > LOG_LIMIT:
            os.remove(LOG_FILE)
        with open(LOG_FILE, "a") as fh:
            fh.write("update: traceback follows\n")
            sys.print_exception(exc, fh)
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


def _read_fails():
    """How many judged boots the pending release has spent. Absent or junk = 0."""
    try:
        return int(_read(BOOT_FAILS_FILE) or 0)
    except ValueError:
        return 0


def _read_wdt():
    """Watchdog-cut boots the pending release has spent. Absent or junk = 0."""
    try:
        return int(_read(BOOT_WDT_FILE) or 0)
    except ValueError:
        return 0


def _reset_cause():
    """machine.reset_cause(), or None when there is no machine module.

    Read at the top of a boot, this describes how the PREVIOUS boot terminated;
    RESET_CAUSE_WDT is the watchdog latch (see the constants above). It is a
    module-level function purely so tests can substitute it: the host running
    these tests has no `machine` module, so the try/except is also the safe
    fallback if the call ever fails on a board - both cases read as "not a WDT
    reset", which is the conservative choice (an ordinary boot is judged exactly
    as it always was).
    """
    try:
        import machine

        return machine.reset_cause()
    except Exception:  # noqa: BLE001 - no machine on the host, or a port quirk
        return None


def _is_wdt_reset():
    return _reset_cause() == RESET_CAUSE_WDT


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


class _PackSink:
    """Turn the streamed pack body into files under :next/, verifying as it goes.

    The pack is never resident on flash, and that is the whole reason this class
    exists. The old shape downloaded it whole to PACK_PATH and unpacked it
    afterwards, so the peak of an update was live + pack + :next at once: 57 +
    47 + 47 blocks of this board's 192, i.e. 94 of the 98 blocks that were free.
    Four blocks is not enough headroom for the littlefs metadata commits that
    writing :next still needs, and it was not theoretical - measured
    2026-09-28, every check of the 0.1.41 release (191289 B pack carrying
    190934 B of files) died in _unpack with OSError(28), ENOSPC, while the same
    release with a one-file pack applied fine. Streaming the body straight into
    :next removes the pack from the peak, and the pack was always the one part
    that is re-downloadable.

    Integrity is unchanged, and so is the ordering that matters:

      * the pack's own sha256 is still checked against the manifest, at the end
        rather than the start because the body is only complete then. Failing
        there costs a wasted download, not a broken board.
      * every file's sha256 is still checked against the manifest before _apply
        is allowed to adopt :next/, and the manifest's file LIST is still
        compared to what the pack actually carried, so a pack cannot drop a
        file and pass.
      * nothing here touches a live file. A stream that fails, truncates or
        verifies wrong leaves a partial :next/, never a half-written tree, and
        the caller's _cleanup removes it.

    `feed` is a state machine over the length-prefixed records rather than a
    loop over a buffer, because the body arrives in 256-byte reads and a record
    is up to ~30 KB - the whole point is to not accumulate one.
    """

    PATH_LEN = 0
    PATH = 1
    DATA_LEN = 2
    DATA = 3

    def __init__(self, files):
        import hashlib

        self._expected = {entry["path"]: entry["sha256"] for entry in files}
        self._seen = set()
        self._written = []
        self._pack = hashlib.sha256()
        self._buf = b""
        self._state = self.PATH_LEN
        self._path_len = 0
        self._path = None
        self._remaining = 0
        self._out = None
        self._file = None

    def feed(self, data):
        """One body slice from net.http_get's sink. Raises on a corrupt pack."""
        try:
            self._consume(data)
        except Exception:
            self.close()
            raise

    def _consume(self, data):
        import hashlib

        self._pack.update(data)
        self._buf += data
        while True:
            _wdt_feed()
            if self._state == self.PATH_LEN:
                if len(self._buf) < 4:
                    return
                self._path_len = struct.unpack(">I", self._buf[:4])[0]
                self._buf = self._buf[4:]
                self._state = self.PATH
            elif self._state == self.PATH:
                if len(self._buf) < self._path_len:
                    return
                self._path = self._buf[: self._path_len].decode()
                self._buf = self._buf[self._path_len:]
                self._state = self.DATA_LEN
            elif self._state == self.DATA_LEN:
                if len(self._buf) < 4:
                    return
                self._remaining = struct.unpack(">I", self._buf[:4])[0]
                self._buf = self._buf[4:]
                dest = NEXT_DIR + "/" + self._path
                _mkdirs(dest)
                self._out = open(dest, "wb")
                self._file = hashlib.sha256()
                self._state = self.DATA
                if self._remaining == 0:
                    self._close_file()
            else:  # DATA
                if not self._buf:
                    if self._remaining == 0:
                        self._close_file()
                    return
                take = min(len(self._buf), self._remaining)
                piece = self._buf[:take]
                self._buf = self._buf[take:]
                self._out.write(piece)
                self._file.update(piece)
                self._remaining -= take
                if self._remaining == 0:
                    self._close_file()

    def _close_file(self):
        """Finish one record: verify it against the manifest, then move on."""
        self._out.close()
        self._out = None
        if self._expected.get(self._path) != _hexdigest(self._file):
            raise ValueError("sha256 mismatch for " + self._path)
        self._seen.add(self._path)
        self._written.append(self._path)
        self._file = None
        self._path = None
        self._state = self.PATH_LEN

    def close(self):
        """Release the open output file, if any. Safe to call twice."""
        if self._out is not None:
            try:
                self._out.close()
            except OSError:
                pass
            self._out = None

    def finish(self, expect_sha):
        """The body is complete: verify the pack, and that it carried every file.

        Returns the staged paths, in pack order, for _apply. Everything checked
        here is still in :next/ - nothing has touched the live tree.
        """
        if self._state != self.PATH_LEN or self._out is not None:
            self.close()
            raise ValueError("truncated pack")
        if _hexdigest(self._pack) != expect_sha:
            raise ValueError("pack sha256 mismatch")
        if self._seen != set(self._expected):
            raise ValueError("pack does not match the manifest's file list")
        return self._written


def _stage(url, files, expect_sha, timeout_s, limit=MAX_PACK_BYTES):
    """Stream the pack into :next/, checking it as it arrives. Returns (paths, bytes).

    Replaces the old _download + _unpack pair. The transfer is still bounded
    (`read_cap` caps the body, `timeout_s` caps every socket call), and it still
    feeds the fuse throughout, so this remains safe to run under the app's
    watchdog from the render loop.
    """
    host, port, path = net.split_url(url)
    if host is None:
        raise ValueError("update url is not plain http: " + url)
    sink = _PackSink(files)
    failure = []

    def take(data):
        # Never raise from inside the socket loop. A non-200 carries a body too
        # (an error page), and letting the sink's parser fail on that would put
        # "truncated pack" in update.log when the server actually said 500 -
        # this codebase has lost sessions to exactly that kind of misdirection.
        # So the body is drained, the status is checked first, and only then is
        # the sink's own verdict raised. The drain is bounded by `read_cap`.
        if failure:
            return
        try:
            sink.feed(data)
        except Exception as exc:  # noqa: BLE001 - re-raised just below
            failure.append(exc)

    try:
        status, total = net.http_get(
            host, port, path, timeout_s, feed=_wdt_feed, sink=take, read_cap=limit
        )
    except Exception:
        sink.close()
        raise
    if status != 200:
        raise OSError("http " + str(status))
    if failure:
        raise failure[0]
    return sink.finish(expect_sha), total


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


def _sha256_file(path):
    """sha256 of a file, read in small chunks. Never holds the file whole."""
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            _wdt_feed()
            chunk = fh.read(SHA_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return _hexdigest(digest)


def _apply(written, version):
    """Install the staged tree, moving what it replaces into the rollback slot.

    Every replaced file is MOVED into :prev by rename, not copied. A copy is a
    whole extra resident tree - live + :next + :prev all at once - and on this
    board's 768 KB filesystem that is what made a full release fail with
    OSError(28), ENOSPC, in _archive_current's write (measured 2026-09-27):
    ~200 KB live + ~180 KB :next + ~180 KB :prev does not fit. Moving gives
    :prev the old files for free, and the peak is one tree plus its successor.
    """
    # Announced BEFORE the first move. From here until version.txt is stamped,
    # a file can be resident in :prev and not yet in the live tree, so the tree
    # is momentarily incomplete - and with the OLD version.txt still in place
    # boot.py would judge the running release proven (boot-ok matches) and DROP
    # the rollback copy, stranding the board on a broken tree. This marker is
    # what makes _recover roll back an apply that did not finish, whatever the
    # version files say.
    _write(APPLYING_FILE, version)
    for rel in written:
        # Renaming a file is fast, but `written` is the whole tree and this
        # runs under whatever fuse is already armed, so feed per file.
        _wdt_feed()
        dest = PREV_DIR + "/" + rel
        _mkdirs(dest)
        _clear(dest)
        try:
            os.rename(rel, dest)
        except OSError:
            pass  # a file this release ADDS has nothing to move aside
        os.rename(NEXT_DIR + "/" + rel, rel)
    # version.txt is written LAST: an interrupted apply re-runs the whole update
    # on the next boot rather than half-adopting it.
    with open(VERSION_FILE, "w") as fh:
        fh.write(version + "\n")
    _clear(APPLYING_FILE)
    # A fresh release starts with a clean watchdog slate. boot-fails and
    # boot-try are reset by _mark_attempt on this release's first boot (boot-try
    # still names the old release); the WDT counter is cleared here for the same
    # reason and to make the reset explicit rather than inherited.
    _clear(BOOT_WDT_FILE)


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


def _drop_proven_rollback():
    """Free the rollback slot once the running release has proven itself.

    boot-ok == version means this tree has come up and soaked, so the copy of
    what it replaced is dead weight. _recover drops it, but only on the boot
    AFTER boot-ok is written - and until it does, the slot sits there for the
    whole session. On this board's 768 KB filesystem that is ~167 KB the next
    in-loop update needs (measured 2026-09-27: free 424 KB clean, 44 KB with
    the slot resident), which is enough on its own to make it fail OSError(28).
    The running app is exactly where the in-loop check spends its flash, so it
    drops the slot here too, not only at boot.
    """
    version = _read(VERSION_FILE)
    if version and _read(BOOT_OK_FILE) == version:
        _clear(PREV_INFO)
        _rmtree(PREV_DIR)


def _plan_rollback(version, managed):
    """Record the rollback plan for a release that is about to be installed.

    This is the rollback slot's index, and it must live on FLASH: the whole
    point is to recover from a release that never comes up, and that must not
    depend on the network which delivered it, nor on the owner being in the
    room. The old tree's BYTES are not copied here - _apply moves each replaced
    file into :prev as it replaces it (see _apply for why a copy cannot fit) -
    but its hashes are, so _rollback can still prove the copy is intact before
    it puts anything back.

    `managed` is the incoming release's file list - exactly the set that is
    about to be replaced - and is recorded so a rollback can also delete any
    file the bad release ADDED, leaving the previous tree's shape and not a mix.
    """
    _rmtree(PREV_DIR)
    entries = []
    for path in managed:
        _wdt_feed()
        try:
            digest = _sha256_file(path)
        except OSError:
            continue  # not on the device yet, so there is nothing to put back
        entries.append({"path": path, "sha256": digest})
    with open(PREV_INFO, "w") as fh:
        json.dump({"version": version, "files": entries, "managed": list(managed)}, fh)
    return entries


def _rollback():
    """Put the previous tree back. Offline, and verified before anything moves.

    Same discipline as an apply: hash the whole copy first, then rename, then
    update version.txt. A corrupt rollback copy must not be half-applied on top
    of a tree that is already broken.
    """
    with open(PREV_INFO) as fh:
        info = json.load(fh)
    entries = info["files"]
    restored = set()
    for entry in entries:
        _wdt_feed()
        if _sha256_file(PREV_DIR + "/" + entry["path"]) != entry["sha256"]:
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
    _clear(BOOT_FAILS_FILE)
    _clear(BOOT_WDT_FILE)
    _clear(PREV_INFO)
    _clear(APPLYING_FILE)
    _rmtree(PREV_DIR)
    return info["version"]


def _recover():
    """Decide whether the running release deserves to stay. Offline.

    Returns True if the previous tree was restored. Three states, judged from
    small files:

      boot-ok == version        it has come up before; nothing to do
      boot-try != version       not yet judged: this boot is its first chance
      boot-try == version       it has been judged before and still has not
                                reported in. It gets BOOT_FAILS_MAX ordinary
                                boots like that; only the boot AFTER the last
                                one rolls it back, so one flaky boot does not
                                discard a release that would have come up on
                                the next.

    Those "ordinary" boots are the ones _mark_attempt charges to boot-fails. A
    boot the watchdog cut down is charged to boot-wdt instead (see
    _mark_attempt), and it takes BOOT_WDT_MAX of those to roll back - so an
    infrastructure reset does not spend a chance the release never had, and a
    release that truly hangs, which the watchdog also resets every boot, is
    still put back rather than rebooted forever.

    The whole thing is gated on a rollback copy existing. That is not just an
    optimisation: it also means the protocol only engages for trees this
    updater installed, so a tree deployed over USB - which may predate
    boot-ok.txt entirely - is never judged by rules it cannot satisfy.

    A fourth state is judged FIRST, before any of those: an apply that did not
    finish. It outranks them because it means files are missing from the live
    tree - moved into :prev but not yet replaced - so the tree must be put back
    even though version.txt and boot-ok.txt both still name the old, proven
    release. Judged in the normal order it would look proven, and its rollback
    copy would be dropped, stranding the board on a tree with holes in it.
    """
    if _read(APPLYING_FILE):
        if _has_rollback():
            previous = _rollback()
            _log(
                "recovered an interrupted apply: rolled back to "
                + (previous or UNKNOWN_VERSION)
            )
            _cleanup()
            return True
        _clear(APPLYING_FILE)  # nothing to put back: finish the bookkeeping
        return False

    version = _read(VERSION_FILE)
    if not version:
        return False  # not OTA-managed at all yet
    if _read(BOOT_OK_FILE) == version:
        # Proven: retire the attempt and counter, and drop the rollback slot
        # (a fresh one is taken before the next apply). Left resident the slot
        # is a permanent ~170 KB leak on this board's 768 KB filesystem -
        # measured 2026-09-27, and enough on its own to make every later update
        # fail with OSError(28,), ENOSPC.
        _clear(BOOT_TRY_FILE)
        _clear(BOOT_FAILS_FILE)
        _clear(BOOT_WDT_FILE)
        _clear(PREV_INFO)
        _rmtree(PREV_DIR)
        return False
    if not _has_rollback():
        return False
    if _read(BOOT_TRY_FILE) != version:
        return False  # this release's first boot: judgement is not due yet
    # Two counters, two ceilings. boot-fails counts ordinary unproven boots and
    # rolls back at BOOT_FAILS_MAX, exactly as it always has. boot-wdt counts
    # boots the watchdog cut short and rolls back only at the higher
    # BOOT_WDT_MAX - so incidental resets no longer discard a working release,
    # while a release that genuinely HANGS (which is also a watchdog reset every
    # boot) is still bounded rather than looping forever.
    if _read_fails() < BOOT_FAILS_MAX and _read_wdt() < BOOT_WDT_MAX:
        return False  # it still has boots left to prove itself
    if _read_wdt() >= BOOT_WDT_MAX:
        reason = (
            "it did not come up in "
            + str(BOOT_FAILS_MAX)
            + " boots and was cut down by the watchdog "
            + str(BOOT_WDT_MAX)
            + " times"
        )
    else:
        reason = "it did not come up in " + str(BOOT_FAILS_MAX) + " boots"
    previous = _rollback()
    _log(
        "rolled back from "
        + version
        + " ("
        + reason
        + ") to "
        + (previous or UNKNOWN_VERSION)
    )
    return True


def _update(config):
    base = config.UPDATE_MANIFEST_URL.rsplit("/", 1)[0]
    timeout_s = getattr(config, "UPDATE_TIMEOUT_S", 3)
    # Sweep staging before spending flash on this attempt. A previous attempt
    # that failed, or a rollback that did not finish, can leave the pack and
    # :next/ resident; both are re-derivable and both are pure cost until the
    # next run. On this board's 768 KB filesystem a single leftover pack is
    # enough to turn one ENOSPC into a permanent failure loop - measured
    # 2026-09-27: free fell to 236 KB and every update then failed OSError(28).
    _cleanup()
    _drop_proven_rollback()
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
    # Fetched straight into :next/ - the pack never lands on flash, so the peak
    # is :prev + :next rather than :prev + :next + pack. That is the difference
    # between 94 of this board's 98 free blocks and 47 of them, and the smaller
    # number is what makes a full 17-file release fit at all (_PackSink has the
    # measurement). Note the pack's sha256 is verified once the body is
    # complete, i.e. after :next/ has been written - safe, because :next is
    # discarded on any failure and the live tree is still untouched.
    written, size = _stage(
        base + "/" + pack["file"], manifest["files"], pack["sha256"], timeout_s
    )
    # Every file is verified in :next/ - now record the rollback plan for the
    # tree we are about to replace. Nothing is copied: _apply moves each
    # replaced file into :prev as it goes, so :next and :prev are never both
    # fully resident (see _plan_rollback and _apply).
    _plan_rollback(current or UNKNOWN_VERSION, [e["path"] for e in manifest["files"]])
    _apply(written, version)
    _log(
        "applied " + version + " (" + str(size) + " bytes, "
        + str(len(written)) + " files)"
    )
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
    """Record that the running release has just spent one boot unproven.

    Written at the very END of boot.py's work - once the update phase is over
    and main.py is about to run - and never earlier. That ordering is
    load-bearing: when this was written at the START of the boot, an interrupt
    during the updater's network phase (which is most of boot.py's runtime, and
    is exactly where an attached mpremote sends Ctrl-C) consumed a boot
    without main.py ever being reached, and the next boot rolled a perfectly
    good release back. Measured, on the board, v0.1.7 -> v0.1.6.

    The boot is COUNTED, not spent all at once: _recover only rolls back once
    this counter has reached BOOT_FAILS_MAX, so a release that fails to prove
    itself on one boot still has the next ones to try.

    A boot that the WATCHDOG cut down is different and is counted differently.
    machine.reset_cause() at the top of THIS boot reports how the PREVIOUS one
    ended, and a WDT latch there means the release was killed by something
    other than its own badness - an infrastructure reset, which may well have
    landed between this function and main.py's boot-ok write. Charging that to
    boot-fails is exactly what rolled a working 0.1.38 towards the brink on
    2026-09-27. So it is charged to boot-wdt instead, whose ceiling is higher.
    boot-try is still stamped either way, because the release is still being
    judged; only the counter it is judged against changes.
    """
    version = _read(VERSION_FILE)
    if not version or not _has_rollback():
        return  # protocol not engaged: never judge a tree we cannot put back
    if _read(BOOT_OK_FILE) == version:
        return  # already proven this boot; nothing to count
    if _read(BOOT_TRY_FILE) != version:
        # First boot of this release: start its counters from zero rather than
        # inheriting whatever an earlier release left behind.
        _write(BOOT_TRY_FILE, version)
        _write(BOOT_FAILS_FILE, "0")
        _write(BOOT_WDT_FILE, "0")
    if _is_wdt_reset():
        _write(BOOT_WDT_FILE, str(_read_wdt() + 1))
        return
    _write(BOOT_FAILS_FILE, str(_read_fails() + 1))


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
        _log_traceback(exc)
        _log("update check failed, keeping current firmware", exc)
        try:
            _cleanup()
        except Exception:  # noqa: BLE001, S110 - staging is a cost, not correctness
            pass
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
        _log_traceback(exc)
        _log("update failed, keeping current firmware", exc)
        try:
            _cleanup()
        except Exception:  # noqa: BLE001, S110
            pass
        return False
    if applied:
        _reset()
    return applied
