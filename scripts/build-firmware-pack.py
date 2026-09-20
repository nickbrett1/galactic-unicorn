#!/usr/bin/env python3
"""Build firmware.pack + manifest.json for the board's boot-time updater.

Design memo: memos/CvaQ2nMNqaTvQbgYc8HJqW.

The board has NO uzlib (probed on the real Pico W), so the payload is
uncompressed: a flat run of length-prefixed records, each

    [4-byte big-endian path length][path utf-8]
    [4-byte big-endian data length][data bytes]

manifest.json carries the pack's sha256 and a sha256 per file; the updater
verifies the pack, then re-hashes every file as it unpacks, so a truncated or
corrupt download can never reach the live tree.

EXCLUDED, on purpose: boot.py and lib/updater.py (the only code that can repair
everything else - it must not arrive over the channel it repairs), and
config_secrets.py (device-local, gitignored).

Usage: build-firmware-pack.py <version> <out_dir>
"""

import hashlib
import json
import os
import struct
import sys

NAME = "galactic-unicorn"
ROOT_FILES = ("main.py", "config.py", "routines.json")
LIB_DIR = "lib"
EXCLUDE = {"lib/updater.py"}


def collect_files(root="."):
    """The firmware files that ship, relative to the repo root."""
    files = [name for name in ROOT_FILES if os.path.exists(os.path.join(root, name))]
    lib = os.path.join(root, LIB_DIR)
    if os.path.isdir(lib):
        for name in sorted(os.listdir(lib)):
            if not name.endswith(".py"):
                continue
            rel = LIB_DIR + "/" + name
            if rel not in EXCLUDE:
                files.append(rel)
    return files


def build_pack(root, files):
    pack = bytearray()
    entries = []
    for rel in files:
        with open(os.path.join(root, rel), "rb") as fh:
            data = fh.read()
        path_bytes = rel.encode()
        pack += struct.pack(">I", len(path_bytes)) + path_bytes
        pack += struct.pack(">I", len(data)) + data
        entries.append({"path": rel, "sha256": hashlib.sha256(data).hexdigest()})
    return bytes(pack), entries


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        raise SystemExit(2)
    version, out_dir = sys.argv[1], sys.argv[2]

    files = collect_files()
    if not files:
        raise SystemExit("no firmware files found - wrong working directory?")

    pack, entries = build_pack(".", files)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "firmware.pack"), "wb") as fh:
        fh.write(pack)

    manifest = {
        "name": NAME,
        "version": version,
        "tag": "v" + version,
        "pack": {
            "file": "firmware.pack",
            "sha256": hashlib.sha256(pack).hexdigest(),
        },
        "files": entries,
    }
    with open(os.path.join(out_dir, "manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
        fh.write("\n")

    print(f"firmware.pack: {len(pack)} bytes, {len(entries)} files, version {version}")
    print("  " + ", ".join(entry["path"] for entry in entries))


if __name__ == "__main__":
    main()
