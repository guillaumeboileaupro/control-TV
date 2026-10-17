"""ScreenCastService state machine with fake processes: no FFmpeg, GPU, display or network."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from control_tv.screencast import service as service_module
from control_tv.screencast.capabilities import EncoderCapabilities
from control_tv.screencast.model import (
    EncodeFailedError,
    EncodeRequest,
    EncoderPreference,
    EncoderUnavailableError,
    InvalidScreenCastOption,
    ScreenCastState,
    ScreenCastStateError,
    SoftwareX264,
    VaapiCqp,
    VaapiDevice,
    VideoProfile,
)
from control_tv.screencast.process import ManagedProcess, ProcessTimeout
from control_tv.screencast.service import OUTPUT_NAME, STDERR_NAME, ScreenCastService
from control_tv.screencast.source import SyntheticSource


@dataclass
class FakeProcess:
    """Exits with `status` after `timeouts` waits time out; records the signals it got."""

    status: int = 0
    timeouts: int = 0
    ignores_sigterm: bool = False
    survives_sigkill: bool = False
    wait_error: OSError | None = None
    signal_error: OSError | None = None
    signals: list[str] = field(default_factory=list)
    ended: bool = False

    @property
    def pid(self) -> int:
        return 4242

    def poll(self) -> int | None:
        return self.status if self.ended else None

    def wait(self, timeout: float) -> int:
        if self.wait_error is not None:
            raise self.wait_error
        if not self.ended and (self.survives_sigkill or self.timeouts > 0):
            self.timeouts -= 1
            raise ProcessTimeout("still running")
        self.ended = True
        return self.status

    def terminate(self) -> None:
        if self.signal_error is not None:
            raise self.signal_error
        self.signals.append("TERM")
        if not (self.ignores_sigterm or self.survives_sigkill):
            self.ended, self.status = True, -15

    def kill(self) -> None:
        self.signals.append("KILL")
        if not self.survives_sigkill:
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


REQUEST = EncodeRequest(
    SyntheticSource(), VideoProfile(duration_seconds=2), EncoderPreference.VAAPI_THEN_SOFTWARE
)


@dataclass
class ExitHooks:
    """Records the interpreter-exit hooks instead of registering real `atexit` callbacks."""

    registered: list[Callable[[], None]] = field(default_factory=list)

    def register(self, callback: Callable[[], None]) -> None:
        self.registered.append(callback)

    def forget(self, callback: Callable[[], None]) -> None:
        if callback in self.registered:
            self.registered.remove(callback)


def service(
    tmp_path: Path,
    runner: FakeRunner,
    probe: FakeProbe | None = None,
    hooks: ExitHooks | None = None,
) -> ScreenCastService:
    exit_hooks = hooks or ExitHooks()
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
        on_exit=exit_hooks.register,
        forget_on_exit=exit_hooks.forget,
    )


def test_a_successful_encode_goes_idle_running_stopped(tmp_path: Path) -> None:
    runner = FakeRunner()
    cast = service(tmp_path, runner)
    assert state(cast) is ScreenCastState.IDLE

    choice = cast.start(REQUEST)
    assert state(cast) is ScreenCastState.RUNNING and cast.choice == choice
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

    result = (cast.start(REQUEST), cast.wait(30.0))[1]

    assert result.encoder == SoftwareX264()
    assert result.fallback_reason is not None
    assert "libx264" in runner.argv[0]


def test_an_unavailable_encoder_fails_before_any_process(tmp_path: Path) -> None:
    runner = FakeRunner()
    cast = service(tmp_path, runner, FakeProbe(EncoderCapabilities(frozenset({"libx264"}), True)))
    only_gpu = EncodeRequest(SyntheticSource(), VideoProfile(), EncoderPreference.VAAPI_ONLY)

    with pytest.raises(EncoderUnavailableError):
        cast.start(only_gpu)

    assert state(cast) is ScreenCastState.FAILED and cast.last_error
    assert runner.argv == []
    assert list(tmp_path.iterdir()) == []


def test_a_process_that_cannot_start_leaves_nothing_behind(tmp_path: Path) -> None:
    cast = service(tmp_path, FakeRunner(error=FileNotFoundError("ffmpeg")))

    with pytest.raises(EncodeFailedError, match="could not start"):
        cast.start(REQUEST)

    assert state(cast) is ScreenCastState.FAILED
    assert list(tmp_path.iterdir()) == []


def test_a_failing_encoder_reports_its_stderr_and_cleans_up(tmp_path: Path) -> None:
    runner = FakeRunner(
        process=FakeProcess(status=1),
        output=b"",
        stderr="Driver does not support any RC mode compatible with selected options",
    )
    cast = service(tmp_path, runner)
    cast.start(REQUEST)

    with pytest.raises(EncodeFailedError, match="status 1: Driver does not support"):
        cast.wait(30.0)

    assert state(cast) is ScreenCastState.FAILED
    assert list(tmp_path.iterdir()) == []


def test_an_empty_output_is_a_failure_even_with_status_zero(tmp_path: Path) -> None:
    cast = service(tmp_path, FakeRunner(output=b""))
    cast.start(REQUEST)

    with pytest.raises(EncodeFailedError, match="status 0"):
        cast.wait(30.0)
    assert state(cast) is ScreenCastState.FAILED


def test_a_timeout_terminates_then_kills_and_fails(tmp_path: Path) -> None:
    process = FakeProcess(timeouts=2, ignores_sigterm=True)
    cast = service(tmp_path, FakeRunner(process=process))
    cast.start(REQUEST)

    with pytest.raises(EncodeFailedError, match="did not finish within 1 s"):
        cast.wait(1.0)

    assert process.signals == ["TERM", "KILL"]
    assert state(cast) is ScreenCastState.FAILED
    assert list(tmp_path.iterdir()) == []


def test_stop_is_idempotent_and_close_always_cleans(tmp_path: Path) -> None:
    process = FakeProcess(timeouts=10)
    cast = service(tmp_path, FakeRunner(process=process))
    cast.stop()  # idle: nothing to do
    cast.start(REQUEST)

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
    cast.start(REQUEST)
    with pytest.raises(ScreenCastStateError, match="running"):
        cast.start(REQUEST)
    cast.close()


def test_a_new_session_replaces_the_last_one_and_its_files(tmp_path: Path) -> None:
    cast = service(tmp_path, FakeRunner())
    first = (cast.start(REQUEST), cast.wait(30.0))[1]

    cast.start(REQUEST)

    assert not first.output.parent.exists()
    cast.close()


def test_leaving_the_with_block_on_an_error_stops_and_cleans(tmp_path: Path) -> None:
    process = FakeProcess(timeouts=10)

    with pytest.raises(RuntimeError), service(tmp_path, FakeRunner(process=process)) as cast:
        cast.start(REQUEST)
        raise RuntimeError("caller failed")

    assert process.signals == ["TERM"]
    assert list(tmp_path.iterdir()) == []


# M1: an encoder that survives SIGKILL is reported, never claimed stopped ------------------------


def test_an_encoder_surviving_sigkill_on_stop_fails_and_is_reported(tmp_path: Path) -> None:
    process = FakeProcess(survives_sigkill=True)
    hooks = ExitHooks()
    cast = service(tmp_path, FakeRunner(process=process), hooks=hooks)
    cast.start(REQUEST)

    with pytest.raises(EncodeFailedError, match=r"pid 4242\) did not stop after SIGKILL"):
        cast.stop()

    assert process.signals == ["TERM", "KILL"]
    assert state(cast) is ScreenCastState.FAILED
    assert cast.unstoppable_pids == (4242,)
    assert cast.last_error is not None and "may still be running" in cast.last_error
    assert list(tmp_path.iterdir()) == []  # recoverable resources are still removed
    cast.close()  # never raises, and does not signal the process again
    assert process.signals == ["TERM", "KILL"]
    assert hooks.registered == []


def test_an_encoder_surviving_sigkill_after_a_timeout_says_both(tmp_path: Path) -> None:
    cast = service(tmp_path, FakeRunner(process=FakeProcess(survives_sigkill=True)))
    cast.start(REQUEST)

    with pytest.raises(EncodeFailedError, match=r"within 1 s; the encoder .* SIGKILL"):
        cast.wait(1.0)

    assert state(cast) is ScreenCastState.FAILED and cast.unstoppable_pids == (4242,)
    assert list(tmp_path.iterdir()) == []


def test_close_reports_an_encoder_surviving_sigkill_without_raising(tmp_path: Path) -> None:
    cast = service(tmp_path, FakeRunner(process=FakeProcess(survives_sigkill=True)))
    cast.start(REQUEST)

    cast.close()

    assert state(cast) is ScreenCastState.FAILED
    assert cast.unstoppable_pids == (4242,)
    assert list(tmp_path.iterdir()) == []


def test_a_signal_error_while_stopping_is_reported_not_raised_raw(tmp_path: Path) -> None:
    process = FakeProcess(timeouts=10, signal_error=PermissionError("not allowed"))
    cast = service(tmp_path, FakeRunner(process=process))
    cast.start(REQUEST)

    with pytest.raises(EncodeFailedError, match="did not stop"):
        cast.stop()
    assert state(cast) is ScreenCastState.FAILED


def test_waiting_that_fails_with_an_os_error_becomes_an_encode_failure(tmp_path: Path) -> None:
    process = FakeProcess(wait_error=ChildProcessError("gone"))
    cast = service(tmp_path, FakeRunner(process=process))
    cast.start(REQUEST)

    with pytest.raises(EncodeFailedError, match="could not be waited for: ChildProcessError"):
        cast.wait(1.0)

    assert state(cast) is ScreenCastState.FAILED
    assert list(tmp_path.iterdir()) == []


def test_a_session_directory_that_cannot_be_created_fails_cleanly(tmp_path: Path) -> None:
    runner = FakeRunner()

    def no_directory() -> Path:
        raise PermissionError("read-only")

    cast = ScreenCastService(runner, FakeProbe(), make_workdir=no_directory)

    with pytest.raises(EncodeFailedError, match="could not be created: PermissionError"):
        cast.start(REQUEST)

    assert state(cast) is ScreenCastState.FAILED and runner.argv == []


# A4: an encode that already ended in error stays failed ---------------------------------------


def test_stop_after_an_encoder_error_keeps_the_failure(tmp_path: Path) -> None:
    process = FakeProcess(status=1)
    cast = service(tmp_path, FakeRunner(process=process, stderr="Invalid argument"))
    cast.start(REQUEST)
    process.ended = True

    with pytest.raises(EncodeFailedError, match="status 1: Invalid argument"):
        cast.stop()

    assert state(cast) is ScreenCastState.FAILED and process.signals == []
    assert list(tmp_path.iterdir()) == []


def test_stop_after_a_clean_end_is_stopped_without_signals(tmp_path: Path) -> None:
    process = FakeProcess()
    cast = service(tmp_path, FakeRunner(process=process))
    cast.start(REQUEST)
    process.ended = True

    cast.stop()

    assert state(cast) is ScreenCastState.STOPPED and process.signals == []


# M2: the D0 restriction to synthetic sources is the service's -------------------------------


def test_a_source_kind_outside_d0_is_refused_before_any_probe_or_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_module, "D0_ALLOWED_SOURCES", frozenset())
    runner = FakeRunner()
    cast = service(tmp_path, runner)

    with pytest.raises(InvalidScreenCastOption, match="only the synthetic test source"):
        cast.start(REQUEST)

    assert state(cast) is ScreenCastState.FAILED
    assert runner.argv == [] and list(tmp_path.iterdir()) == []


# Interpreter exit ---------------------------------------------------------------------------


def test_a_running_encode_is_hooked_to_interpreter_exit_until_closed(tmp_path: Path) -> None:
    hooks = ExitHooks()
    process = FakeProcess(timeouts=10)
    cast = service(tmp_path, FakeRunner(process=process), hooks=hooks)
    cast.start(REQUEST)
    assert hooks.registered == [cast.close]

    hooks.registered[0]()  # what `atexit` would run

    assert process.signals == ["TERM"] and hooks.registered == []
    assert list(tmp_path.iterdir()) == []


def test_the_default_session_directory_is_private_and_temporary() -> None:
    workdir = service_module._temporary_workdir()
    try:
        assert workdir.is_dir() and workdir.name.startswith("control-tv-screencast-")
        assert workdir.stat().st_mode & 0o777 == 0o700
    finally:
        workdir.rmdir()


def test_the_default_exit_hooks_register_and_unregister() -> None:
    calls: list[str] = []

    def callback() -> None:
        calls.append("ran")

    service_module._register_at_exit(callback)
    service_module._unregister_at_exit(callback)
    assert calls == []


def test_a_failure_without_stderr_still_reports_its_status(tmp_path: Path) -> None:
    runner = FakeRunner(process=FakeProcess(status=2))
    cast = service(tmp_path, runner)
    cast.start(REQUEST)
    (Path(runner.argv[0][-1]).parent / STDERR_NAME).unlink()

    with pytest.raises(EncodeFailedError, match=r"^the encoder exited with status 2$"):
        cast.wait(1.0)
