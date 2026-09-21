#!/usr/bin/env python3
"""Tests for apply_static_ip(): which API sets the address, and why.

    python3 tests/test_static_ip.py

The reason this has a test at all is micropython#15695: on the rp2 port the
4-tuple ifconfig((ip, mask, gw, dns)) leaves the board unable to resolve any
name at all - every getaddrinfo raises OSError(-2), NTP fails its three
attempts and the update check fails on every boot - while raw UDP DNS to the
same server answers in 8-160 ms. The newer pair of calls, wlan.ipconfig() for
the address and network.ipconfig() for the resolver, works. These tests pin
that preference, and the fallback to the older form for firmware that has no
network.ipconfig (the regression arrived in 1.24, ipconfig with it).
"""

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

import updater


class FakeConfig:
    STATIC_IP = "192.168.1.63"
    STATIC_MASK = "255.255.255.0"
    STATIC_GATEWAY = "192.168.1.1"
    STATIC_DNS = "192.168.1.1"


class FakeNetwork:
    """Stands in for the `network` module, which apply_static_ip imports."""

    def __init__(self):
        self.calls = []

    def ipconfig(self, dns=None):
        self.calls.append(("dns", dns))
        return dns


class FakeWLAN:
    def __init__(self, ipconfig_raises=False):
        self.calls = []
        self.ipconfig_raises = ipconfig_raises

    def ipconfig(self, addr4=None, gw4=None):
        self.calls.append(("ipconfig", addr4, gw4))
        if self.ipconfig_raises:
            raise OSError(1)

    def ifconfig(self, arg):
        self.calls.append(("ifconfig", arg))
        return arg


def report(name, ok, detail):
    print(("PASS" if ok else "FAIL"), "-", name, "::", detail)
    return ok


def with_network(module, body):
    """Install `module` as the real `network` for the duration of body()."""
    had = "network" in sys.modules
    old = sys.modules.get("network")
    sys.modules["network"] = module
    try:
        return body()
    finally:
        if had:
            sys.modules["network"] = old
        else:
            sys.modules.pop("network", None)


def fresh(config=None):
    """A working directory that absorbs the update.log line apply_static_ip writes."""
    tmp = tempfile.mkdtemp(prefix="static-ip-")
    os.chdir(tmp)
    return config or FakeConfig()


def case_prefers_ipconfig():
    def body():
        fresh()
        net = FakeNetwork()
        wlan = FakeWLAN()

        def run():
            return updater.apply_static_ip(wlan, FakeConfig())

        ok = with_network(net, run)
        return report(
            "the address goes through wlan.ipconfig, the resolver through network.ipconfig",
            ok is True
            and wlan.calls == [("ipconfig", ("192.168.1.63", "255.255.255.0"), "192.168.1.1")]
            and net.calls == [("dns", "192.168.1.1")],
            f"ok={ok!r} wlan={wlan.calls} network={net.calls}",
        )

    return body()


def case_never_passes_dns_to_ifconfig():
    def body():
        fresh()
        net = FakeNetwork()
        wlan = FakeWLAN()

        def run():
            return updater.apply_static_ip(wlan, FakeConfig())

        with_network(net, run)
        used_ifconfig = [c for c in wlan.calls if c[0] == "ifconfig"]
        return report(
            "the 4-tuple ifconfig - the path that breaks DNS - is not used",
            used_ifconfig == [],
            f"ifconfig calls={used_ifconfig}",
        )

    return body()


def case_falls_back_to_ifconfig():
    def body():
        fresh()
        net = FakeNetwork()
        wlan = FakeWLAN(ipconfig_raises=True)

        def run():
            return updater.apply_static_ip(wlan, FakeConfig())

        ok = with_network(net, run)
        return report(
            "an ipconfig that raises falls back to the older ifconfig",
            ok is True
            and wlan.calls
            == [
                ("ipconfig", ("192.168.1.63", "255.255.255.0"), "192.168.1.1"),
                ("ifconfig", ("192.168.1.63", "255.255.255.0", "192.168.1.1", "192.168.1.1")),
            ],
            f"ok={ok!r} wlan={wlan.calls}",
        )

    return body()


def case_no_network_module():
    def body():
        fresh()
        wlan = FakeWLAN()
        sys.modules.pop("network", None)

        # No network module at all: the import inside apply_static_ip fails and
        # the older ifconfig is the only thing left.
        ok = updater.apply_static_ip(wlan, FakeConfig())
        return report(
            "without a network module it still sets the address",
            ok is True
            and [c for c in wlan.calls if c[0] == "ifconfig"]
            == [("ifconfig", ("192.168.1.63", "255.255.255.0", "192.168.1.1", "192.168.1.1"))],
            f"ok={ok!r} wlan={wlan.calls}",
        )

    return body()


def case_no_static_ip():
    def body():
        fresh()
        net = FakeNetwork()
        wlan = FakeWLAN()

        class NoStatic(FakeConfig):
            STATIC_IP = None

        def run():
            return updater.apply_static_ip(wlan, NoStatic())

        ok = with_network(net, run)
        return report(
            "no STATIC_IP means nothing is touched (DHCP stays the default)",
            ok is False and wlan.calls == [] and net.calls == [],
            f"ok={ok!r} wlan={wlan.calls} network={net.calls}",
        )

    return body()


def case_incomplete_static_ip():
    def body():
        fresh()
        net = FakeNetwork()
        wlan = FakeWLAN()

        class Partial(FakeConfig):
            STATIC_DNS = None

        def run():
            return updater.apply_static_ip(wlan, Partial())

        ok = with_network(net, run)
        return report(
            "a half-configured address is refused rather than half-applied",
            ok is False and wlan.calls == [] and net.calls == [],
            f"ok={ok!r} wlan={wlan.calls} network={net.calls}",
        )

    return body()


def main():
    results = [
        case_prefers_ipconfig(),
        case_never_passes_dns_to_ifconfig(),
        case_falls_back_to_ifconfig(),
        case_no_network_module(),
        case_no_static_ip(),
        case_incomplete_static_ip(),
    ]
    print(f"{sum(1 for r in results if r)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
