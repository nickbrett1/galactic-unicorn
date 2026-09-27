#!/usr/bin/env python3
"""Host tests for the updater's fetch/verify/apply path. No board.

    python3 tests/test_updater_fetch.py

This is the seam that changed on 2026-09-26: the updater used to pull the
manifest and pack from GitHub over HTTPS, which this board cannot do (see
config.UPDATE_MANIFEST_URL), and now pulls them over plain HTTP from the LAN
service through `net.http_get` - a bounded raw socket. These tests drive
`_fetch`, `_download` and a whole `_update` against a real local HTTP server,
so the pack format, the sha256 checks and the "version.txt is written LAST"
rule are all exercised without hardware.

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


def case_download_verifies_the_sha256():
    body = b"PACKBYTES" * 1000
    ROUTES["/f/firmware.pack"] = (200, body)
    sha = hashlib.sha256(body).hexdigest()
    dest = os.path.join(TMP, "got.pack")
    size = updater._download(f"http://127.0.0.1:{PORT}/f/firmware.pack", dest, sha, 3)
    with open(dest, "rb") as fh:
        data = fh.read()
    ok = size == len(body) and data == body
    return _report("_download streams and verifies the pack", ok, f"{size} bytes")


def case_download_rejects_a_bad_sha256():
    ROUTES["/f/bad.pack"] = (200, b"not-the-pack")
    dest = os.path.join(TMP, "bad.pack")
    try:
        updater._download(f"http://127.0.0.1:{PORT}/f/bad.pack", dest, "0" * 64, 3)
        ok = False
    except ValueError as exc:
        ok = "mismatch" in str(exc)
    return _report("_download refuses a pack whose sha256 is wrong", ok, "ValueError")


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
    updater.VERSION_FILE = "version.txt"


def main():
    global PORT, TMP
    server, PORT = _serve()
    TMP = tempfile.mkdtemp(prefix="updater-fetch-")
    try:
        results = [
            case_fetch_reads_the_manifest(),
            case_fetch_rejects_a_tls_url(),
            case_fetch_rejects_a_non_200(),
            case_download_verifies_the_sha256(),
            case_download_rejects_a_bad_sha256(),
            case_update_applies_a_release(),
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
