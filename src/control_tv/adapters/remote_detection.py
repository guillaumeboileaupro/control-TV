"""Passive detection of the Android TV Remote service on the local network.

Google TV and Android TV devices advertise `_androidtvremote2._tcp.local.`, the channel their
own remote uses for keys such as volume, mute, power and navigation. control-TV does not use
that channel; it only reports whether a discovered TV offers it, so the application can say
honestly what a TV whose Cast volume is fixed could offer instead.

This module only listens: it browses and resolves mDNS records and never connects to the
service. It runs right after Cast discovery, on that discovery's own zeroconf instance: a
second instance on the same host would compete with it for the mDNS replies sent to one
socket only, and either could then miss a device. It never closes that instance, which
belongs to Cast discovery. Any failure, or a service seen but not resolved, makes the answer
unknown (`None`), never a false "not offered".
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from contextlib import suppress
from typing import Any, Protocol

from zeroconf import Error as ZeroconfError
from zeroconf import ServiceBrowser, ServiceInfo, ServiceStateChange

SERVICE_TYPE = "_androidtvremote2._tcp.local."
DEFAULT_BROWSE_SECONDS = 1.5
DEFAULT_RESOLVE_TIMEOUT = 1.0


class RemoteDetector(Protocol):
    def detect(self, zeroconf: Any) -> frozenset[str] | None:
        """The addresses advertising the service, or None when it cannot tell."""


class NoRemoteDetection:
    """A detector that never looks: every device's remote channel stays unknown."""

    def detect(self, zeroconf: Any) -> frozenset[str] | None:
        return None


class ZeroconfRemoteDetector:
    """Browse the Android TV Remote service on a zeroconf instance it does not own."""

    def __init__(
        self,
        *,
        browse_seconds: float = DEFAULT_BROWSE_SECONDS,
        resolve_timeout: float = DEFAULT_RESOLVE_TIMEOUT,
        browser_factory: Callable[..., Any] = ServiceBrowser,
        info_factory: Callable[[str, str], Any] = ServiceInfo,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._browse_seconds = browse_seconds
        self._resolve_timeout = resolve_timeout
        self._browser_factory = browser_factory
        self._info_factory = info_factory
        self._sleep = sleep
        self._clock = clock

    def detect(self, zeroconf: Any) -> frozenset[str] | None:
        if zeroconf is None:
            return None
        names: set[str] = set()
        lock = threading.Lock()

        def on_change(
            zeroconf: Any, service_type: str, name: str, state_change: ServiceStateChange
        ) -> None:
            with lock:
                if state_change is ServiceStateChange.Removed:
                    names.discard(name)
                else:
                    names.add(name)

        browser: Any = None
        try:
            browser = self._browser_factory(zeroconf, SERVICE_TYPE, handlers=[on_change])
            self._sleep(self._browse_seconds)
            browser.cancel()
            browser = None
            with lock:
                seen = sorted(names)
            return self._resolve(zeroconf, seen)
        except (OSError, RuntimeError, ZeroconfError):
            return None
        finally:
            if browser is not None:
                with suppress(OSError, RuntimeError, ZeroconfError):
                    browser.cancel()

    def _resolve(self, zeroconf: Any, names: list[str]) -> frozenset[str] | None:
        deadline = self._clock() + self._resolve_timeout
        addresses: set[str] = set()
        for name in names:
            info = self._info_factory(SERVICE_TYPE, name)
            if not info.load_from_cache(zeroconf):
                remaining = deadline - self._clock()
                if remaining <= 0 or not info.request(zeroconf, int(remaining * 1000)):
                    return None  # seen but not resolved: cannot tell which TV offers it
            addresses.update(info.parsed_addresses())
        return frozenset(addresses)
