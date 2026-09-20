#!/usr/bin/env bash
#
# Produces the files that get attached to the GitHub Release.
#
# genproj seeds this file once; after that it is yours. `scripts/` is app-owned,
# so regeneration never overwrites it — unlike .buildkite/pipeline.yml, which is
# genproj's and is rewritten on every regeneration.
#
# Contract: write the files to attach into $OUT_DIR (default: release/). The
# release step uploads every file it finds there and nothing else.
#
# Called as: bash scripts/release-artifacts.sh <version>
# The version is the tag without its `v` prefix, e.g. "1.2.4" for tag v1.2.4.
#
# WHAT THIS PROJECT PUBLISHES (the genproj default is replaced wholesale):
#
#   firmware.pack   the firmware, uncompressed, in the board's pack format
#   manifest.json   version + pack_sha256 + per-file sha256
#
# The board's updater fetches releases/latest/download/manifest.json on every
# boot and pulls firmware.pack only when the version changed. There is no
# tarball and no per-target asset: the Pico W has no uzlib, so a compressed
# payload cannot be inflated on the device, and the board is a single host, not
# a release matrix. Design memo: memos/CvaQ2nMNqaTvQbgYc8HJqW.
#
# The pack + manifest are built by scripts/build-firmware-pack.py (python3, the
# language this project's CI already runs). Asset names are a contract with the
# device: the updater fetches "manifest.json" and "firmware.pack" by exact
# string, so treat those names as frozen.
set -euo pipefail

VERSION="${1:?usage: release-artifacts.sh <version>}"
OUT_DIR="${OUT_DIR:-release}"

rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"

python3 scripts/build-firmware-pack.py "$VERSION" "$OUT_DIR"

if [ -z "$(ls -A "$OUT_DIR" 2>/dev/null)" ]; then
  echo "No payloads produced, so this release carries notes and no assets." >&2
  exit 1
fi

echo "release artifacts:"
ls -l "$OUT_DIR"
