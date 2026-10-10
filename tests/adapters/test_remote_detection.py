"""Passive detection of the Android TV Remote service (`control_tv.adapters.remote_detection`).

The zeroconf browser and its records are fakes: nothing here touches a network. The rules
pinned: only advertised and resolved services count, any failure or unresolved record makes
the answer unknown (never a false "not offered"), the browse is cancelled, and the zeroconf
instance, which belongs to Cast discovery, is never closed.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from zeroconf import Error as ZeroconfError
from zeroconf import ServiceStateChange

from control_tv.adapters import remote_detection
from control_tv.adapters.remote_detection import (
    SERVICE_TYPE,
    NoRemoteDetection,
    ZeroconfRemoteDetector,
)

ROOT = Path(__file__).resolve().parents[2]
ADDED = ServiceStateChange.Added


class SharedZeroconf:
    """Cast discovery's zeroconf: the detector may browse on it but never close it."""

    def close(self) -> None:
        raise AssertionError("the detector closed a zeroconf instance it does not own")


class FakeBrowser:
    def __init__(self, zc: Any, service_type: str, *, handlers: list[Any]) -> None:
        assert service_type == SERVICE_TYPE
        self.zc = zc
        self.handler = handlers[0]
        self.cancelled = 0

    def announce(self, name: str, change: ServiceStateChange = ADDED) -> None:
        self.handler(zeroconf=self.zc, service_type=SERVICE_TYPE, name=name, state_change=change)

    def cancel(self) -> None:
        self.cancelled += 1


class Records:
    """What the fake network knows: per service name, its addresses and how it resolves."""

    def __init__(self) -> None:
        self.cached: dict[str, list[str]] = {}
        self.requestable: dict[str, list[str]] = {}
        self.requests: list[tuple[str, int]] = []
        self.fail_with: Exception | None = None

    def info(self, service_type: str, name: str) -> FakeInfo:
        return FakeInfo(self, name)


class FakeInfo:
    def __init__(self, records: Records, name: str) -> None:
        self.records = records
        self.name = name
        self.addresses: list[str] = []

    def load_from_cache(self, zc: Any) -> bool:
        if self.records.fail_with is not None:
            raise self.records.fail_with
        if self.name in self.records.cached:
            self.addresses = self.records.cached[self.name]
            return True
        return False

    def request(self, zc: Any, timeout: int) -> bool:
        self.records.requests.append((self.name, timeout))
        if self.name in self.records.requestable:
            self.addresses = self.records.requestable[self.name]
            return True
        return False

    def parsed_addresses(self) -> list[str]:
        return self.addresses


def run(
    records: Records,
    announcements: list[tuple[str, ServiceStateChange]],
    *,
    clock: Callable[[], float] = lambda: 100.0,
    browser_error: Exception | None = None,
) -> tuple[frozenset[str] | None, list[FakeBrowser], list[float]]:
    """Run one detection; the announcements arrive while the detector waits."""
    browsers: list[FakeBrowser] = []
    waits: list[float] = []

    def make_browser(zc: Any, service_type: str, *, handlers: list[Any]) -> FakeBrowser:
        if browser_error is not None:
            raise browser_error
        browsers.append(FakeBrowser(zc, service_type, handlers=handlers))
        return browsers[-1]

    def wait(seconds: float) -> None:
        waits.append(seconds)
        for name, change in announcements:
            browsers[-1].announce(name, change)

    detector = ZeroconfRemoteDetector(
        browse_seconds=1.5,
        resolve_timeout=1.0,
        browser_factory=make_browser,
        info_factory=records.info,
        sleep=wait,
        clock=clock,
    )
    return detector.detect(SharedZeroconf()), browsers, waits


def test_advertised_services_resolved_from_the_cache_give_their_addresses() -> None:
    records = Records()
    records.cached = {"TV A._androidtvremote2._tcp.local.": ["192.0.2.10", "fe80::1"]}

    found, browsers, waits = run(records, [("TV A._androidtvremote2._tcp.local.", ADDED)])

    assert found == frozenset({"192.0.2.10", "fe80::1"})
    assert waits == [1.5]
    assert browsers[0].cancelled == 1
    assert records.requests == []


def test_no_service_advertised_means_no_tv_offers_it() -> None:
    found, browsers, _ = run(Records(), [])

    assert found == frozenset()
    assert browsers[0].cancelled == 1


def test_a_removed_service_is_not_counted() -> None:
    records = Records()
    records.cached = {"TV A": ["192.0.2.10"]}

    found, _, _ = run(records, [("TV A", ADDED), ("TV A", ServiceStateChange.Removed)])

    assert found == frozenset()


def test_a_record_missing_from_the_cache_is_requested_within_the_budget() -> None:
    records = Records()
    records.requestable = {"TV B": ["192.0.2.11"]}

    found, _, _ = run(records, [("TV B", ADDED)])

    assert found == frozenset({"192.0.2.11"})
    assert records.requests == [("TV B", 1000)]


def test_a_service_seen_but_not_resolved_makes_the_answer_unknown() -> None:
    records = Records()
    records.cached = {"TV A": ["192.0.2.10"]}

    found, _, _ = run(records, [("TV A", ADDED), ("TV unresolvable", ADDED)])

    assert found is None


def test_an_exhausted_budget_makes_the_answer_unknown_without_waiting() -> None:
    records = Records()
    records.requestable = {"TV B": ["192.0.2.11"]}
    times = iter([100.0, 102.0])  # the 1 s budget is already spent at the first resolve

    found, _, _ = run(records, [("TV B", ADDED)], clock=lambda: next(times))

    assert found is None
    assert records.requests == []


def test_a_browser_that_cannot_start_leaves_the_answer_unknown() -> None:
    found, browsers, waits = run(Records(), [], browser_error=ZeroconfError("bad browse"))

    assert found is None
    assert browsers == [] and waits == []


def test_an_error_while_resolving_leaves_the_answer_unknown_and_the_browse_cancelled() -> None:
    records = Records()
    records.fail_with = OSError("socket closed")

    found, browsers, _ = run(records, [("TV A", ADDED)])

    assert found is None
    assert browsers[0].cancelled == 1


def test_an_error_while_browsing_leaves_the_answer_unknown_and_still_cancels_the_browse() -> None:
    browsers: list[FakeBrowser] = []

    def make_browser(zc: Any, service_type: str, *, handlers: list[Any]) -> FakeBrowser:
        browsers.append(FakeBrowser(zc, service_type, handlers=handlers))
        return browsers[-1]

    def interrupted(seconds: float) -> None:
        raise RuntimeError("zeroconf stopped while browsing")

    detector = ZeroconfRemoteDetector(browser_factory=make_browser, sleep=interrupted)

    assert detector.detect(SharedZeroconf()) is None
    assert browsers[0].cancelled == 1


def test_no_zeroconf_instance_means_unknown_without_any_browse() -> None:
    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("no browse without a zeroconf instance")

    assert ZeroconfRemoteDetector(browser_factory=refuse).detect(None) is None


def test_no_detection_never_looks_and_knows_nothing() -> None:
    assert NoRemoteDetection().detect(SharedZeroconf()) is None


def test_the_detector_only_listens_and_never_connects_or_opens_its_own_zeroconf() -> None:
    source = (ROOT / "src" / "control_tv" / "adapters" / "remote_detection.py").read_text()

    assert "import socket" not in source
    assert ".connect(" not in source
    assert "Zeroconf(" not in source
    assert remote_detection.SERVICE_TYPE == "_androidtvremote2._tcp.local."
