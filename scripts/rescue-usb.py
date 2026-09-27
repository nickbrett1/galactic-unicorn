#!/usr/bin/env python3
"""Rescue USB deploy: push the firmware over ONE serial connection.

Why this exists
---------------
`scripts/deploy.sh` runs one `mpremote` PROCESS per file, and every process
re-opens the port. On a board whose boot is unhappy that is fatal: the board
resets, USB re-enumerates, and `/dev/tty.usbmodem*` is simply ABSENT for
seconds at a time. Every re-open is a roll of those dice. That is exactly how
the last deploy died: 60 rounds of

    mpremote: failed to access /dev/tty.usbmodem83201
              (it may be in use by another program)

So this script opens the port ONCE and pushes every file through it. Nothing
runs until the deliberate soft reset at the end, so there is no window in which
a half-updated tree can execute.

It also replaces the transport, not just the connection count. The board could
never read its update manifest over HTTPS (it cannot complete a TLS handshake),
so `config.py` here points at the LAN service over plain HTTP -- landing that
plus `lib/net.py`, `lib/updater.py` and `main.py` is what makes a boot-time
update check finish at all.

THE `:` TRAP
------------
The mpremote CLI writes ":" for the device root (`fs cp x.py :`). That alias is
expanded by the CLI's own argument parsing, NOT by the transport. Driving the
transport directly, a destination of ":config.py" is a file whose NAME begins
with a colon. It looks like it worked -- mkdir succeeds, every write succeeds,
sizes are right -- and the board keeps running the old tree. So `plan()` below
returns plain device-relative paths, and the bogus entries an earlier run of a
"library" deploy created are swept up first.

WHY NOT mpremote's OWN TRANSPORT
--------------------------------
Because every read in it is unbounded (`serial.read(n)` on a port opened with
`timeout=None`). When the board resets mid-conversation, or floods the console
from a crash loop, that read never returns and the whole deploy hangs with no
output -- which is exactly what it did, twice. This drives the raw REPL by hand
instead, with `timeout=` on the port and a deadline on every read, and retries
the whole sequence on any error. Bounded and resumable beats clever.

Order (lib/ before the entry point, exactly as deploy.sh explains): a board
that receives a main.py importing a lib/ it does not have dies on ImportError
-- every 8 s, because an armed fuse outlives a reset.

Usage
-----
    ~/.venv-mpremote/bin/python scripts/rescue-usb.py [PORT] [--attempts N]
"""

import glob
import os
import subprocess
import sys
import time

import serial

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PORT = "/dev/cu.usbmodem83201"
FUSE_MS = 8000
CHUNK = 256

# Files an earlier run created by passing a ":"-prefixed destination through to
# a transport that does not expand it. Only these exact names, so the updater's
# own ":prev" and ":next" rollback slots are never touched.
STRAY_NAMES = (":boot.py", ":config.py", ":config_secrets.py", ":main.py", ":routines.json", ":lib")


# --------------------------------------------------------------------------- #
# A raw REPL client you cannot hang in.
# --------------------------------------------------------------------------- #


class Link:
    """One live serial connection, in the raw REPL."""

    def __init__(self, port):
        self.s = serial.Serial(port, 115200, timeout=0.05)

    def close(self):
        try:
            # Leave the raw REPL before letting go. A board parked there is
            # SILENT and inert -- Ctrl-C produces no prompt in raw mode, so a
            # later handshake can never resync, and with no fuse armed nothing
            # reboots it out of the state either. (This is not hypothetical: it
            # is what stranded the previous three runs.)
            self.s.write(b"\r\x02")
            time.sleep(0.1)
        except Exception:  # noqa: BLE001, S110 - the node may already be gone
            pass
        try:
            self.s.close()
        except Exception:  # noqa: BLE001, S110 - the node may already be gone
            pass


def drain(s, seconds, until=None):
    """Read for up to `seconds`, returning early once `until` appears.

    Every read has a deadline, so a reset mid-conversation ends the call with
    short data rather than blocking forever.
    """
    buf = b""
    end = time.time() + seconds
    while time.time() < end:
        try:
            chunk = s.read(4096)
        except Exception:  # noqa: BLE001 - node torn down mid-reset
            break
        if chunk:
            buf += chunk
            if until and until in buf:
                break
        else:
            time.sleep(0.01)
    return buf


def raw_exec(link, command, timeout=8.0):
    """Run one command in the raw REPL. Returns (stdout, stderr) bytes.

    The device answers `OK`, stdout, \\x04, stderr, \\x04, `>`. All of it
    usually arrives in ONE read, so the reply is SPLIT on the two markers
    rather than assuming stdout's \\x04 is the last byte of the first chunk --
    which is what made every command look like a timeout.
    """
    if isinstance(command, str):
        command = command.encode()
    s = link.s
    for i in range(0, len(command), CHUNK):
        s.write(command[i : i + CHUNK])
        time.sleep(0.005)
    s.write(b"\x04")

    # Reply = "OK" + stdout + \x04 + stderr + \x04 + ">". Wait for all three
    # parts rather than for a fixed delay: the trailing ">" is what tells us
    # the device is ready for the next command, and waiting a fixed 0.3s for
    # it made every round trip ~0.7s -- which is how a 32-chunk feed gap
    # quietly grew past the 8 s fuse and cut the push off mid-file.
    end = time.time() + timeout
    buf = b""
    while time.time() < end:
        buf += drain(s, 0.02)
        parts = buf.split(b"\x04")
        if len(parts) >= 3 and b">" in parts[2]:
            break
    parts = buf.split(b"\x04")
    if len(parts) < 3:
        raise OSError(f"timed out reading the reply: {buf[-80:]!r}")
    head = parts[0]
    while head.startswith(b">"):  # a prompt byte left over from the last reply
        head = head[1:]
    if not head.startswith(b"OK"):
        raise OSError(f"raw repl refused the command: {head[:80]!r}")
    if os.environ.get("RESCUE_DEBUG"):
        print(f"      dbg {command[:32]!r} -> out={head[2:][-60:]!r} err={parts[1][-60:]!r}")
    return head[2:], parts[1]


def feed(link):
    """Reload the fuse.

    A Ctrl-C into a running main.py leaves that program's watchdog armed and
    burning, and an armed fuse outlives the interrupt. Re-arming here -- rather
    than only once at the top -- means a slow file can never be cut off
    halfway through. On a board caught during boot.py there is no fuse to
    reload and this arms one; either way, feeding it between files keeps the
    connection alive for the whole push.
    """
    raw_exec(link, f"import machine; machine.WDT(timeout={FUSE_MS})")


# The exact GitHub release prefix the pre-fix config.py points at, and the LAN
# service that replaces it. Rewriting this ONE line on the device is what stops
# the boot loop: the hang is a TLS handshake in the boot-time update check, and
# the moment the URL is plain HTTP the check completes instead of wedging.
OLD_URL_PREFIX = "https://github.com/nickbrett1/galactic-unicorn/releases/latest/download/"
NEW_URL_PREFIX = "http://192.168.1.2:3009/firmware/"


def stabilize(link):
    """Point the ON-BOARD config.py at the LAN service, in one command.

    This runs before anything else because it is tiny and it is the difference
    between a board that resets every ten seconds and one that holds still: the
    full config.py is a twelve-kilobyte, fifty-round-trip affair, and a
    resetting board may not grant that much. One string replace does not need
    it to. Idempotent, so a retry is free.
    """
    out, err = raw_exec(
        link,
        "p='config.py'\n"
        "s=open(p).read()\n"
        f"n=s.replace({OLD_URL_PREFIX!r},{NEW_URL_PREFIX!r})\n"
        "open(p,'w').write(n)\n"
        "print('changed' if n != s else 'already')",
    )
    if err:
        raise OSError("stabilize: {}".format(err.decode(errors="replace")))
    print("stabilize: config.py on the device -> {}".format(out.decode(errors="replace").strip()))


def enter_raw(port, deadline_s):
    """Return a Link parked at the raw REPL, or raise.

    Retries because the board is resetting: the node is absent for seconds at a
    time, and Ctrl-C during boot.py only makes boot.py swallow it and carry on
    to main.py. So interrupt repeatedly until a prompt appears, then convert
    the friendly REPL into a raw one.

    Every pass starts with Ctrl-B as well as Ctrl-C. Ctrl-B is what leaves the
    raw REPL, and a board parked there answers Ctrl-C with silence -- so a
    handshake that only ever sends Ctrl-C can never recover from it. At the
    friendly REPL Ctrl-B is inert.
    """
    end = time.time() + deadline_s
    last = "no attempt"
    while time.time() < end:
        t0 = time.time()
        try:
            link = Link(port)
        except Exception as exc:  # noqa: BLE001 - node re-enumerating
            last = f"open: {exc!r}"
            time.sleep(0.5)
            continue
        try:
            buf = b""
            for _ in range(8):
                link.s.write(b"\r\x02\r\x03")
                buf += drain(link.s, 0.6, until=b">>>")
                if b">>>" in buf:
                    break
            if b">>>" not in buf:
                raise OSError(f"no prompt: {buf[-160:]!r}")
            print(f"rescue: friendly REPL after {time.time() - t0:.1f}s")
            link.s.write(b"\r\x01")
            got = drain(link.s, 4.0, until=b"raw REPL; CTRL-B to exit\r\n>")
            if b"raw REPL; CTRL-B to exit\r\n>" not in got:
                raise OSError(f"no raw repl: {got[-160:]!r}")
            print("rescue: raw REPL")
            raw_exec(link, "import machine, gc, os, time; gc.collect()")
            print("rescue: command channel up")
            feed(link)
            print("rescue: fuse fed")
            stabilize(link)
            return link
        except Exception as exc:  # noqa: BLE001 - retry the whole handshake
            print(f"rescue: handshake attempt failed: {exc!r}")
            last = f"{exc!r}"
            link.close()
            time.sleep(0.4)
    raise OSError(f"could not reach the raw REPL ({last})")


# --------------------------------------------------------------------------- #
# The deploy itself
# --------------------------------------------------------------------------- #


def plan(root):
    """[(local_path, device_path)] in push order: lib/ first, entry point last.

    Device paths are RELATIVE and bare -- see THE `:` TRAP above.
    """
    out = []
    for path in sorted(glob.glob(os.path.join(root, "lib", "*.py"))):
        out.append((path, os.path.relpath(path, root).replace(os.sep, "/")))
    for name in ("config.py", "config_secrets.py", "routines.json", "main.py", "boot.py"):
        path = os.path.join(root, name)
        if os.path.exists(path):
            out.append((path, name))
    return out


def sweep_strays(link):
    """Remove the bogus ":name" entries an earlier run left behind."""
    names = eval(
        raw_exec(link, f"print(repr([n for n in os.listdir() if n in {list(STRAY_NAMES)!r}]))")[0]
    )
    for name in names:
        raw_exec(
            link,
            "def _drop(p):\n"
            " import os\n"
            " try:\n"
            "  for e in os.listdir(p):\n"
            "   _drop(p + '/' + e)\n"
            "  os.rmdir(p)\n"
            " except OSError:\n"
            "  os.remove(p)\n"
            f"_drop({name!r})",
        )
        print(f"rescue: swept stray {name}")
    return names


def write_file(link, dest, data):
    feed(link)
    _out, err = raw_exec(link, f"f=open({dest!r},'wb')\nw=f.write")
    if err:
        raise OSError("open {}: {}".format(dest, err.decode(errors="replace")))
    for i in range(0, len(data), CHUNK):
        # Re-arm inside a big file too. The fuse is 8 s and a chunk is a
        # round trip, so a gap of 8 chunks is already ~2 s of margin.
        if i % (CHUNK * 8) == 0:
            feed(link)
        _, err = raw_exec(link, f"w({data[i : i + CHUNK]!r})")
        if err:
            raise OSError("write {}: {}".format(dest, err.decode(errors="replace")))
    _, err = raw_exec(link, "f.close()")
    if err:
        raise OSError("close {}: {}".format(dest, err.decode(errors="replace")))


def stamp_from_git(root):
    try:
        tag = subprocess.check_output(
            ["git", "describe", "--tags", "--abbrev=0"],
            cwd=root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return tag[1:] if tag.startswith("v") else tag


def push_once(port, files, version, deadline_s):
    link = enter_raw(port, deadline_s)
    try:
        print("rescue: at the raw REPL")
        sweep_strays(link)
        try:
            raw_exec(link, "os.mkdir('lib')")
        except OSError:
            pass  # lib/ usually exists already

        for path, dest in files:
            with open(path, "rb") as fh:
                data = fh.read()
            before = time.monotonic()
            write_file(link, dest, data)
            rel = os.path.relpath(path, ROOT)
            took = time.monotonic() - before
            print(f"rescue: {rel:<20} -> {dest:<20} {len(data):>6} B  {took:.2f}s")

        if version:
            feed(link)
            raw_exec(link, f"f = open('version.txt', 'w'); f.write({version + chr(10)!r}); f.close()")
            stamped = eval(raw_exec(link, "print(repr(open('version.txt').read().strip()))")[0])
            print(f"rescue: stamped version.txt = {stamped}")

        # Read the pushed config back OFF THE DEVICE, so "it landed" is a
        # measurement and not a hope.
        landed = eval(
            raw_exec(
                link,
                "print(repr([l for l in open('config.py').read().splitlines()"
                " if l.startswith('UPDATE_MANIFEST_URL')]))",
            )[0]
        )
        print(f"rescue: board config.py says {landed}")
        if not landed or "192.168.1.2:3009" not in landed[0]:
            raise OSError(f"config.py on the device is not the LAN one: {landed!r}")
        return link
    except Exception:
        link.close()
        raise


def parse_args(argv):
    port = DEFAULT_PORT
    attempts = 6
    i = 0
    while i < len(argv):
        if argv[i] == "--attempts":
            attempts = int(argv[i + 1])
            i += 2
        else:
            port = argv[i]
            i += 1
    return port, attempts


def main():
    port, attempts = parse_args(sys.argv[1:])

    files = plan(ROOT)
    if not files:
        raise SystemExit("rescue: nothing to push from " + ROOT)
    version = stamp_from_git(ROOT)
    print(f"rescue: pushing {len(files)} files, stamping {version or '(no tag)'}")

    link = None
    for attempt in range(1, attempts + 1):
        print(f"rescue: attempt {attempt}/{attempts} on {port}")
        try:
            link = push_once(port, files, version, deadline_s=120)
            break
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001 - the board reset; start over
            print(f"rescue: attempt {attempt} failed ({exc!r}), retrying")
            time.sleep(1.5)
    if link is None:
        raise SystemExit(f"rescue: gave up after {attempts} attempts")

    try:
        # Soft reset (not hard): it re-runs boot.py + main.py without a USB
        # re-enumeration, so this connection sees the board come back.
        link.s.write(b"\r\x02")  # ctrl-B: friendly REPL
        time.sleep(0.3)
        link.s.write(b"\r\x04")  # ctrl-D: soft reset
        out = drain(link.s, 30.0)
        print("rescue: board said after soft reset:")
        for line in out.decode(errors="replace").splitlines():
            if line.strip():
                print("    " + line)
    finally:
        link.close()
    print("rescue: done")


if __name__ == "__main__":
    main()
