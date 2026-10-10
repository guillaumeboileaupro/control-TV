"""Discovery reports whether each TV offers its own remote-control channel (issue #37).

`PyChromecastTransport` runs a remote-service scan alongside Cast discovery and marks each
device by address. Fakes only: no network, no Cast connection.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from pychromecast import Chromecast

from control_tv.adapters import PyChromecastTransport
from control_tv.adapters.remote_detection import NoRemoteDetection, ZeroconfRemoteDetector
from control_tv.domain import DiscoveryError

TV_A = "11111111-1111-1111-1111-111111111111"
TV_B = "22222222-2222-2222-2222-222222222222"
GROUP = "33333333-3333-3333-3333-333333333333"


def fake_cast(uuid: str, host: str, cast_type: str = "cast") -> Chromecast:
    device = SimpleNamespace(
        uuid=UUID(uuid),
        cast_info=SimpleNamespace(
            uuid=UUID(uuid),
            friendly_name=f"TV {uuid[0]}",
            host=host,
            port=8009,
            cast_type=cast_type,
            model_name="Google TV",
        ),
        disconnect=lambda timeout=None: None,
    )
    return cast(Chromecast, device)


class Detector:
    def __init__(self, hosts: frozenset[str] | None) -> None:
        self.hosts = hosts
        self.calls: list[object] = []

    def detect(self, zeroconf: object) -> frozenset[str] | None:
        self.calls.append(zeroconf)
        return self.hosts


class Browser:
    """Stands for PyChromecast's CastBrowser, whose zeroconf the detector must reuse."""

    def __init__(self) -> None:
        self.zc = object()

    def stop_discovery(self) -> None:
        pass


def discover_with(hosts: frozenset[str] | None) -> dict[str, bool | None]:
    casts = [
        fake_cast(TV_A, "192.0.2.10"),
        fake_cast(TV_B, "192.0.2.11"),
        fake_cast(GROUP, "192.0.2.10", "group"),
    ]
    transport = PyChromecastTransport(
        discoverer=lambda timeout: (casts, Browser()), remote_detector=Detector(hosts)
    )
    return {str(d.id): d.android_tv_remote for d in transport.discover(timeout=1.0)}


def test_a_tv_at_an_address_advertising_the_service_offers_it_and_the_others_do_not() -> None:
    found = discover_with(frozenset({"192.0.2.10"}))

    assert found[TV_A] is True
    assert found[TV_B] is False


def test_a_cast_group_never_borrows_its_member_tvs_remote_channel() -> None:
    assert discover_with(frozenset({"192.0.2.10"}))[GROUP] is None


def test_a_scan_that_cannot_tell_leaves_every_device_unknown() -> None:
    assert set(discover_with(None).values()) == {None}


def test_detection_reuses_the_cast_browsers_own_zeroconf_instance() -> None:
    detector, browser = Detector(frozenset()), Browser()
    transport = PyChromecastTransport(
        discoverer=lambda timeout: ([fake_cast(TV_A, "192.0.2.10")], browser),
        remote_detector=detector,
    )

    transport.discover(timeout=1.0)

    assert detector.calls == [browser.zc]


def test_no_device_found_means_no_detection() -> None:
    detector = Detector(frozenset())
    transport = PyChromecastTransport(
        discoverer=lambda timeout: ([], Browser()), remote_detector=detector
    )

    assert transport.discover(timeout=1.0) == []
    assert detector.calls == []


def test_a_failing_cast_discovery_runs_no_detection() -> None:
    detector = Detector(frozenset())

    def failing(timeout: float) -> Any:
        raise DiscoveryError("no network")

    transport = PyChromecastTransport(discoverer=failing, remote_detector=detector)

    with pytest.raises(DiscoveryError):
        transport.discover(timeout=1.0)
    assert detector.calls == []


def test_recovery_inside_a_status_read_never_runs_a_detection() -> None:
    detector = Detector(frozenset({"192.0.2.10"}))
    transport = PyChromecastTransport(
        discoverer=lambda timeout: ([fake_cast(TV_A, "192.0.2.10")], Browser()),
        remote_detector=detector,
    )

    transport._discover(timeout=1.0, cleanup_deadline=10.0)

    assert detector.calls == []


def test_real_discovery_looks_for_the_service_and_a_test_discoverer_does_not() -> None:
    real = PyChromecastTransport()
    injected = PyChromecastTransport(discoverer=lambda timeout: ([], object()))

    assert isinstance(real._remote_detector, ZeroconfRemoteDetector)
    assert isinstance(injected._remote_detector, NoRemoteDetection)
