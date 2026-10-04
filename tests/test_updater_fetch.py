#!/usr/bin/env python3
"""Host tests for the updater's fetch/verify/apply path. No board.

    python3 tests/test_updater_fetch.py

This is the seam that changed on 2026-09-26: the updater used to pull the
manifest and pack from GitHub over HTTPS, which this board cannot do (see
config.UPDATE_MANIFEST_URL), and now pulls them over plain HTTP from the LAN
service through `net.http_get` - a bounded raw socket. These tests drive
`_fetch`, `_stage` and a whole `_update` against a real local HTTP server, so
the pack format, the sha256 checks and the "version.txt is written LAST" rule
are all exercised without hardware. `_stage` replaced `_download`/`_unpack` on
2026-09-28: the pack is streamed straight into :next/ and never written to
flash, which is what lets a full 17-file release fit on the board at all.

`_update` is driven in a temporary working directory with the device files
redirected into it, because it is meant to write to the board's flash root.
"""

import hashlib
import json
import os
import shutil
import struct
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.path.insert(0, ROOT)

import updater

ROUTES = {}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        spec = ROUTES.get(self.path)
        if spec is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        status, body = spec
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def _serve():
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def _pack(files):
    """The board's pack format: length-prefixed path, length-prefixed data."""
    blob = bytearray()
    entries = []
    for path, data in files.items():
        pb = path.encode()
        blob += struct.pack(">I", len(pb)) + pb
        blob += struct.pack(">I", len(data)) + data
        entries.append({"path": path, "sha256": hashlib.sha256(data).hexdigest()})
    return bytes(blob), entries


def _report(label, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL':<4} {label:<52} {detail}")
    return ok


class Config:
    def __init__(self, url):
        self.UPDATE_ENABLED = True
        self.UPDATE_MANIFEST_URL = url
        self.UPDATE_TIMEOUT_S = 3


def case_fetch_reads_the_manifest():
    ROUTES["/f/manifest.json"] = (200, b'{"version":"9.9.9"}')
    text = updater._fetch(f"http://127.0.0.1:{PORT}/f/manifest.json", 3)
    ok = json.loads(text)["version"] == "9.9.9"
    return _report("_fetch reads a plain-HTTP document", ok, text)


def case_fetch_rejects_a_tls_url():
    # An https URL is a configuration error, not something to silently downgrade:
    # this board cannot do TLS (config.UPDATE_MANIFEST_URL).
    try:
        updater._fetch("https://github.com/x/manifest.json", 3)
        ok = False
    except ValueError:
        ok = True
    return _report("_fetch refuses an https URL", ok, "ValueError")


def case_fetch_rejects_a_non_200():
    ROUTES["/f/500"] = (500, b"boom")
    try:
        updater._fetch(f"http://127.0.0.1:{PORT}/f/500", 3)
        ok = False
    except OSError as exc:
        ok = "http 500" in str(exc)
    return _report("_fetch raises on a non-200", ok, "OSError http 500")


def case_stage_streams_into_next_and_verifies_every_file():
    # The pack is no longer written to flash at all: it is streamed straight
    # into :next/. What matters is unchanged - the files land, byte for byte.
    files = {"main.py": b"print('main')\n" * 40, "lib/net.py": b"NET = 1\n" * 90}
    blob, entries = _pack(files)
    ROUTES["/f/stage.pack"] = (200, blob)
    _device_files(TMP)
    _rm_next()
    cwd = os.getcwd()
    os.chdir(TMP)
    try:
        written, size = updater._stage(
            f"http://127.0.0.1:{PORT}/f/stage.pack",
            entries,
            hashlib.sha256(blob).hexdigest(),
            3,
        )
        got = {}
        for rel in sorted(files):
            with open(updater.NEXT_DIR + "/" + rel, "rb") as fh:
                got[rel] = fh.read()
        left_pack = os.path.exists(updater.PACK_PATH)
    finally:
        os.chdir(cwd)
    ok = (
        sorted(written) == sorted(files)
        and got == files
        and size == len(blob)
        and not left_pack
    )
    return _report(
        "_stage streams the pack into :next and verifies every file",
        ok,
        f"files={len(written)} size={size} pack_written={left_pack}",
    )


def case_stage_rejects_a_bad_pack_sha256():
    files = {"main.py": b"print('main')\n"}
    blob, entries = _pack(files)
    ROUTES["/f/bad.pack"] = (200, blob)
    _device_files(TMP)
    _rm_next()
    cwd = os.getcwd()
    os.chdir(TMP)
    try:
        updater._stage(
            f"http://127.0.0.1:{PORT}/f/bad.pack", entries, "0" * 64, 3
        )
        ok = False
        detail = "no raise"
    except ValueError as exc:
        ok = "pack sha256 mismatch" in str(exc)
        detail = "ValueError " + str(exc)
    finally:
        os.chdir(cwd)
    return _report("_stage refuses a pack whose sha256 is wrong", ok, detail)


def case_stage_rejects_a_truncated_pack():
    # A body that stops mid-record must not be adopted. This is the failure the
    # old code expressed as "truncated pack (data)" from a file read; the sink
    # has to catch it from a short stream instead.
    files = {"main.py": b"print('main')\n" * 200}
    blob, entries = _pack(files)
    ROUTES["/f/cut.pack"] = (200, blob[: len(blob) // 2])
    _device_files(TMP)
    _rm_next()
    cwd = os.getcwd()
    os.chdir(TMP)
    try:
        updater._stage(
            f"http://127.0.0.1:{PORT}/f/cut.pack",
            entries,
            hashlib.sha256(blob).hexdigest(),
            3,
        )
        ok = False
        detail = "no raise"
    except ValueError as exc:
        ok = "truncated" in str(exc)
        detail = "ValueError " + str(exc)
    finally:
        os.chdir(cwd)
    return _report("_stage refuses a truncated pack", ok, detail)


def case_stage_rejects_a_pack_missing_a_manifest_file():
    # The manifest's file LIST is binding: a pack that carries fewer files than
    # it promised is rejected even if every file it did carry is intact.
    files = {"main.py": b"print('main')\n", "lib/net.py": b"NET = 1\n"}
    # The manifest (entries) promises two files; the pack carries only one, and
    # its own sha256 is honest about that, so only the LIST check can catch it.
    _full, entries = _pack(files)
    blob, _one = _pack({"main.py": files["main.py"]})
    ROUTES["/f/short.pack"] = (200, blob)
    _device_files(TMP)
    _rm_next()
    cwd = os.getcwd()
    os.chdir(TMP)
    try:
        updater._stage(
            f"http://127.0.0.1:{PORT}/f/short.pack",
            entries,
            hashlib.sha256(blob).hexdigest(),
            3,
        )
        ok = False
        detail = "no raise"
    except ValueError as exc:
        ok = "file list" in str(exc)
        detail = "ValueError " + str(exc)
    finally:
        os.chdir(cwd)
    return _report("_stage refuses a pack missing a manifest file", ok, detail)


def case_update_applies_a_release():
    # The whole path: manifest over HTTP -> pack over HTTP -> unpack -> verify
    # every file -> archive -> apply -> version.txt. This is what boots the
    # board onto a new release, so it is the one thing worth an end-to-end test.
    files = {
        "main.py": b"print('new main')\n",
        "lib/thing.py": b"VALUE = 2\n",
    }
    blob, entries = _pack(files)
    manifest = {
        "name": "galactic-unicorn",
        "version": "9.9.9",
        "pack": {"file": "firmware.pack", "sha256": hashlib.sha256(blob).hexdigest()},
        "files": entries,
    }
    ROUTES["/f/manifest.json"] = (200, json.dumps(manifest).encode())
    ROUTES["/f/firmware.pack"] = (200, blob)

    _device_files(TMP)
    cwd = os.getcwd()
    os.chdir(TMP)
    try:
        with open(updater.VERSION_FILE, "w") as fh:
            fh.write("9.9.8\n")
        os.mkdir("lib")
        with open("lib/thing.py", "w") as fh:
            fh.write("VALUE = 1\n")
        applied = updater._update(Config(f"http://127.0.0.1:{PORT}/f/manifest.json"))
        with open(updater.VERSION_FILE) as fh:
            stamp = fh.read().strip()
        with open("main.py") as fh:
            new_main = fh.read()
        prev_exists = os.listdir(updater.PREV_DIR)
    finally:
        os.chdir(cwd)

    ok = (
        applied is True
        and stamp == "9.9.9"
        and "new main" in new_main
        and bool(prev_exists)
    )
    return _report(
        "a release is fetched, verified, applied and stamped",
        ok,
        f"applied={applied} version={stamp} rollback={bool(prev_exists)}",
    )


def case_apply_moves_the_old_tree_into_the_rollback_slot():
    # The replaced files must land in :prev by RENAME, not by copy. A copy is a
    # whole extra resident tree, and on the board's 768 KB filesystem
    # live + :next + :prev does not fit: that is what made a full release fail
    # with OSError(28), ENOSPC in _archive_current's write (measured
    # 2026-09-27). This asserts the old bytes survive in :prev and that the
    # apply did not also leave the source behind.
    files = {"main.py": b"print('new main')\n", "lib/thing.py": b"VALUE = 2\n"}
    blob, entries = _pack(files)
    manifest = {
        "name": "galactic-unicorn",
        "version": "9.9.9",
        "pack": {"file": "firmware.pack", "sha256": hashlib.sha256(blob).hexdigest()},
        "files": entries,
    }
    ROUTES["/f/move.json"] = (200, json.dumps(manifest).encode())
    ROUTES["/f/firmware.pack"] = (200, blob)

    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP)
    _device_files(TMP)
    cwd = os.getcwd()
    os.chdir(TMP)
    try:
        with open(updater.VERSION_FILE, "w") as fh:
            fh.write("9.9.8\n")
        os.mkdir("lib")
        with open("lib/thing.py", "w") as fh:
            fh.write("VALUE = 1\n")
        applied = updater._update(Config(f"http://127.0.0.1:{PORT}/f/move.json"))
        with open(updater.PREV_DIR + "/lib/thing.py") as fh:
            saved = fh.read()
        with open("lib/thing.py") as fh:
            live = fh.read()
        next_gone = not os.path.exists(updater.NEXT_DIR)
    finally:
        os.chdir(cwd)
    ok = (
        applied is True
        and "VALUE = 1" in saved
        and "VALUE = 2" in live
        and next_gone
    )
    return _report(
        "the apply moves the old tree into :prev, not a copy",
        ok,
        f"saved={saved.strip()!r} live={live.strip()!r} next_gone={next_gone}",
    )


def case_interrupted_apply_is_rolled_back():
    # An apply that died between "move the old file into :prev" and "install the
    # staged file" leaves the live tree with a HOLE. version.txt and boot-ok.txt
    # still both name the old release, so judged the normal way it looks proven
    # and its rollback copy would be dropped - stranding the board on a tree
    # that is missing files. The applying marker is what makes _recover put the
    # old tree back instead.
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP)
    _device_files(TMP)
    cwd = os.getcwd()
    os.chdir(TMP)
    try:
        with open(updater.VERSION_FILE, "w") as fh:
            fh.write("9.9.8\n")
        with open(updater.BOOT_OK_FILE, "w") as fh:
            fh.write("9.9.8\n")
        old = b"OLD MAIN\n"
        os.mkdir(updater.PREV_DIR)
        with open(updater.PREV_DIR + "/main.py", "wb") as fh:
            fh.write(old)
        with open(updater.PREV_INFO, "w") as fh:
            json.dump(
                {
                    "version": "9.9.8",
                    "files": [
                        {"path": "main.py", "sha256": hashlib.sha256(old).hexdigest()}
                    ],
                    "managed": ["main.py"],
                },
                fh,
            )
        with open(updater.APPLYING_FILE, "w") as fh:
            fh.write("9.9.9\n")
        restored = updater._recover()
        has_main = os.path.exists("main.py")
        with open("main.py") as fh:
            body = fh.read()
        marker_gone = not os.path.exists(updater.APPLYING_FILE)
    finally:
        os.chdir(cwd)
    ok = restored is True and has_main and "OLD MAIN" in body and marker_gone
    return _report(
        "an interrupted apply is rolled back, not adopted",
        ok,
        f"restored={restored} main={has_main} marker_gone={marker_gone}",
    )


def case_update_sweeps_leftover_staging():
    # A failed attempt - or a rollback interrupted mid-flight - can leave the
    # pack and :next/ resident. On the board's 768 KB flash those leftovers
    # alone made every later update fail with OSError(28), ENOSPC (measured
    # 2026-09-27), so _update must sweep them before it spends any.
    files = {"main.py": b"print('new main')\n"}
    blob, entries = _pack(files)
    manifest = {
        "name": "galactic-unicorn",
        "version": "9.9.9",
        "pack": {"file": "firmware.pack", "sha256": hashlib.sha256(blob).hexdigest()},
        "files": entries,
    }
    ROUTES["/f/sweep.json"] = (200, json.dumps(manifest).encode())
    ROUTES["/f/firmware.pack"] = (200, blob)

    _device_files(TMP)
    cwd = os.getcwd()
    os.chdir(TMP)
    try:
        with open(updater.VERSION_FILE, "w") as fh:
            fh.write("9.9.8\n")
        # Leftovers a previous attempt would have left behind.
        with open(updater.PACK_PATH, "wb") as fh:
            fh.write(b"stale pack" * 100)
        os.mkdir(updater.NEXT_DIR)
        with open(updater.NEXT_DIR + "/stale.py", "w") as fh:
            fh.write("STALE = 1\n")
        applied = updater._update(Config(f"http://127.0.0.1:{PORT}/f/sweep.json"))
        stale_gone = not os.path.exists(updater.NEXT_DIR + "/stale.py")
        pack_gone = not os.path.exists(updater.PACK_PATH)
    finally:
        os.chdir(cwd)
    ok = applied is True and stale_gone and pack_gone
    return _report(
        "leftover pack and :next are swept before an update",
        ok,
        f"applied={applied} stale_gone={stale_gone} pack_gone={pack_gone}",
    )


def case_update_reclaims_rollback_when_staging_runs_out_of_space():
    # Measured on the board 2026-10-04: live tree 323 KB, pack 255 KB, and a
    # resident :prev left only ~44 KB free, so every stage failed OSError(28)
    # and the board sat on 0.1.48 for three days while the service served
    # 0.1.49. _update must reclaim the rollback slot (which _apply rebuilds)
    # and retry once, rather than dead-ending. The first stage raises as the
    # full filesystem did; the retry must find the slot gone and succeed.
    files = {"main.py": b"print('new main')\n"}
    blob, entries = _pack(files)
    manifest = {
        "name": "galactic-unicorn",
        "version": "9.9.9",
        "pack": {"file": "firmware.pack", "sha256": hashlib.sha256(blob).hexdigest()},
        "files": entries,
    }
    ROUTES["/f/space.json"] = (200, json.dumps(manifest).encode())
    ROUTES["/f/firmware.pack"] = (200, blob)

    _device_files(TMP)
    calls = {"n": 0}
    saw_slot = {"on_fail": None}
    real_stage = updater._stage

    def flaky(url, fs, sha, timeout_s, limit=updater.MAX_PACK_BYTES):
        calls["n"] += 1
        if calls["n"] == 1:
            saw_slot["on_fail"] = os.path.exists(updater.PREV_DIR)
            raise OSError(28)
        return real_stage(url, fs, sha, timeout_s, limit)

    updater._stage = flaky
    cwd = os.getcwd()
    os.chdir(TMP)
    try:
        with open(updater.VERSION_FILE, "w") as fh:
            fh.write("9.9.8\n")
        # No proven boot-ok for this version, or _drop_proven_rollback would
        # (correctly) drop the slot before we can stage against it.
        try:
            os.remove(updater.BOOT_OK_FILE)
        except OSError:
            pass
        # A resident rollback copy is what ate the free space.
        os.makedirs(updater.PREV_DIR, exist_ok=True)
        with open(updater.PREV_DIR + "/old.py", "w") as fh:
            fh.write("OLD = 1\n")
        applied = updater._update(Config(f"http://127.0.0.1:{PORT}/f/space.json"))
    finally:
        updater._stage = real_stage
        os.chdir(cwd)
    ok = applied is True and calls["n"] == 2 and saw_slot["on_fail"] is True
    return _report(
        "out of space: the rollback slot is reclaimed and the stage retried",
        ok,
        f"applied={applied} stages={calls['n']} slot_present_on_fail={saw_slot['on_fail']}",
    )


def case_update_is_a_noop_on_the_same_version():
    ROUTES["/f/same.json"] = (200, b'{"version":"9.9.9"}')
    _device_files(TMP)
    cwd = os.getcwd()
    os.chdir(TMP)
    try:
        with open(updater.VERSION_FILE, "w") as fh:
            fh.write("9.9.9\n")
        applied = updater._update(Config(f"http://127.0.0.1:{PORT}/f/same.json"))
    finally:
        os.chdir(cwd)
    return _report("the same version applies nothing", applied is False, "")


def _device_files(where):
    """Point the updater's device paths inside `where` (its flash root).

    RELATIVE names, and the caller chdirs into `where`: the updater addresses
    its flash root the way the board does ("version.txt", ":next/..."), and its
    `_mkdirs` builds a path by splitting on "/", so an absolute path would be
    taken apart into relative pieces. Reproducing that here keeps the test
    honest about the shape of the paths the board actually uses.
    """
    del where
    updater.PACK_PATH = "incoming.pack"
    updater.NEXT_DIR = ":next"
    updater.PREV_DIR = ":prev"
    updater.PREV_INFO = "prev.json"
    updater.APPLYING_FILE = "applying.txt"
    updater.VERSION_FILE = "version.txt"


def _rm_next():
    """Clear :next/ so a case starts from a clean staging slot."""
    shutil.rmtree(updater.NEXT_DIR, ignore_errors=True)


def main():
    global PORT, TMP
    server, PORT = _serve()
    TMP = tempfile.mkdtemp(prefix="updater-fetch-")
    try:
        results = [
            case_fetch_reads_the_manifest(),
            case_fetch_rejects_a_tls_url(),
            case_fetch_rejects_a_non_200(),
            case_stage_streams_into_next_and_verifies_every_file(),
            case_stage_rejects_a_bad_pack_sha256(),
            case_stage_rejects_a_truncated_pack(),
            case_stage_rejects_a_pack_missing_a_manifest_file(),
            case_update_applies_a_release(),
            case_apply_moves_the_old_tree_into_the_rollback_slot(),
            case_interrupted_apply_is_rolled_back(),
            case_update_sweeps_leftover_staging(),
            case_update_reclaims_rollback_when_staging_runs_out_of_space(),
            case_update_is_a_noop_on_the_same_version(),
        ]
    finally:
        server.shutdown()
        shutil.rmtree(TMP, ignore_errors=True)
    print()
    print(f"{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
