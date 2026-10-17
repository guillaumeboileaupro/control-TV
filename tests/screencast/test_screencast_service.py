"""ScreenCastService state machine with fake process groups: no FFmpeg, GPU, display or network.

Besides the normal paths, these tests attack the contracts: forged sources and requests, groups
that outlive their leader or SIGKILL, encodes that "succeed" without a valid output, file and
directory errors, invalid durations and concurrent calls.
"""

from __future__ import annotations

import itertools
import math
import shutil
import signal
import threading
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
    SourceKind,
    SyntheticSource,
    VaapiCqp,
    VaapiDevice,
    VideoProfile,
)
from control_tv.screencast.process import ManagedProcess, ProcessTimeout
from control_tv.screencast.service import (
    OUTPUT_NAME,
    STDERR_NAME,
    ScreenCastService,
)

TERM, KILL = signal.SIGTERM, signal.SIGKILL


@dataclass
class FakeProcess:
    """A process group. The leader exits with `status` after `timeouts` waits time out;
    `lingering_child` keeps a member running after the leader exits until it is signalled;
    `survives_sigkill` keeps the group alive whatever it is sent (clear it to let it go)."""

    status: int = 0
    timeouts: int = 0
    ignores_sigterm: bool = False
    survives_sigkill: bool = False
    lingering_child: bool = False
    wait_error: OSError | None = None
    signal_error: OSError | None = None
    alive_error: RuntimeError | None = None
    gate: threading.Event | None = None
    entered: threading.Event = field(default_factory=threading.Event)
    signals: list[signal.Signals] = field(default_factory=list)
    ended: bool = False
    released: bool = False
    signals_after_release: int = 0

    @property
    def pid(self) -> int:
        return 4242

    def exit_status(self) -> int | None:
        return self.status if self.ended else None

    def wait_exit(self, timeout: float) -> int:
        self.entered.set()
        if self.gate is not None:
            self.gate.wait(5.0)
        if self.wait_error is not None:
            raise self.wait_error
        if not self.ended and (self.survives_sigkill or self.timeouts > 0):
            self.timeouts -= 1
            raise ProcessTimeout("still running")
        self.ended = True
        return self.status

    def group_alive(self) -> bool:
        if self.alive_error is not None:
            raise self.alive_error
        if self.released:
            return False
        return self.survives_sigkill or not self.ended or self.lingering_child

    def signal_group(self, sig: signal.Signals) -> None:
        if self.signal_error is not None:
            raise self.signal_error
        if self.released:
            self.signals_after_release += 1
            return
        self.signals.append(sig)
        if self.survives_sigkill or (sig is TERM and self.ignores_sigterm):
            return
        self.lingering_child = False
        if not self.ended:
            self.ended, self.status = True, -sig

    def release(self) -> int | None:
        if self.group_alive():
            return None
        self.released = True
        return self.status


@dataclass
class FakeRunner:
    process: FakeProcess = field(default_factory=FakeProcess)
    output: bytes = b"\x00\x00\x00\x18ftypisom"
    stderr: str = ""
    error: BaseException | None = None
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
    error: Exception | None = None
    calls: int = 0

    def probe(self, device: VaapiDevice) -> EncoderCapabilities:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.capabilities


@dataclass
class ExitHooks:
    """Records the interpreter-exit hooks instead of registering real `atexit` callbacks."""

    registered: list[Callable[[], None]] = field(default_factory=list)
    error: Exception | None = None

    def register(self, callback: Callable[[], None]) -> None:
        if self.error is not None:
            raise self.error
        self.registered.append(callback)

    def forget(self, callback: Callable[[], None]) -> None:
        if callback in self.registered:
            self.registered.remove(callback)


def pids(cast: ScreenCastService) -> tuple[int, ...]:
    """Reads the kept PIDs through a call, so each check sees the current value."""
    return cast.unstoppable_pids


def state(cast: ScreenCastService) -> ScreenCastState:
    """Reads the state through a call, so each check sees the current value."""
    return cast.state


REQUEST = EncodeRequest(
    SyntheticSource(), VideoProfile(duration_seconds=2), EncoderPreference.VAAPI_THEN_SOFTWARE
)


def service(
    tmp_path: Path,
    runner: FakeRunner,
    probe: FakeProbe | None = None,
    hooks: ExitHooks | None = None,
) -> ScreenCastService:
    exit_hooks = hooks or ExitHooks()
    ticks = itertools.chain([100.0, 102.5], itertools.count(103.0))
    counter = itertools.count()

    def workdir() -> Path:
        path = tmp_path / f"session-{next(counter)}"
        path.mkdir()
        return path

    return ScreenCastService(
        runner,
        probe or FakeProbe(),
        stop_grace_seconds=0.05,
        clock=lambda: next(ticks),
        make_workdir=workdir,
        on_exit=exit_hooks.register,
        forget_on_exit=exit_hooks.forget,
    )


def nothing_left(tmp_path: Path) -> bool:
    return list(tmp_path.iterdir()) == []


# Normal paths --------------------------------------------------------------------------------


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
    assert runner.process.released
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
    assert runner.argv == [] and nothing_left(tmp_path)


def test_a_process_that_cannot_start_leaves_nothing_behind(tmp_path: Path) -> None:
    cast = service(tmp_path, FakeRunner(error=FileNotFoundError("ffmpeg")))

    with pytest.raises(EncodeFailedError, match="could not start: FileNotFoundError"):
        cast.start(REQUEST)

    assert state(cast) is ScreenCastState.FAILED and nothing_left(tmp_path)


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

    assert state(cast) is ScreenCastState.FAILED and nothing_left(tmp_path)


def test_a_failure_without_stderr_still_reports_its_status(tmp_path: Path) -> None:
    runner = FakeRunner(process=FakeProcess(status=2))
    cast = service(tmp_path, runner)
    cast.start(REQUEST)
    (Path(runner.argv[0][-1]).parent / STDERR_NAME).unlink()

    with pytest.raises(EncodeFailedError, match=r"^the encoder exited with status 2$"):
        cast.wait(1.0)


def test_a_timeout_terminates_then_kills_and_fails(tmp_path: Path) -> None:
    process = FakeProcess(timeouts=2, ignores_sigterm=True)
    cast = service(tmp_path, FakeRunner(process=process))
    cast.start(REQUEST)

    with pytest.raises(EncodeFailedError, match="did not finish within 1 s"):
        cast.wait(1.0)

    assert process.signals == [TERM, KILL]
    assert state(cast) is ScreenCastState.FAILED and nothing_left(tmp_path)


def test_stop_is_idempotent_and_close_always_cleans(tmp_path: Path) -> None:
    process = FakeProcess(timeouts=10)
    cast = service(tmp_path, FakeRunner(process=process))
    cast.stop()  # idle: nothing to do
    cast.start(REQUEST)

    cast.stop()
    cast.stop()

    assert state(cast) is ScreenCastState.STOPPED
    assert process.signals == [TERM] and process.released
    cast.close()
    cast.close()
    assert nothing_left(tmp_path)


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

    assert process.signals == [TERM] and nothing_left(tmp_path)


# P1-1: the whole group is cleaned, never only its leader -----------------------------------


@pytest.mark.parametrize("finish", ["wait", "stop"])
def test_a_member_left_after_the_leader_exits_is_stopped_before_the_result(
    tmp_path: Path, finish: str
) -> None:
    process = FakeProcess(lingering_child=True)
    cast = service(tmp_path, FakeRunner(process=process))
    cast.start(REQUEST)
    process.ended = True  # the leader exited 0, a descendant still runs

    if finish == "wait":
        cast.wait(1.0)
    else:
        cast.stop()

    assert process.signals == [TERM] and process.released
    assert state(cast) is ScreenCastState.STOPPED


def test_no_signal_reaches_a_group_once_released(tmp_path: Path) -> None:
    process = FakeProcess()
    cast = service(tmp_path, FakeRunner(process=process))
    cast.start(REQUEST)
    cast.wait(1.0)

    cast.stop()
    cast.close()
    cast.reclaim()

    assert process.released and process.signals == [] and process.signals_after_release == 0


# P1-2: no source other than the validated synthetic one, before any probe or process -------


class LookAlikeSource:
    @property
    def kind(self) -> SourceKind:
        return SourceKind.SYNTHETIC

    def input_arguments(self, profile: VideoProfile) -> tuple[str, ...]:
        return ("-f", "x11grab", "-i", ":0.0")


def forged_request(name: str, value: object) -> EncodeRequest:
    request = EncodeRequest(SyntheticSource(), VideoProfile(), EncoderPreference.SOFTWARE_ONLY)
    object.__setattr__(request, name, value)
    return request


def forged_pattern(pattern: str) -> SyntheticSource:
    source = SyntheticSource()
    object.__setattr__(source, "pattern", pattern)
    return source


@pytest.mark.parametrize(
    "request_",
    [
        forged_request("source", LookAlikeSource()),
        forged_request("source", forged_pattern("x11grab")),
        forged_request("source", forged_pattern("movie=/home/user/video.mp4")),
        forged_request("preference", "anything"),
        "not a request",
    ],
    ids=["look-alike", "x11grab-pattern", "file-pattern", "preference", "not-a-request"],
)
def test_a_forged_request_is_refused_before_any_probe_or_process(
    tmp_path: Path, request_: EncodeRequest
) -> None:
    runner, probe = FakeRunner(), FakeProbe()
    cast = service(tmp_path, runner, probe)

    with pytest.raises(InvalidScreenCastOption):
        cast.start(request_)

    assert state(cast) is ScreenCastState.FAILED
    assert probe.calls == 0 and runner.argv == [] and nothing_left(tmp_path)


def test_the_command_is_built_from_the_rebuilt_request(tmp_path: Path) -> None:
    runner = FakeRunner()
    cast = service(tmp_path, runner)

    cast.start(REQUEST)

    assert "testsrc2=size=1920x1080:rate=30" in runner.argv[0]
    assert "x11grab" not in runner.argv[0]
    cast.close()


# P1-3: groups that survive SIGKILL are kept, polled again and released -------------------


def test_a_group_surviving_sigkill_on_stop_is_kept_and_reported(tmp_path: Path) -> None:
    process = FakeProcess(survives_sigkill=True)
    hooks = ExitHooks()
    cast = service(tmp_path, FakeRunner(process=process), hooks=hooks)
    cast.start(REQUEST)

    with pytest.raises(EncodeFailedError, match=r"pid 4242\) could not be confirmed stopped"):
        cast.stop()

    assert process.signals == [TERM, KILL] and not process.released
    assert state(cast) is ScreenCastState.FAILED
    assert cast.unstoppable_pids == (4242,)
    assert nothing_left(tmp_path)  # recoverable resources are still removed
    cast.close()  # never raises; the group stays kept while it lives
    assert cast.unstoppable_pids == (4242,) and hooks.registered == []


def test_a_kept_group_blocks_new_encodes_until_the_kernel_lets_it_go(tmp_path: Path) -> None:
    process = FakeProcess(survives_sigkill=True)
    runner, probe = FakeRunner(process=process), FakeProbe()
    cast = service(tmp_path, runner, probe)
    cast.start(REQUEST)
    with pytest.raises(EncodeFailedError):
        cast.stop()

    with pytest.raises(EncodeFailedError, match=r"earlier encoder \(pid 4242\) still runs"):
        cast.start(REQUEST)
    assert probe.calls == 1 and len(runner.argv) == 1
    assert cast.reclaim() == (4242,)

    process.survives_sigkill = False  # the kernel finally ends it
    assert cast.reclaim() == ()
    assert process.released and process.signals[-1] is KILL
    runner.process = FakeProcess()
    cast.start(REQUEST)
    assert state(cast) is ScreenCastState.RUNNING
    cast.close()


def test_close_and_start_poll_kept_groups_again(tmp_path: Path) -> None:
    process = FakeProcess(survives_sigkill=True)
    runner = FakeRunner(process=process)
    cast = service(tmp_path, runner)
    cast.start(REQUEST)
    cast.close()
    assert cast.unstoppable_pids == (4242,) and state(cast) is ScreenCastState.FAILED

    process.survives_sigkill = False
    cast.close()
    assert pids(cast) == ()


@dataclass
class EndsRightAfterRelease(FakeProcess):
    """A kept group that ends just after `release()` found it alive (the reclaim race)."""

    race: bool = False

    def release(self) -> int | None:
        result = super().release()
        if self.race and result is None:
            self.race, self.survives_sigkill, self.ended = False, False, True
        return result


def test_reclaim_keeps_a_handle_whose_group_ends_right_after_release(tmp_path: Path) -> None:
    process = EndsRightAfterRelease(survives_sigkill=True)
    cast = service(tmp_path, FakeRunner(process=process))
    cast.start(REQUEST)
    cast.close()
    process.race = True

    assert cast.reclaim() == (4242,)  # not released: the handle must not be dropped
    assert not process.released

    assert cast.reclaim() == ()  # the next poll reaps it
    assert process.released


def test_a_kept_group_whose_state_cannot_be_read_stays_kept(tmp_path: Path) -> None:
    process = FakeProcess(survives_sigkill=True)
    cast = service(tmp_path, FakeRunner(process=process))
    cast.start(REQUEST)
    cast.close()

    process.alive_error = RuntimeError("proc unreadable")
    assert cast.reclaim() == (4242,)


def test_a_group_surviving_sigkill_after_a_timeout_says_both(tmp_path: Path) -> None:
    cast = service(tmp_path, FakeRunner(process=FakeProcess(survives_sigkill=True)))
    cast.start(REQUEST)

    with pytest.raises(EncodeFailedError, match=r"within 1 s; the encoder .* SIGKILL"):
        cast.wait(1.0)

    assert state(cast) is ScreenCastState.FAILED and cast.unstoppable_pids == (4242,)
    assert nothing_left(tmp_path)


@pytest.mark.parametrize("finish", ["wait", "stop"])
def test_a_descendant_surviving_sigkill_after_a_clean_exit_is_no_success(
    tmp_path: Path, finish: str
) -> None:
    process = FakeProcess(survives_sigkill=True)
    cast = service(tmp_path, FakeRunner(process=process))
    cast.start(REQUEST)
    process.ended = True  # the leader exited 0; a member of its group cannot be killed

    with pytest.raises(EncodeFailedError, match="could not be confirmed stopped"):
        if finish == "wait":
            cast.wait(1.0)
        else:
            cast.stop()

    assert state(cast) is ScreenCastState.FAILED and pids(cast) == (4242,)
    assert nothing_left(tmp_path)


def test_a_signal_error_keeps_the_group_rather_than_claiming_it_stopped(tmp_path: Path) -> None:
    process = FakeProcess(timeouts=10, signal_error=PermissionError("not allowed"))
    cast = service(tmp_path, FakeRunner(process=process))
    cast.start(REQUEST)

    with pytest.raises(EncodeFailedError, match="could not be confirmed stopped"):
        cast.stop()
    assert state(cast) is ScreenCastState.FAILED and cast.unstoppable_pids == (4242,)


# P2-1: stopped only with a valid result, whether wait() or stop() sees the end -------------


def end_on_its_own(tmp_path: Path, runner: FakeRunner) -> ScreenCastService:
    cast = service(tmp_path, runner)
    cast.start(REQUEST)
    runner.process.ended = True
    return cast


def finish_with(cast: ScreenCastService, finish: str) -> None:
    if finish == "wait":
        cast.wait(1.0)
    else:
        cast.stop()


@pytest.mark.parametrize("finish", ["wait", "stop"])
@pytest.mark.parametrize(
    ("runner", "message"),
    [
        (FakeRunner(output=b""), "status 0 but produced no output"),
        (FakeRunner(stderr="Invalid data found"), "status 0 but reported errors: Invalid data"),
        (FakeRunner(process=FakeProcess(status=1), stderr="boom"), "status 1: boom"),
    ],
    ids=["missing-output", "stderr-errors", "non-zero"],
)
def test_an_ended_encode_without_a_valid_result_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, finish: str, runner: FakeRunner, message: str
) -> None:
    runner = FakeRunner(
        process=FakeProcess(status=runner.process.status),
        output=runner.output,
        stderr=runner.stderr,
    )
    cast = end_on_its_own(tmp_path, runner)

    with pytest.raises(EncodeFailedError, match=message):
        finish_with(cast, finish)

    assert state(cast) is ScreenCastState.FAILED and nothing_left(tmp_path)


@pytest.mark.parametrize("finish", ["wait", "stop"])
def test_an_empty_output_file_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, finish: str
) -> None:
    runner = FakeRunner()
    cast = end_on_its_own(tmp_path, runner)
    Path(runner.argv[0][-1]).write_bytes(b"")

    with pytest.raises(EncodeFailedError, match="produced no output"):
        finish_with(cast, finish)


@pytest.mark.parametrize("finish", ["wait", "stop"])
def test_an_output_that_is_not_a_regular_file_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, finish: str
) -> None:
    runner = FakeRunner(output=b"")
    cast = end_on_its_own(tmp_path, runner)
    output = Path(runner.argv[0][-1])
    (tmp_path / "elsewhere").write_bytes(b"not the encode")
    output.symlink_to(tmp_path / "elsewhere")

    with pytest.raises(EncodeFailedError, match="not a regular file"):
        finish_with(cast, finish)


@pytest.mark.parametrize("finish", ["wait", "stop"])
def test_an_output_at_the_size_limit_was_cut_and_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, finish: str
) -> None:
    monkeypatch.setattr(service_module, "MAX_OUTPUT_BYTES", 4)
    cast = end_on_its_own(tmp_path, FakeRunner(output=b"12345"))

    with pytest.raises(EncodeFailedError, match="reached the 4 byte limit"):
        finish_with(cast, finish)


# P2-2: exceptions become ScreenCastError and every resource is cleaned -------------------


def test_a_session_directory_that_cannot_be_created_fails_cleanly(tmp_path: Path) -> None:
    runner = FakeRunner()

    def no_directory() -> Path:
        raise PermissionError("read-only")

    cast = ScreenCastService(runner, FakeProbe(), make_workdir=no_directory)

    with pytest.raises(EncodeFailedError, match="could not be created: PermissionError"):
        cast.start(REQUEST)

    assert state(cast) is ScreenCastState.FAILED and runner.argv == []


def test_a_directory_that_cannot_be_made_private_is_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = FakeRunner()
    cast = service(tmp_path, runner)

    def refuse(self: Path, mode: int) -> None:
        raise PermissionError("chmod refused")

    monkeypatch.setattr(Path, "chmod", refuse)
    with pytest.raises(EncodeFailedError, match="could not be made private: PermissionError"):
        cast.start(REQUEST)

    assert nothing_left(tmp_path) and runner.argv == []


def test_an_unreadable_output_fails_and_cleans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cast = service(tmp_path, FakeRunner())
    cast.start(REQUEST)

    def unreadable(path: Path) -> int | None:
        raise PermissionError("stat refused")

    monkeypatch.setattr(ScreenCastService, "_regular_file_size", staticmethod(unreadable))
    with pytest.raises(EncodeFailedError, match="files could not be read: PermissionError"):
        cast.wait(1.0)
    assert state(cast) is ScreenCastState.FAILED and nothing_left(tmp_path)


def test_an_unreadable_stderr_fails_and_cleans(tmp_path: Path) -> None:
    runner = FakeRunner()
    cast = service(tmp_path, runner)
    cast.start(REQUEST)
    stderr = Path(runner.argv[0][-1]).parent / STDERR_NAME
    stderr.unlink()
    stderr.mkdir()

    with pytest.raises(EncodeFailedError, match="files could not be read: IsADirectoryError"):
        cast.wait(1.0)
    assert state(cast) is ScreenCastState.FAILED and nothing_left(tmp_path)


def test_a_command_that_cannot_be_built_fails_before_any_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = FakeRunner()
    cast = service(tmp_path, runner)

    def refuse(*args: object) -> tuple[str, ...]:
        raise InvalidScreenCastOption("no arguments for this source")

    monkeypatch.setattr(service_module, "encode_command", refuse)
    with pytest.raises(InvalidScreenCastOption, match="no arguments"):
        cast.start(REQUEST)
    assert runner.argv == [] and nothing_left(tmp_path)


def test_an_internal_error_becomes_an_encode_failure(tmp_path: Path) -> None:
    cast = service(tmp_path, FakeRunner(), FakeProbe(error=RuntimeError("bug")))

    with pytest.raises(EncodeFailedError, match="could not start: RuntimeError"):
        cast.start(REQUEST)
    assert state(cast) is ScreenCastState.FAILED and nothing_left(tmp_path)


def test_an_exit_hook_that_fails_stops_the_started_encoder(tmp_path: Path) -> None:
    process = FakeProcess(timeouts=10)
    hooks = ExitHooks(error=RuntimeError("no atexit"))
    cast = service(tmp_path, FakeRunner(process=process), hooks=hooks)

    with pytest.raises(EncodeFailedError, match="could not start: RuntimeError"):
        cast.start(REQUEST)
    assert process.signals == [TERM] and process.released and nothing_left(tmp_path)


def test_an_interrupted_start_cleans_then_re_raises(tmp_path: Path) -> None:
    cast = service(tmp_path, FakeRunner(error=KeyboardInterrupt()))

    with pytest.raises(KeyboardInterrupt):
        cast.start(REQUEST)
    assert state(cast) is ScreenCastState.FAILED and nothing_left(tmp_path)
    assert cast.last_error == "the start was interrupted"


def test_waiting_that_fails_with_an_os_error_becomes_an_encode_failure(tmp_path: Path) -> None:
    process = FakeProcess(timeouts=10, wait_error=ChildProcessError("gone"))
    cast = service(tmp_path, FakeRunner(process=process))
    cast.start(REQUEST)

    with pytest.raises(EncodeFailedError, match="could not be waited for: ChildProcessError"):
        cast.wait(1.0)
    assert state(cast) is ScreenCastState.FAILED and nothing_left(tmp_path)


def broken_hook(callback: Callable[[], None]) -> None:
    raise RuntimeError("atexit broken")


def test_close_stops_the_encoder_even_if_its_exit_hook_cannot_be_removed(tmp_path: Path) -> None:
    process = FakeProcess(timeouts=10)
    cast = service(tmp_path, FakeRunner(process=process))
    cast.start(REQUEST)
    cast._forget_on_exit = broken_hook

    cast.close()

    assert process.signals == [TERM] and process.released  # stopped, not leaked
    assert state(cast) is ScreenCastState.STOPPED and nothing_left(tmp_path)
    assert cast.last_error == "close() could not remove its exit hook: RuntimeError"


def test_close_keeps_an_unkillable_encoder_even_if_its_exit_hook_cannot_be_removed(
    tmp_path: Path,
) -> None:
    process = FakeProcess(survives_sigkill=True)
    cast = service(tmp_path, FakeRunner(process=process))
    cast.start(REQUEST)
    cast._forget_on_exit = broken_hook

    cast.close()

    assert process.signals[:2] == [TERM, KILL] and not process.released
    assert pids(cast) == (4242,) and state(cast) is ScreenCastState.FAILED
    assert cast.last_error is not None
    assert "could not be confirmed stopped" in cast.last_error
    assert "could not remove its exit hook" in cast.last_error


def test_close_survives_an_internal_error_while_stopping(tmp_path: Path) -> None:
    process = FakeProcess(timeouts=10)
    cast = service(tmp_path, FakeRunner(process=process))
    cast.start(REQUEST)

    def broken_stop() -> None:
        raise RuntimeError("bug")

    cast._stop = broken_stop  # type: ignore[method-assign]
    cast.close()

    assert state(cast) is ScreenCastState.FAILED and nothing_left(tmp_path)
    assert cast.last_error == "close() met an internal error: RuntimeError"


def test_a_directory_that_cannot_be_removed_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cast = service(tmp_path, FakeRunner())
    cast.start(REQUEST)
    cast.wait(1.0)
    monkeypatch.setattr(shutil, "rmtree", lambda path, ignore_errors: None)

    cast.close()

    assert cast.last_error is not None and "could not be removed" in cast.last_error


# Durations -------------------------------------------------------------------------------


BAD_SECONDS = [-1.0, math.nan, math.inf, -math.inf, True, 1e9]


@pytest.mark.parametrize("value", BAD_SECONDS)
def test_an_invalid_stop_grace_is_refused(tmp_path: Path, value: float) -> None:
    with pytest.raises(InvalidScreenCastOption, match="stop_grace_seconds"):
        ScreenCastService(FakeRunner(), FakeProbe(), stop_grace_seconds=value)


@pytest.mark.parametrize("value", [*BAD_SECONDS, 0.0])
def test_an_invalid_wait_timeout_is_refused_without_changing_the_encode(
    tmp_path: Path, value: float
) -> None:
    process = FakeProcess(timeouts=10)
    cast = service(tmp_path, FakeRunner(process=process))
    cast.start(REQUEST)

    with pytest.raises(InvalidScreenCastOption, match="timeout"):
        cast.wait(value)

    assert state(cast) is ScreenCastState.RUNNING and process.signals == []
    cast.close()


def test_a_zero_stop_grace_is_allowed(tmp_path: Path) -> None:
    ScreenCastService(FakeRunner(), FakeProbe(), stop_grace_seconds=0)


# Concurrency: one call at a time, others refused -----------------------------------------


def test_a_concurrent_call_is_refused_and_close_defers_to_the_exit_hook(tmp_path: Path) -> None:
    gate = threading.Event()
    process = FakeProcess(gate=gate)
    hooks = ExitHooks()
    cast = service(tmp_path, FakeRunner(process=process), hooks=hooks)
    cast.start(REQUEST)
    results: list[object] = []
    waiter = threading.Thread(target=lambda: results.append(cast.wait(5.0)))
    waiter.start()
    assert process.entered.wait(5.0)

    try:
        for call in (cast.stop, cast.reclaim, lambda: cast.start(REQUEST)):
            with pytest.raises(ScreenCastStateError, match="one call at a time"):
                call()
        cast.close()  # refused too, but never raises
        assert cast.last_error is not None and "refused" in cast.last_error
        assert hooks.registered == [cast.close]  # interpreter exit still stops the encoder
    finally:
        gate.set()
        waiter.join(5.0)

    assert len(results) == 1 and state(cast) is ScreenCastState.STOPPED
    cast.close()
    assert hooks.registered == [] and nothing_left(tmp_path)


# Interpreter exit ------------------------------------------------------------------------


def test_a_running_encode_is_hooked_to_interpreter_exit_until_closed(tmp_path: Path) -> None:
    hooks = ExitHooks()
    process = FakeProcess(timeouts=10)
    cast = service(tmp_path, FakeRunner(process=process), hooks=hooks)
    cast.start(REQUEST)
    assert hooks.registered == [cast.close]

    hooks.registered[0]()  # what `atexit` would run

    assert process.signals == [TERM] and hooks.registered == [] and nothing_left(tmp_path)


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
