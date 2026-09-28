"""The remote producer: poll the LAN service and emit the event it asks for.

Design memo: "Galactic Unicorn -- remote triggering (phase 2)", section 6;
wire contract: `specs/spec/api/device-protocols.md` in the sibling
`galactic-unicorn-remote` repo.

The remote is not a feature (memo section 3). It is a SECOND PRODUCER of the
four button events the panel already handles:

    buttons --+
              +--> lib/routine.py
    this ------+

So this module does no more than: ask the service what should be true, run the
pure decision in lib/reconcile.py, and - when that says `apply` - hand the
event to the engine, which turns it into the button press that would have
produced it. Every child-proofing rule therefore holds for free: a remote
"book time" while bathtime counts down is inert, cancel is silent, and a
phone-started countdown is indistinguishable from one started at the panel.

The poll obeys the four measured constraints in device-protocols.md section 8:

  1. A tight budget. `socket.settimeout` bounds DNS+connect+read, so a dead
     service cannot stall more than REMOTE_TIMEOUT_S - and the countdown is
     ticks_ms-based, so even that never touches the timer.
  2. NO wifi JOIN during COUNTDOWN or HANDOFF - but the poll continues. We
     never spend an unbounded join on a running timer; if the link is up we
     keep asking, and if it is down the poll resumes when the link returns.
  3. The update check is deferred out of COUNTDOWN (main.py owns that gate;
     this module exposes `busy()` so the gate is explicit).
  4. Heap before network: `gc.collect()` before the request, a HARD byte cap on
     the read, and the observed report built ONCE into a reused dict.

A failure here is logged with enough context to tell a HEAP failure from a
LINK failure (memo sections 6.2, 11.15): the line carries free heap, the link
state and the radio status, and a one-word cause.

Subset note: this module stays inside MicroPython 1.19.1's language subset
(roughly CPython 3.4). No f-strings, no walrus operator, no type annotations.
It imports nothing board-only at module level, so its pure helpers
(clamp_poll_ms, split_url, poll_path) run under the host pytest suite.
"""

import gc
import json
import os
import socket
import time

import reconcile

try:
    import wedge
except ImportError:  # pragma: no cover - the host suite may not have it
    wedge = None

# Mirror the board's own state names for the two the poller treats specially.
# Kept as literals (not an import of lib/routine.py) so this module stays
# importable on the host: lib/routine.py pulls in display/bigfont/icons.
STATE_COUNTDOWN = reconcile.STATE_COUNTDOWN
STATE_HANDOFF = reconcile.STATE_HANDOFF

# The wire action names (device-protocols.md section 1).
ACTION_START = reconcile.ACTION_START
ACTION_CANCEL = reconcile.ACTION_CANCEL
ACTION_NONE = reconcile.ACTION_NONE

# One read of the socket is at most this many bytes, so a chatty or wrong
# server cannot hand the board a body bigger than REMOTE_READ_CAP: the loop
# stops once the running total reaches the cap.
CHUNK = 256

# remote.log starts over past this size - the board has ~126 KB of heap and no
# rotation, and losing old lines beats an unbounded file.
LOG_LIMIT = 4096


# -- pure helpers (host-tested) ----------------------------------------------

def clamp_poll_ms(raw, floor_ms, ceiling_ms):
    """Clamp the server's `next_poll_ms` to the board's floors (memo 6.3.5).

    The server owns the cadence; the board only bounds it. A missing or
    unparseable value falls back to the ceiling - the idle interval - which is
    the safe end: never poll faster than asked, and never wedge at zero.
    """
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return ceiling_ms
    if value < floor_ms:
        return floor_ms
    if value > ceiling_ms:
        return ceiling_ms
    return value


def split_url(url):
    """Split "http://host:port" (or bare host) into (host, port). None on TLS.

    A literal IP is used so the poll does no DNS (config.REMOTE_SERVICE_URL);
    the scheme check keeps a mis-typed https from silently becoming a plain
    HTTP connection.
    """
    if not url:
        return None, None
    rest = url
    if rest.startswith("http://"):
        rest = rest[7:]
    elif rest.startswith("https://"):
        return None, None
    rest = rest.split("/", 1)[0]
    if not rest:
        return None, None
    if ":" in rest:
        host, _, port = rest.partition(":")
        try:
            return host, int(port)
        except ValueError:
            return None, None
    return rest, 80


def poll_path(report, token):
    """The GET path and query for one poll, built from the observed report.

    Only the parameters the contract names, and only `routine`/`remaining_s`
    when the report carries them (i.e. in COUNTDOWN/HANDOFF) - which is exactly
    what build_report split out. Values come from the board, not from input.
    """
    parts = [
        "token=" + str(token),
        "boot=" + str(report["boot"]),
        "fw=" + str(report["fw"]),
        "applied_gen=" + str(report["applied_gen"]),
        "state=" + str(report["state"]),
    ]
    if "routine" in report:
        parts.append("routine=" + str(report["routine"]))
    if "remaining_s" in report:
        parts.append("remaining_s=" + str(report["remaining_s"]))
    if "rssi" in report:
        parts.append("rssi=" + str(report["rssi"]))
    if "uptime_s" in report:
        parts.append("uptime_s=" + str(report["uptime_s"]))
    # The board's own wedge tally (lib/wedge.py). An extra query parameter the
    # service may ignore; it is carried so the service's timeline of a quiet
    # window is joined by the board's account of the same window.
    if "wedge" in report:
        parts.append("wedge=" + str(report["wedge"]))
    return "/device/poll?" + "&".join(parts)


def should_cycle_radio(consecutive_link_failures, threshold):
    """True when a run of link-up failures has earned a radio cycle.

    Pure, so the policy is pinned on the host (tests/test_remote.py) while the
    act it authorises is not. A threshold of 0 disables the recovery rather
    than firing it immediately: "off" has to be expressible, and 0 is how the
    rest of config spells it.
    """
    if not threshold or threshold <= 0:
        return False
    return consecutive_link_failures >= threshold


def recovery_is_available(failed_cycles, max_cycles, since_last_ms, cooldown_ms):
    """False once the radio cycle has been SPENT: too many, or too soon.

    The cycle is the only thing on the board that can bring a deaf CYW43 back
    (lib/net.py:radio_reset), which is exactly why it must not run in a tight
    forever-loop. It takes the interface down to do its job, so a cycle every
    few seconds is a radio that never gets a quiet second - and if the deaf
    state is one time clears, the recovery becomes what prevents it.

    Measured 2026-09-28, fw=0.1.39: 22 of 23 cycles failed across a single
    wedge, one every ~8 s, and no poll ever recovered. So this bounds both the
    count and the rate. `max_cycles` is consecutive FAILED cycles - one poll
    that succeeds clears the run, so the next wedge gets a full allowance.
    `since_last_ms` is None when no cycle has run yet this session.

    Either bound at 0 disables it, matching how the rest of config spells
    "off": disabling must not be the most aggressive setting.
    """
    if max_cycles and max_cycles > 0 and failed_cycles >= max_cycles:
        return False
    if (
        cooldown_ms
        and cooldown_ms > 0
        and since_last_ms is not None
        and since_last_ms < cooldown_ms
    ):
        return False
    return True


# Below this much free memory, an ENOMEM is taken to be a heap shortfall.
# Above it, the heap has room and the refusal came from somewhere else. 32 KB
# is comfortable against the board's ~90-100 KB idle free and the ~25 KB a TLS
# handshake would want, so it separates "no room" from "room, but not here".
HEAP_ENOMEM_FLOOR = 32 * 1024


def classify_failure(exc, free, link):
    """Which window was this: the heap or the link? (memo sections 6.2, 11.15).

    A heap shortfall surfaces as MemoryError, or - when the allocation happens
    in C, inside the socket - as OSError(12) (ENOMEM). Both are the heap, and
    telling them from a link failure is the whole reason the log line exists.

    ENOMEM is read from `errno` OR from `args[0]`: CPython leaves `.errno`
    None for `OSError(12)` (it is in args), and the board's spelling has varied
    between MicroPython versions - so both are checked rather than one assumed.

    ENOMEM does **not** always mean the heap, though, which is why `free` is a
    parameter. The board's measured HTTPS failure (config.py, 2026-09-26)
    raised OSError(12) with the heap demonstrably healthy - a 64 KB contiguous
    block allocated fine - so the refusal was mbedTLS/lwIP, not the allocator,
    and calling it "heap" sent the search into the wrong window. `free` is the
    only evidence the line has, so it is used: ENOMEM with room to spare is not
    a heap verdict, and falls through to the link/other checks. `free` is None
    when gc.mem_free() itself raised, and then the old verdict stands.
    """
    if isinstance(exc, MemoryError):
        return "heap"
    if isinstance(exc, OSError):
        errno = getattr(exc, "errno", None)
        if errno is None and exc.args:
            errno = exc.args[0]
        if errno == 12 and (free is None or free < HEAP_ENOMEM_FLOOR):
            return "heap"
    if link is False:
        return "link"
    if exc is None:
        return "link"
    return "other"


# -- small board helpers -----------------------------------------------------

def _hex(data):
    try:
        import ubinascii

        return ubinascii.hexlify(data).decode()
    except ImportError:  # pragma: no cover - host-side only
        import binascii

        return binascii.hexlify(data).decode()


def _new_boot_id():
    """A fresh, short boot id for this session (device-protocols.md section 3).

    Random so it CHANGES on every boot: the server clears pending desired the
    moment it sees a new one (section 3.2), which is what stops a reboot from
    immediately re-running the last command.
    """
    try:
        return _hex(os.urandom(4))
    except Exception:  # noqa: BLE001 - no urandom is still a usable (if fixed) id
        return "boot0000"


def _read_gen(path):
    try:
        with open(path) as fh:
            return int(fh.read().strip())
    except Exception:  # noqa: BLE001 - absent/unreadable both mean "none yet"
        return 0


def _write_gen(path, gen):
    """Persist the applied_gen high-water mark. A few writes a day (section 3)."""
    try:
        with open(path, "w") as fh:
            fh.write(str(gen) + "\n")
    except Exception:  # noqa: BLE001, S110 - a lost write costs a re-apply, not correctness
        pass


def _file_log(path, line):
    """Append one line to a bounded log file. Never raises."""
    try:
        try:
            size = os.stat(path)[6]
        except OSError:
            size = 0
        if size > LOG_LIMIT:
            os.remove(path)
        with open(path, "a") as fh:
            fh.write(line + "\n")
    except Exception:  # noqa: BLE001, S110 - logging must never be fatal
        pass


def _default_feed():
    try:
        from watchdog import feed

        feed()
    except Exception:  # noqa: BLE001, S110 - no watchdog is a degradation
        pass


def _rssi(wlan):
    """The station's dBm, or None. The contract wants a non-positive integer."""
    if wlan is None:
        return None
    try:
        value = int(wlan.status("rssi"))
    except Exception:  # noqa: BLE001 - not every port answers 'rssi'
        return None
    if value > 0:  # server validates rssi <= 0; drop a nonsensical value
        return None
    return value


def _http_get(host, port, path, timeout_s, read_cap, feed, log=None):
    """One plain-HTTP GET, bounded at every step. Returns the raw bytes.

    Plain HTTP on the LAN (the service terminates no TLS): the board sends a
    query-param GET and reads a few hundred bytes back. The socket timeout
    bounds DNS, connect and read; the read loop stops at `read_cap`, so neither
    a dead service nor a hostile one can stall or flood the board.
    """
    feed()
    addr = socket.getaddrinfo(host, port)[0][-1]
    sock = socket.socket()
    try:
        sock.settimeout(timeout_s)
        sock.connect(addr)
        request = (
            "GET "
            + path
            + " HTTP/1.0\r\nHost: "
            + host
            + "\r\nConnection: close\r\n\r\n"
        )
        sock.send(request.encode())
        feed()
        chunks = []
        total = 0
        while total < read_cap:
            feed()
            chunk = sock.read(min(CHUNK, read_cap - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        return b"".join(chunks)
    finally:
        sock.close()


def split_response(raw):
    """(status_code, body_bytes) from a raw HTTP/1.x response."""
    idx = raw.find(b"\r\n\r\n")
    if idx < 0:
        return 0, b""
    head = raw[:idx]
    body = raw[idx + 4:]
    line_end = head.find(b"\r\n")
    if line_end < 0:
        line_end = len(head)
    fields = head[:line_end].decode().split(" ")
    if len(fields) < 2:
        return 0, body
    try:
        return int(fields[1]), body
    except ValueError:
        return 0, body


# -- the poller --------------------------------------------------------------

class Remote:
    """Polls the service and feeds the engine. One instance, owned by main.py.

    It is deliberately passive: `poll_if_due(now)` is called once per render
    loop, does nothing until the cadence is up, and then does at most one
    poll. It never sleeps, never runs its own loop, and never touches the
    display - so the countdown it might be starting is never blocked by it.
    """

    def __init__(self, engine, config, log, fw="dev", feed=None):
        self.engine = engine
        self.config = config
        self.log = log
        self.fw = fw
        self._feed = feed or _default_feed

        self.enabled = bool(getattr(config, "REMOTE_ENABLED", False))
        self.url = getattr(config, "REMOTE_SERVICE_URL", "") or ""
        self.token = getattr(config, "REMOTE_DEVICE_TOKEN", None)
        self.host, self.port = split_url(self.url)
        self.floor_ms = int(getattr(config, "REMOTE_POLL_MIN_MS", 1000))
        self.ceiling_ms = int(getattr(config, "REMOTE_POLL_MAX_MS", 10000))
        self.timeout_s = float(getattr(config, "REMOTE_TIMEOUT_S", 1.5))
        self.read_cap = int(getattr(config, "REMOTE_READ_CAP", 1024))
        self.gen_file = getattr(config, "REMOTE_APPLIED_GEN_FILE", "applied_gen.txt")
        self.log_file = getattr(config, "REMOTE_LOG_FILE", "remote.log")
        self.join_attempts = int(getattr(config, "REMOTE_WIFI_ATTEMPTS", 1))
        self.join_attempt_ms = int(getattr(config, "REMOTE_WIFI_ATTEMPT_MS", 10000))

        self.boot = _new_boot_id()
        self.applied_gen = _read_gen(self.gen_file)

        # Allocated ONCE and refilled in place every poll (memo 6.3.4): the
        # observed report must not allocate in the reporting path.
        self.report = {}
        # The decide() input, also reused. boot/last_boot are constant for the
        # session, so the boot-id rule is a no-op on the board (the SERVER
        # clears pending desired; device-protocols.md section 3.2).
        self.panel = {
            "boot": self.boot,
            "last_boot": self.boot,
            "state": reconcile.STATE_AMBIENT,
            "routine": None,
        }

        self._wlan = None
        self._started = time.ticks_ms()
        self.next_poll_at = 0
        # One link warning per outage: a countdown that cannot join must not
        # write a line every second. Re-armed when the link comes back.
        self._link_warned = False
        self._disabled_reason = self._disabled()
        # How many polls have failed in a row while the radio claimed to be up
        # (see should_cycle_radio and net.radio_reset).
        self.radio_reset_after = int(getattr(config, "RADIO_RESET_AFTER", 3))
        self._fails_since_ok = 0
        # How far the recovery itself may go. A cycle takes the interface down,
        # so an unthrottled one is a radio that never gets quiet (see
        # recovery_is_available). Counted in FAILED cycles, cleared by a poll.
        self.radio_reset_max = int(getattr(config, "RADIO_RESET_MAX", 3))
        self.radio_reset_cooldown_ms = int(
            getattr(config, "RADIO_RESET_COOLDOWN_MS", 120000)
        )
        self._failed_cycles = 0
        self._last_cycle_at = None
        # One line per outage when the allowance runs out, so a board that has
        # stopped trying does not look like a board that is still recovering.
        self._spent_warned = False
        # A cycle the poller has ASKED for but not performed: the render loop
        # owns the doing (see _cycle_radio and main.py).
        self.cycle_pending = False
        # What the service cannot see: the board's own accounting of the wedge
        # (lib/wedge.py). Counters in RAM for this boot, and a bounded flash
        # file that survives the WDT reset a wedge tends to end in. None when
        # the module is absent (host-only), so every use is guarded.
        self.journal = wedge.Journal(self.boot, log=log) if wedge else None

    # -- lifecycle -------------------------------------------------------

    def _disabled(self):
        if not self.enabled:
            return "REMOTE_ENABLED is False"
        if not self.host:
            return "no usable REMOTE_SERVICE_URL"
        if not self.token:
            return "no device token in config_secrets"
        if not getattr(self.config, "WIFI_SSID", None):
            return "no WiFi credentials"
        return None

    def describe(self):
        if self._disabled_reason:
            return "disabled (" + self._disabled_reason + ")"
        return (
            "polling "
            + self.url
            + " boot="
            + self.boot
            + " applied_gen="
            + str(self.applied_gen)
        )

    def busy(self):
        """True while a state the update check must keep out of is active.
        COUNTDOWN and HANDOFF (device-protocols.md section 8.3). main.py reads
        this to defer the OTA update check; it is the same info as the engine's
        own state, surfaced here so the deferral is explicit.
        """
        return self.engine.state in (STATE_COUNTDOWN, STATE_HANDOFF)

    def _net_log(self, message, exc=None):
        """net.join_wifi's log hook, folded into this module's log line.

        Written to remote.log as well as the REPL. The cycle's evidence -
        "interface is down", "back up and answering", "did not take effect" -
        is the whole point of the recovery, and until this change it went to
        the REPL only: a hard reset (or the next USB attach) took it with it,
        so a recovery could not be told from a board that merely rebooted.
        remote.log survives a reset, so the proof does too.
        """
        line = "remote: " + str(message)
        if exc is not None:
            line = line + ": " + repr(exc)
        self.log(line)
        _file_log(self.log_file, line)

    def _radio(self):
        if self._wlan is None:
            try:
                import network

                self._wlan = network.WLAN(network.STA_IF)
            except Exception:  # noqa: BLE001 - no radio: every poll will defer
                return None
        return self._wlan

    # -- the loop entry point --------------------------------------------

    def poll_if_due(self, now):
        """Do at most one poll, if the cadence says it is time. Never raises."""
        if self._disabled_reason is not None:
            return
        if time.ticks_diff(now, self.next_poll_at) < 0:
            return
        try:
            self._poll(now)
        except Exception as exc:  # noqa: BLE001 - a poll must not kill the loop
            self._log_failure(exc, "poll", now)
            self._note_failure()
            self._schedule(now, self.floor_ms)
            return
        if self._fails_since_ok:
            # A silent success is invisible in the log, and "the poll stopped
            # failing" is exactly the evidence a recovery needs. One line per
            # outage, written where the failures were.
            self._net_log(
                "poll recovered after "
                + str(self._fails_since_ok)
                + " consecutive failures"
            )
            if self.journal is not None:
                self.journal.note_recovery(self._fails_since_ok)
        self._fails_since_ok = 0
        # The link is good again: the recovery's allowance is restored, so the
        # next wedge starts with a full one rather than inheriting this one.
        self._failed_cycles = 0
        self._spent_warned = False

    def _note_failure(self):
        """Count it, and cycle the radio once the run is long enough.

        Only failures with the link UP count. A link that is honestly down is
        `join_wifi`'s problem and it already retries; the state this exists for
        is the one where nothing looks wrong and nothing gets through
        (lib/net.py:radio_reset).
        """
        wlan = self._radio()
        try:
            link = wlan is not None and wlan.isconnected()
        except Exception:  # noqa: BLE001 - an unreadable radio is not an up one
            link = False
        if not link:
            self._fails_since_ok = 0
            return
        self._fails_since_ok += 1
        if not should_cycle_radio(self._fails_since_ok, self.radio_reset_after):
            return
        if self._recovery_available():
            self._cycle_radio()
            return
        if not self._spent_warned:
            self._spent_warned = True
            self._net_log(
                "the radio cycle has been spent ("
                + str(self._failed_cycles)
                + " failed"
                + (", within the cool-down" if self._failed_cycles == 0 else "")
                + ") - polling on without it"
            )

    def _recovery_available(self):
        """Is a cycle still allowed? (policy in recovery_is_available)."""
        since = None
        if self._last_cycle_at is not None:
            since = time.ticks_diff(time.ticks_ms(), self._last_cycle_at)
        return recovery_is_available(
            self._failed_cycles,
            self.radio_reset_max,
            since,
            self.radio_reset_cooldown_ms,
        )

    def _cycle_radio(self):
        """Ask for a radio cycle. The render loop performs it, not the poll.

        net.radio_reset tears the CYW43 driver down and brings it back up. This
        runs immediately before engine.tick(), i.e. possibly while the matrix's
        PIO/DMA is still writing a frame - and a teardown during pixel streaming
        was measured to do nothing at all (isconnected() never went False, the
        radio stayed deaf). So the poll only raises the request; main.py calls
        take_cycle_request() at the top of a frame, where the display is idle,
        and runs cycle_radio() there.
        """
        self._fails_since_ok = 0
        self._last_cycle_at = time.ticks_ms()
        self.cycle_pending = True
        if self.journal is not None:
            self.journal.note_cycle(None)
        self._net_log(
            str(self.radio_reset_after)
            + " polls failed with the link up - the radio needs a cycle"
        )

    def take_cycle_request(self):
        """True once per pending request: the render loop's cue to cycle."""
        if not self.cycle_pending:
            return False
        self.cycle_pending = False
        return True

    def cycle_radio(self):
        """Perform a deferred cycle. Called by the render loop, never by poll."""
        try:
            import net

            recovered = net.radio_reset(self.config, log=self._net_log, feed=self._feed)
        except Exception as exc:  # noqa: BLE001 - recovery must not kill the loop
            self._log_failure(exc, "radio cycle", None)
            self._failed_cycles += 1
            return False
        self._net_log(
            "radio cycle recovered the radio" if recovered
            else "radio cycle did not recover the radio"
        )
        # A recovery is SPENT by failing: the next one is delayed by the
        # cool-down and the run is bounded by radio_reset_max, so a wedge that
        # cycles cannot fix does not become a radio that is always down.
        self._failed_cycles = 0 if recovered else self._failed_cycles + 1
        if self.journal is not None:
            self.journal.note_cycle(recovered)
        return recovered

    def _schedule(self, now, ms):
        self.next_poll_at = time.ticks_add(now, ms)

    def _poll(self, now):
        wlan = self._radio()
        link = wlan is not None and wlan.isconnected()
        if link:
            self._link_warned = False

        if not link:
            if self.busy():
                # NO join during COUNTDOWN/HANDOFF (section 8.2). The poll keeps
                # being scheduled, so remote Cancel is still available the
                # moment the link returns - but we never spend an unbounded
                # join on a running timer. Relaxed to the idle ceiling: while
                # the link is down there is nothing to ask.
                if not self._link_warned:
                    self._log_failure(
                        None,
                        "no link in " + str(self.engine.state) + " (join deferred)",
                        now,
                    )
                    self._link_warned = True
                self._schedule(now, self.ceiling_ms)
                return
            import net

            if not net.join_wifi(
                self.config,
                self.join_attempts,
                self.join_attempt_ms,
                log=self._net_log,
                feed=self._feed,
            ):
                if not self._link_warned:
                    self._log_failure(None, "wifi join failed", now)
                    self._link_warned = True
                self._schedule(now, self.ceiling_ms)
                return
            link = wlan.isconnected()

        report = self._build_report(wlan, now)
        path = poll_path(report, self.token)

        # Heap before network (memo 6.3.4): the handshake and the read allocate
        # in C; a fragmented heap refuses them as OSError(12), not MemoryError.
        gc.collect()
        self._feed()
        raw = _http_get(self.host, self.port, path, self.timeout_s, self.read_cap, self._feed)

        code, body = split_response(raw)
        if code != 200:
            # Name what the service rejected. The body is tiny JSON that says
            # which parameter was bad, and this line is logged AND file-logged on
            # every failure - but it is bound to 80 bytes, because a 4xx from
            # anywhere else could be something else entirely.
            detail = body[:80]
            try:
                detail = detail.decode()
            except Exception:  # noqa: BLE001 - undecodable bytes are still worth showing
                detail = repr(detail)
            raise OSError("http " + str(code) + ": " + detail)
        desired = json.loads(body.decode())

        self._apply(desired, report, now)

    def _build_report(self, wlan, now):
        """Fill the reused report dict from ONE snapshot (no per-poll alloc)."""
        state = self.engine.state
        # The wire vocabulary has no OFF: a dark, idle panel reports ambient.
        if state not in reconcile.PANEL_STATES:
            state = reconcile.STATE_AMBIENT
        routine_id = None
        remaining_s = None
        if state in (STATE_COUNTDOWN, STATE_HANDOFF):
            routine_id = self.engine.routine_id()
            remaining_s = self.engine.remaining_s(now)
        # A negative uptime_s is possible on the first poll: main.py captures
        # `now` before this Remote is constructed (which sets _started), so the
        # diff is a few ms negative and floor-division makes it -1. The service
        # rightly rejects that (observed 2026-09-27: the first poll of every
        # boot got OSError(http 422, detail=uptime_s)), so clamp it to 0.
        uptime_s = max(time.ticks_diff(now, self._started) // 1000, 0)
        return reconcile.build_report(
            self.report,
            self.boot,
            self.fw,
            self.applied_gen,
            state,
            routine_id,
            remaining_s,
            _rssi(wlan),
            uptime_s,
            wedge=(self.journal.summary() if self.journal is not None else None),
        )

    def _apply(self, desired, report, now):
        self.panel["state"] = report["state"]
        self.panel["routine"] = report.get("routine")
        # No wall clock on the board (section 3): `now_epoch_s` is only for the
        # defensive TTL, and the server never sends expires_at, so 0 is honest.
        kind, detail = reconcile.decide(desired, self.applied_gen, self.panel, 0)

        gen = desired.get("gen")
        if kind == reconcile.DECISION_APPLY:
            # Emit the SAME event the button would; the engine turns it into
            # the button press. This is the whole of the remote's vocabulary.
            self.engine.post_event(detail)
            if gen is not None and gen > self.applied_gen:
                self.applied_gen = gen
                _write_gen(self.gen_file, gen)
            self.log("remote: applied " + str(detail) + " gen=" + str(gen))
        elif kind == reconcile.DECISION_IGNORE:
            self.log("remote: ignored desired (" + str(detail) + ")")

        self._schedule(now, clamp_poll_ms(desired.get("next_poll_ms"), self.floor_ms, self.ceiling_ms))

    def _log_failure(self, exc, where, now=None):
        """One line with heap AND link context, so the two cannot be confused."""
        wlan = self._wlan
        link = None
        status = None
        if wlan is not None:
            try:
                link = wlan.isconnected()
                status = wlan.status()
            except Exception:  # noqa: BLE001, S110 - the log must not raise
                pass
        # The heap reading is context, not a guarantee: this whole method exists
        # so that a failure to REPORT a failure cannot be what takes the loop
        # down. So read it defensively, exactly like the radio fields above.
        # (It also keeps the class importable/runnable on the host, where gc has
        # no mem_free - the module's pure helpers are what pytest can pin.)
        try:
            free = gc.mem_free()
        except Exception:  # noqa: BLE001 - logging must never be fatal
            free = None
        cause = classify_failure(exc, free, link)
        if self.journal is not None:
            # The board's own tally, for the next successful poll to carry back
            # (lib/wedge.py): the service sees the quiet, this is the why.
            self.journal.note_failure(
                cause,
                where,
                exc,
                free,
                link,
                status=status,
                state=self.engine.state,
            )
        line = (
            "remote: "
            + where
            + " failed cause="
            + cause
            + " exc="
            + repr(exc)
            + " heap_free="
            + str(free)
            + " link="
            + str(link)
            + " status="
            + str(status)
            + " state="
            + str(self.engine.state)
            + " gen="
            + str(self.applied_gen)
        )
        self.log(line)
        _file_log(self.log_file, line)
