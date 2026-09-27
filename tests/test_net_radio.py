#!/usr/bin/env python3
"""Host tests for the radio recovery: net.radio_reset and its two proofs.

    python3 tests/test_net_radio.py

Why this file exists. The first version of the cycle (`active(False)`;
`active(True)`) was deployed to the board on 2026-09-27 and did NOTHING, while
reporting success. From inside the render loop `isconnected()` never went
False, the driver never restarted, and the radio stayed deaf - the log said
"radio: back up" and the very next poll died OSError(110) again. A recovery
that cannot tell "I cycled it" from "I called two functions" is worse than no
recovery, because it hides the outage. That is exactly the shape of the NTP
bug (`ntptime.settime()` with no timeout to compare against): an operation
with no observable result.

So the tests here pin the three things a no-op cannot fake:

  * `wait_for_link_down` - the interface has to be OBSERVED to let go. An
    interface that stays connected costs a bounded wait and returns False.
  * `tcp_probe` - a real TCP handshake against a real listening socket, and a
    bounded failure against a port with nothing on it. An IP is not evidence:
    the wedge has isconnected() True and a valid lease.
  * `radio_reset` end to end, against a fake `network` module, for BOTH
    outcomes: a wlan that drops and comes back (True, "answering") and a wlan
    that never drops (False, "did not take effect").

Nothing here touches the board or the LAN: the wlan is a fake and the TCP
server is on 127.0.0.1.
"""

import os
import socket
import sys
import threading
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.path.insert(0, ROOT)

# MicroPython's time helpers, so the module under test can run on the host.
# MUST precede `import net`.
if not hasattr(time, "ticks_ms"):
    time.ticks_ms = lambda: int(time.monotonic() * 1000)
    time.ticks_diff = lambda a, b: a - b
    time.sleep_ms = lambda ms: time.sleep(ms / 1000.0)

# The board's machine module, on the host. MUST precede `import watchdog`.
machine = types.ModuleType("machine")
machine.WDT = lambda timeout=None: None
machine.WDT_RESET = 3
machine.reset_cause = lambda: 0
sys.modules["machine"] = machine

import config  # after the shims above
import net  # after the shims above
import watchdog  # after the fake `machine` above


def _report(label, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL':<4} {label:<52} {detail}")
    return ok


# --- fakes ------------------------------------------------------------------

class FakeWlan:
    """A station whose `isconnected()` tells the truth about `active()`.

    `honest` True is a healthy driver: taking the interface down really does
    drop the association, which is what the REPL measurement showed the real
    one does when nothing is streaming pixels. `honest` False is the wedge:
    every call returns True and the association never goes anywhere.
    """

    def __init__(self, honest=True):
        self.honest = honest
        self.active_flag = True
        self._connected = True
        self.disconnects = 0
        self.connects = 0
        self.ifconfig_size = None
        self.ipconfig_calls = []

    def isconnected(self):
        if self.honest:
            return self.active_flag
        return True

    def status(self):
        return 3

    def active(self, flag=None):
        if flag is None:
            return self.active_flag
        self.active_flag = bool(flag)
        self._connected = bool(flag)

    def disconnect(self):
        self.disconnects += 1
        if self.honest:
            self._connected = False

    def connect(self, ssid, password=None):
        self.connects += 1
        if self.honest:
            self._connected = True

    def ifconfig(self, *_args):
        return ("0.0.0.0", "255.255.255.0", "0.0.0.0", "0.0.0.0")

    def ipconfig(self, **kwargs):
        self.ipconfig_calls.append(kwargs)


class RecordingWlan(FakeWlan):
    """A wlan that logs the order of the calls a cycle makes."""

    def __init__(self, honest=True):
        super().__init__(honest)
        self.calls = []

    def active(self, flag=None):
        if flag is not None:
            self.calls.append("active(" + str(flag) + ")")
        super().active(flag)

    def disconnect(self):
        self.calls.append("disconnect")
        super().disconnect()

    def connect(self, ssid, password=None):
        self.calls.append("connect")
        super().connect(ssid, password)


class FakeConfig:
    """Just the attributes lib/net.py reads on the recovery path."""

    WIFI_SSID = "testnet"
    WIFI_PASSWORD = "secret"
    STATIC_IP = None
    RADIO_SETTLE_MS = 0
    RADIO_DOWN_MS = 200
    RADIO_JOIN_MS = 200
    RADIO_PROBE_MS = 500
    REMOTE_SERVICE_URL = None
    UPDATE_MANIFEST_URL = None


def _install_fake_network(wlan):
    """Put a `network` module in sys.modules whose STA_IF is this wlan."""
    mod = types.ModuleType("network")
    mod.STA_IF = 0
    mod.AP_IF = 1
    mod.WLAN = lambda _iface: wlan
    sys.modules["network"] = mod
    return mod


class _TCPServer:
    """A real listening socket on 127.0.0.1, so tcp_probe has someone to reach."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self._stop = False
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while not self._stop:
            try:
                conn, _addr = self.sock.accept()
            except OSError:
                return
            conn.close()

    def freelocal_port(self):
        """A port on 127.0.0.1 with nothing listening - the refusal case."""
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        return port

    def close(self):
        self._stop = True
        try:
            self.sock.close()
        except OSError:
            pass


# --- wait_for_link_down: the interface must be SEEN to let go ---------------

def case_link_down_is_observed_immediately():
    wlan = FakeWlan(honest=True)
    wlan.active(False)
    ok = net.wait_for_link_down(wlan, 1000) is True
    return _report("a dropped association returns True at once", ok)


def case_link_that_never_drops_times_out():
    """The wedge: isconnected() stays True, so the wait must give up bounded."""
    wlan = FakeWlan(honest=False)
    started = time.monotonic()
    result = net.wait_for_link_down(wlan, 200)
    took = time.monotonic() - started
    ok = result is False and 0.15 <= took < 2.0
    return _report("a link that stays up returns False, bounded", ok, f"{took:.2f}s")


def case_link_down_waits_out_the_budget_not_longer():
    """A slow drop is waited for, but only up to the budget."""
    class SlowWlan(FakeWlan):
        def __init__(self):
            super().__init__(honest=False)
            self.reads = 0

        def isconnected(self):
            self.reads += 1
            return self.reads < 3  # drops on the third look

    wlan = SlowWlan()
    started = time.monotonic()
    ok = net.wait_for_link_down(wlan, 200) is True and (time.monotonic() - started) < 1.0
    return _report("a slow drop is waited for", ok)


def case_link_down_treats_an_unreadable_radio_as_down():
    class UnreadableWlan(FakeWlan):
        def isconnected(self):
            raise OSError(110)

    ok = net.wait_for_link_down(UnreadableWlan(), 200) is True
    return _report("an unreadable radio counts as down", ok)


def case_link_down_feeds_the_fuse():
    wlan = FakeWlan(honest=False)
    feeds = []
    net.wait_for_link_down(wlan, 200, feed=lambda: feeds.append(1))
    return _report("the wait feeds the fuse while it blocks", len(feeds) >= 1, f"{len(feeds)} feeds")


# --- tcp_probe: a real handshake, and a bounded refusal ---------------------

def case_tcp_probe_completes_against_a_listener():
    server = _TCPServer()
    try:
        ok = net.tcp_probe("127.0.0.1", server.port, 2) is True
    finally:
        server.close()
    return _report("tcp_probe is True when a handshake completes", ok)


def case_tcp_probe_is_false_and_bounded_with_nothing_listening():
    server = _TCPServer()
    try:
        port = server.freelocal_port()
        started = time.monotonic()
        result = net.tcp_probe("127.0.0.1", port, 2)
        took = time.monotonic() - started
    finally:
        server.close()
    ok = result is False and took < 2.0
    return _report("a refused connection is a bounded False", ok, f"{took:.2f}s")


def case_tcp_probe_feeds_before_it_blocks():
    server = _TCPServer()
    feeds = []
    try:
        net.tcp_probe("127.0.0.1", server.port, 2, feed=lambda: feeds.append(1))
    finally:
        server.close()
    return _report("tcp_probe feeds the fuse before the socket call", len(feeds) >= 1)


# --- the endpoint it verifies against, and the URL split --------------------

def case_split_host_port():
    cases = [
        ("http://192.168.1.2:3009", ("192.168.1.2", 3009), True),
        ("http://192.168.1.2:3009/firmware/manifest.json", ("192.168.1.2", 3009), True),
        ("http://192.168.1.2", ("192.168.1.2", 80), True),
        ("https://example.com:443", (None, None), True),
        ("", (None, None), True),
        (None, (None, None), True),
    ]
    ok = all(net.split_host_port(url) == want for url, want, _ in cases)
    return _report("split_host_port reads http URLs, refuses TLS", ok)


def case_probe_target_prefers_the_service_then_the_manifest():
    cfg = FakeConfig()
    cfg.REMOTE_SERVICE_URL = "http://192.168.1.2:3009"
    cfg.UPDATE_MANIFEST_URL = "http://192.168.1.9:1234/firmware/manifest.json"
    first = net._probe_target(cfg) == ("192.168.1.2", 3009)

    cfg.REMOTE_SERVICE_URL = None
    fallback = net._probe_target(cfg) == ("192.168.1.9", 1234)

    cfg.UPDATE_MANIFEST_URL = None
    nothing = net._probe_target(cfg) is None
    ok = first and fallback and nothing
    return _report("the probe target prefers the service, else the manifest", ok)


# --- radio_reset, end to end, against a fake network ------------------------

def _run_cycle(honest, cfg=None, server=None):
    """Drive radio_reset with a fake wlan; return (result, logs)."""
    wlan = RecordingWlan(honest=honest)
    _install_fake_network(wlan)
    cfg = cfg or FakeConfig()
    if server is not None:
        cfg.REMOTE_SERVICE_URL = "http://127.0.0.1:" + str(server.port)
    logs = []

    def log(message, exc=None):
        logs.append(message if exc is None else f"{message} :: {exc}")

    result = net.radio_reset(cfg, log=log)
    return result, logs, wlan


def case_radio_reset_reports_recovery_only_when_proved():
    server = _TCPServer()
    try:
        result, logs, wlan = _run_cycle(honest=True, server=server)
    finally:
        server.close()
    ok = (
        result is True
        and wlan.disconnects == 1
        and wlan.connects == 1
        and any("interface is down" in line for line in logs)
        and any("back up and answering" in line for line in logs)
    )
    return _report("a real cycle is True and says it is answering", ok)


def case_radio_reset_is_false_when_the_link_never_drops():
    """The deployment's actual failure: no drop, no cure, and it must say so."""
    result, logs, _wlan = _run_cycle(honest=False)
    ok = (
        result is False
        and any("never reported down" in line for line in logs)
        and any("did not take effect" in line for line in logs)
        and not any("back up and answering" in line for line in logs)
    )
    return _report("a cycle that never drops is False, not a false success", ok)


def case_radio_reset_order_is_down_then_up_then_join():
    server = _TCPServer()
    try:
        _result, _logs, wlan = _run_cycle(honest=True, server=server)
    finally:
        server.close()
    ok = wlan.calls[:4] == ["active(False)", "disconnect", "active(True)", "connect"]
    return _report("the cycle goes down, disconnects, comes up, joins", ok, " ".join(wlan.calls[:4]))


def case_radio_reset_without_ssid_does_nothing():
    cfg = FakeConfig()
    cfg.WIFI_SSID = None
    wlan = RecordingWlan()
    _install_fake_network(wlan)
    result = net.radio_reset(cfg, log=lambda *_a: None)
    return _report("no credentials means no cycle", result is False and wlan.calls == [])


def case_radio_reset_is_false_without_a_verification_endpoint():
    """No endpoint: it says so rather than claiming success it cannot show.

    It is still True in the narrow sense - the interface was seen to drop and
    come back - but the log must be explicit that nothing confirmed the radio
    is talking, so the line is never read as proof.
    """
    result, logs, _wlan = _run_cycle(honest=True)
    ok = result is True and any("no endpoint configured" in line for line in logs)
    return _report("with no endpoint the cycle says it is unverified", ok)


# --- the fuse budget the cycle must respect ---------------------------------

def _fuse_budget_cases():
    """The longest single unfed stretch is one probe or one down-wait."""
    longest_ms = max(config.RADIO_PROBE_MS, config.RADIO_DOWN_MS)
    return longest_ms < watchdog.TIMEOUT_MS


def case_the_cycles_longest_block_fits_under_the_fuse():
    ok = _fuse_budget_cases()
    return _report("every blocked step in the cycle fits the fuse", ok)


def main():
    results = [
        case_link_down_is_observed_immediately(),
        case_link_that_never_drops_times_out(),
        case_link_down_waits_out_the_budget_not_longer(),
        case_link_down_treats_an_unreadable_radio_as_down(),
        case_link_down_feeds_the_fuse(),
        case_tcp_probe_completes_against_a_listener(),
        case_tcp_probe_is_false_and_bounded_with_nothing_listening(),
        case_tcp_probe_feeds_before_it_blocks(),
        case_split_host_port(),
        case_probe_target_prefers_the_service_then_the_manifest(),
        case_radio_reset_reports_recovery_only_when_proved(),
        case_radio_reset_is_false_when_the_link_never_drops(),
        case_radio_reset_order_is_down_then_up_then_join(),
        case_radio_reset_without_ssid_does_nothing(),
        case_radio_reset_is_false_without_a_verification_endpoint(),
        case_the_cycles_longest_block_fits_under_the_fuse(),
    ]
    print()
    print(f"{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
