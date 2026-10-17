"""ScreenCastService state machine with fake processes: no FFmpeg, GPU, display or network."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from control_tv.screencast.capabilities import EncoderCapabilities
from control_tv.screencast.model import (
    EncodeFailedError,
    EncoderPreference,
    EncoderUnavailableError,
    ScreenCastState,
    ScreenCastStateError,
    SoftwareX264,
    SyntheticEncodeRequest,
    SyntheticSource,
    VaapiCqp,
    VaapiDevice,
    VideoProfile,
)
from control_tv.screencast.process import ManagedProcess, ProcessTimeout
from control_tv.screencast.service import OUTPUT_NAME, ScreenCastService


@dataclass
class FakeProcess:
    """Exits with `status` after `timeouts` waits time out; records the signals it got."""

    status: int = 0
    timeouts: int = 0
    ignores_sigterm: bool = False
    signals: list[str] = field(default_factory=list)
    ended: bool = False

    @property
    def pid(self) -> int:
        return 4242

    def poll(self) -> int | None:
        return self.status if self.ended else None

    def wait(self, timeout: float) -> int:
        if not self.ended and self.timeouts > 0:
            self.timeouts -= 1
            raise ProcessTimeout("still running")
        self.ended = True
        return self.status

    def terminate(self) -> None:
        self.signals.append("TERM")
        if not self.ignores_sigterm:
            self.ended, self.status = True, -15

    def kill(self) -> None:
        self.signals.append("KILL")
        self.ended, self.status = True, -9


@dataclass
class FakeRunner:
    process: FakeProcess = field(default_factory=FakeProcess)
    output: bytes = b"\x00\x00\x00\x18ftypisom"
    stderr: str = ""
    error: OSError | None = None
    argv: list[tuple[str, ...]] = field(default_factory=list)

    def start(self, argv: Sequence[str], *, stderr_path: Path) -> ManagedProcess:
        if self.error is not None:
            raise self.error
        self.argv.append(tuple(argv))
        stderr_path.write_text(self.stderr)
        if self.output:
            Path(argv[-1]).write_bytes(self.output)
        return self.process


BOTH_ENCODERS = EncoderCapabilities(frozenset({"h264_vaapi", "libx264"}), True)


@dataclass
class FakeProbe:
    capabilities: EncoderCapabilities = field(default_factory=lambda: BOTH_ENCODERS)

    def probe(self, device: VaapiDevice) -> EncoderCapabilities:
        return self.capabilities


def state(cast: ScreenCastService) -> ScreenCastState:
    """Reads the state through a call, so each check sees the current value."""
    return cast.state


REQUEST = SyntheticEncodeRequest(
    SyntheticSource(), VideoProfile(duration_seconds=2), EncoderPreference.VAAPI_THEN_SOFTWARE
)


def service(
    tmp_path: Path, runner: FakeRunner, probe: FakeProbe | None = None
) -> ScreenCastService:
    ticks = iter([100.0, 102.5, 103.0, 104.0])
    counter = iter(range(100))

    def workdir() -> Path:
        path = tmp_path / f"session-{next(counter)}"
        path.mkdir()
        return path

    return ScreenCastService(
        runner,
        probe or FakeProbe(),
        stop_grace_seconds=0.1,
        clock=lambda: next(ticks),
        make_workdir=workdir,
    )


def test_a_successful_encode_goes_idle_running_stopped(tmp_path: Path) -> None:
    runner = FakeRunner()
    cast = service(tmp_path, runner)
    assert state(cast) is ScreenCastState.IDLE

    choice = cast.start_synthetic(REQUEST)
    assert state(cast) is ScreenCastState.RUNNING
    result = cast.wait(30.0)

    assert state(cast) is ScreenCastState.STOPPED
    assert isinstance(choice.plan, VaapiCqp)
    assert result.encoder == choice.plan and result.fallback_reason is None
    assert result.output.name == OUTPUT_NAME and result.output_bytes > 0
    assert result.elapsed_seconds == 2.5
    assert "-b:v" not in runner.argv[0] and runner.argv[0][-1] == str(result.output)
    workdir = result.output.parent
    assert workdir.stat().st_mode & 0o777 == 0o700

    cast.close()
    assert not workdir.exists()


def test_the_software_fallback_runs_when_vaapi_is_missing_and_allowed(tmp_path: Path) -> None:
    runner = FakeRunner()
    cast = service(tmp_path, runner, FakeProbe(EncoderCapabilities(frozenset({"libx264"}), False)))

    result = (cast.start_synthetic(REQUEST), cast.wait(30.0))[1]

    assert result.encoder == SoftwareX264()
    assert result.fallback_reason is not None
    assert "libx264" in runner.argv[0]


def test_an_unavailable_encoder_fails_before_any_process(tmp_path: Path) -> None:
    runner = FakeRunner()
    cast = service(tmp_path, runner, FakeProbe(EncoderCapabilities(frozenset({"libx264"}), True)))
    only_gpu = SyntheticEncodeRequest(
        SyntheticSource(), VideoProfile(), EncoderPreference.VAAPI_ONLY
    )

    with pytest.raises(EncoderUnavailableError):
        cast.start_synthetic(only_gpu)

    assert state(cast) is ScreenCastState.FAILED and cast.last_error
    assert runner.argv == []
    assert list(tmp_path.iterdir()) == []


def test_a_process_that_cannot_start_leaves_nothing_behind(tmp_path: Path) -> None:
    cast = service(tmp_path, FakeRunner(error=FileNotFoundError("ffmpeg")))

    with pytest.raises(EncodeFailedError, match="could not start"):
        cast.start_synthetic(REQUEST)

    assert state(cast) is ScreenCastState.FAILED
    assert list(tmp_path.iterdir()) == []


def test_a_failing_encoder_reports_its_stderr_and_cleans_up(tmp_path: Path) -> None:
    runner = FakeRunner(
        process=FakeProcess(status=1),
        output=b"",
        stderr="Driver does not support any RC mode compatible with selected options",
    )
    cast = service(tmp_path, runner)
    cast.start_synthetic(REQUEST)

    with pytest.raises(EncodeFailedError, match="status 1: Driver does not support"):
        cast.wait(30.0)

    assert state(cast) is ScreenCastState.FAILED
    assert list(tmp_path.iterdir()) == []


def test_an_empty_output_is_a_failure_even_with_status_zero(tmp_path: Path) -> None:
    cast = service(tmp_path, FakeRunner(output=b""))
    cast.start_synthetic(REQUEST)

    with pytest.raises(EncodeFailedError, match="status 0"):
        cast.wait(30.0)
    assert state(cast) is ScreenCastState.FAILED


def test_a_timeout_terminates_then_kills_and_fails(tmp_path: Path) -> None:
    process = FakeProcess(timeouts=2, ignores_sigterm=True)
    cast = service(tmp_path, FakeRunner(process=process))
    cast.start_synthetic(REQUEST)

    with pytest.raises(EncodeFailedError, match="did not finish within 1 s"):
        cast.wait(1.0)

    assert process.signals == ["TERM", "KILL"]
    assert state(cast) is ScreenCastState.FAILED
    assert list(tmp_path.iterdir()) == []


def test_stop_is_idempotent_and_close_always_cleans(tmp_path: Path) -> None:
    process = FakeProcess(timeouts=10)
    cast = service(tmp_path, FakeRunner(process=process))
    cast.stop()  # idle: nothing to do
    cast.start_synthetic(REQUEST)

    cast.stop()
    cast.stop()

    assert state(cast) is ScreenCastState.STOPPED
    assert process.signals == ["TERM"]
    cast.close()
    cast.close()
    assert list(tmp_path.iterdir()) == []


def test_illegal_transitions_are_refused(tmp_path: Path) -> None:
    cast = service(tmp_path, FakeRunner(process=FakeProcess(timeouts=10)))

    with pytest.raises(ScreenCastStateError):
        cast.wait(1.0)
    cast.start_synthetic(REQUEST)
    with pytest.raises(ScreenCastStateError, match="running"):
        cast.start_synthetic(REQUEST)
    cast.close()


def test_a_new_session_replaces_the_last_one_and_its_files(tmp_path: Path) -> None:
    cast = service(tmp_path, FakeRunner())
    first = (cast.start_synthetic(REQUEST), cast.wait(30.0))[1]

    cast.start_synthetic(REQUEST)

    assert not first.output.parent.exists()
    cast.close()


def test_leaving_the_with_block_on_an_error_stops_and_cleans(tmp_path: Path) -> None:
    process = FakeProcess(timeouts=10)

    with pytest.raises(RuntimeError), service(tmp_path, FakeRunner(process=process)) as cast:
        cast.start_synthetic(REQUEST)
        raise RuntimeError("caller failed")

    assert process.signals == ["TERM"]
    assert list(tmp_path.iterdir()) == []
