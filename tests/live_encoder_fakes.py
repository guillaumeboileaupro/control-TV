"""Fake process group, runner and probe for the D0 live encoder (no process, no FFmpeg).

Shared by the encoder's own tests and the D1 end-to-end tests.
"""

from __future__ import annotations

import signal
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from control_tv.screen_streaming.h264_encoder import D0LiveEncoder, LiveEncoderSettings
from control_tv.screencast.capabilities import EncoderCapabilities
from control_tv.screencast.model import VaapiDevice
from control_tv.screencast.process import ManagedProcess, ProcessTimeout

BOTH = EncoderCapabilities(frozenset({"h264_vaapi", "libx264"}), True)
CPU_ONLY = EncoderCapabilities(frozenset({"libx264"}), False)


@dataclass
class FakeGroup:
    """A process group: TERM ends it unless `survives`; `interrupt` raises once on a signal."""

    survives: bool = False
    interrupt: BaseException | None = None
    ended: bool = False
    released: bool = False
    releases: int = 0
    signals: list[signal.Signals] = field(default_factory=list)

    @property
    def pid(self) -> int:
        return 4242

    def exit_status(self) -> int | None:
        return -15 if self.ended else None

    def wait_exit(self, timeout: float) -> int:
        raise ProcessTimeout("running")

    def group_alive(self) -> bool:
        return not self.released and (self.survives or not self.ended)

    def signal_group(self, sig: signal.Signals) -> None:
        if self.interrupt is not None:
            interrupt, self.interrupt = self.interrupt, None
            raise interrupt
        assert not self.released, "a released group was signalled"
        self.signals.append(sig)
        if not self.survives:
            self.ended = True

    def release(self) -> int | None:
        if self.released:
            return -15
        if self.group_alive():
            return None
        self.released = True
        self.releases += 1
        return -15


@dataclass
class FakeRunner:
    group: FakeGroup = field(default_factory=FakeGroup)
    error: BaseException | None = None
    argv: list[tuple[str, ...]] = field(default_factory=list)

    def start(self, argv: Sequence[str], *, stderr_path: Path) -> ManagedProcess:
        if self.error is not None:
            raise self.error
        self.argv.append(tuple(argv))
        stderr_path.write_text("")
        (stderr_path.parent / "index.m3u8").write_text("#EXTM3U\n")
        return self.group


@dataclass
class FakeProbe:
    capabilities: EncoderCapabilities = field(default_factory=lambda: BOTH)
    calls: int = 0

    def probe(self, device: VaapiDevice) -> EncoderCapabilities:
        self.calls += 1
        return self.capabilities


def encoder(
    tmp_path: Path,
    runner: FakeRunner | None = None,
    probe: FakeProbe | None = None,
    settings: LiveEncoderSettings | None = None,
) -> D0LiveEncoder:
    counter = iter(range(100))

    def workdir() -> Path:
        path = tmp_path / f"live-{next(counter)}"
        path.mkdir()
        return path

    return D0LiveEncoder(
        runner or FakeRunner(),
        probe or FakeProbe(),
        settings or LiveEncoderSettings(stop_grace_seconds=0.05),
        make_workdir=workdir,
        handle_factory=lambda: "live-1",
    )
