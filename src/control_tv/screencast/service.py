"""ScreenCastService (D0): one bounded, local encode at a time, of a synthetic picture only.

States: idle -> starting -> running -> (stopping ->) stopped, or failed; `stopping` is transient
and never outlives a call. The service owns one session at a time (a private 0700 directory, the
encoder process and the chosen encoder). `close()` (or leaving the `with` block) always stops the
encoder and removes the directory, and never raises.

An encoder that survives SIGKILL (for example blocked in a GPU driver) is **not** claimed stopped:
the session fails, its PID is kept in `unstoppable_pids`, and the error says so. Its directory is
still removed (a running process keeps any file it has open; nothing else of the session stays).

No screen is captured, no network port is opened and no device is contacted. The D0 restriction
to synthetic sources is enforced here, not in the command builder, so later sources plug in
through the `VideoSource` port without changing the command.
"""

from __future__ import annotations

import atexit
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

from control_tv.screencast.capabilities import CapabilityProbe, select_encoder
from control_tv.screencast.ffmpeg import encode_command
from control_tv.screencast.model import (
    EncodeFailedError,
    EncoderChoice,
    EncodeRequest,
    EncodeResult,
    InvalidScreenCastOption,
    ScreenCastError,
    ScreenCastState,
    ScreenCastStateError,
    SourceKind,
)
from control_tv.screencast.process import (
    ManagedProcess,
    ProcessRunner,
    ProcessTimeout,
    stop_process,
)

OUTPUT_NAME = "encode.mp4"
STDERR_NAME = "ffmpeg-stderr.log"
_STDERR_TAIL = 2000

# D0 encodes the synthetic test picture only (issue #80): no desktop or other capture.
D0_ALLOWED_SOURCES = frozenset({SourceKind.SYNTHETIC})

_STARTABLE = {ScreenCastState.IDLE, ScreenCastState.STOPPED, ScreenCastState.FAILED}


def _temporary_workdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="control-tv-screencast-"))


def _register_at_exit(callback: Callable[[], None]) -> None:
    atexit.register(callback)


def _unregister_at_exit(callback: Callable[[], None]) -> None:
    atexit.unregister(callback)


@dataclass
class _Session:
    workdir: Path
    choice: EncoderChoice
    process: ManagedProcess | None
    started_at: float


class ScreenCastService:
    def __init__(
        self,
        runner: ProcessRunner,
        probe: CapabilityProbe,
        *,
        ffmpeg: str = "ffmpeg",
        stop_grace_seconds: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
        make_workdir: Callable[[], Path] = _temporary_workdir,
        on_exit: Callable[[Callable[[], None]], None] = _register_at_exit,
        forget_on_exit: Callable[[Callable[[], None]], None] = _unregister_at_exit,
    ) -> None:
        self._runner = runner
        self._probe = probe
        self._ffmpeg = ffmpeg
        self._grace = stop_grace_seconds
        self._clock = clock
        self._make_workdir = make_workdir
        self._on_exit = on_exit
        self._forget_on_exit = forget_on_exit
        self._state = ScreenCastState.IDLE
        self._session: _Session | None = None
        self._unstoppable: list[int] = []
        self.last_error: str | None = None

    @property
    def state(self) -> ScreenCastState:
        return self._state

    @property
    def choice(self) -> EncoderChoice | None:
        """The encoder of the current or last session, with its fallback reason if any."""
        return self._session.choice if self._session is not None else None

    @property
    def unstoppable_pids(self) -> tuple[int, ...]:
        """Encoders that survived SIGKILL: still running as far as this service knows."""
        return tuple(self._unstoppable)

    # Lifecycle -------------------------------------------------------------------------------

    def start(self, request: EncodeRequest) -> EncoderChoice:
        """Starts one encode. On any failure the service is `failed` and nothing is left behind."""
        if self._state not in _STARTABLE:
            raise ScreenCastStateError(f"cannot start while {self._state.value}")
        self._end_session()
        self.last_error = None
        if request.source.kind not in D0_ALLOWED_SOURCES:
            raise self._failed(
                InvalidScreenCastOption(
                    f"only the synthetic test source is allowed in D0, not {request.source.kind}"
                )
            )
        self._state = ScreenCastState.STARTING
        try:
            choice = select_encoder(request, self._probe.probe(request.device))
        except ScreenCastError as error:
            raise self._failed(error) from None
        try:
            workdir = self._make_workdir()
            workdir.chmod(0o700)
        except OSError as error:
            raise self._failed(
                EncodeFailedError(
                    f"the session directory could not be created: {type(error).__name__}"
                )
            ) from None
        self._session = _Session(workdir, choice, None, self._clock())
        argv = encode_command(
            self._ffmpeg, request.source, request.profile, choice.plan, workdir / OUTPUT_NAME
        )
        try:
            self._session.process = self._runner.start(argv, stderr_path=workdir / STDERR_NAME)
        except OSError as error:
            raise self._failed(
                EncodeFailedError(f"the encoder could not start: {type(error).__name__}")
            ) from None
        self._state = ScreenCastState.RUNNING
        # A normal interpreter exit stops the encoder too. A parent killed with SIGKILL cannot;
        # the encode then still ends by itself (`-t`, at most MAX_DURATION_SECONDS) and its file
        # cannot pass MAX_OUTPUT_BYTES (`-fs`).
        self._on_exit(self.close)
        return choice

    def wait(self, timeout: float) -> EncodeResult:
        """Waits for the running encode. Past `timeout` the encoder is stopped and the session
        fails; a non-zero exit or an empty output fails it too. Only `ScreenCastError` escapes."""
        session = self._session
        if self._state is not ScreenCastState.RUNNING or session is None or session.process is None:
            raise ScreenCastStateError(f"no running encode (state: {self._state.value})")
        try:
            status = session.process.wait(timeout)
        except ProcessTimeout:
            raise self._failed(
                EncodeFailedError(f"the encode did not finish within {timeout:g} s"),
                halt=True,
            ) from None
        except OSError as error:
            raise self._failed(
                EncodeFailedError(f"the encoder could not be waited for: {type(error).__name__}"),
                halt=True,
            ) from None
        session.process = None
        output = session.workdir / OUTPUT_NAME
        size = output.stat().st_size if output.exists() else 0
        if status != 0 or size == 0:
            raise self._failed(self._exit_error(status, session))
        self._state = ScreenCastState.STOPPED
        return EncodeResult(
            encoder=session.choice.plan,
            fallback_reason=session.choice.fallback_reason,
            output=output,
            output_bytes=size,
            elapsed_seconds=self._clock() - session.started_at,
        )

    def stop(self) -> None:
        """Stops a running encode; does nothing in any other state (idempotent).

        An encode that had already ended in error leaves the service `failed`, not `stopped`;
        an encoder that survives SIGKILL fails the session and raises `EncodeFailedError`.
        """
        session = self._session
        if self._state is not ScreenCastState.RUNNING or session is None or session.process is None:
            return
        ended = session.process.poll()
        if ended is not None:
            session.process = None
            if ended != 0:
                raise self._failed(self._exit_error(ended, session))
            self._state = ScreenCastState.STOPPED
            return
        self._state = ScreenCastState.STOPPING
        if not self._halt(session, session.process):
            raise self._failed(self._unstoppable_error(session))
        self._state = ScreenCastState.STOPPED

    def close(self) -> None:
        """Stops any encode and removes the session's directory. Never raises."""
        self._forget_on_exit(self.close)
        try:
            self.stop()
        except ScreenCastError:
            pass  # recorded in `last_error` (and `unstoppable_pids`); closing goes on
        finally:
            self._end_session()

    def __enter__(self) -> ScreenCastService:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    # Internals ------------------------------------------------------------------------------

    def _halt(self, session: _Session, process: ManagedProcess) -> bool:
        """Stops the session's encoder; False (and its PID recorded) if it survived SIGKILL."""
        pid = process.pid
        try:
            status = stop_process(process, self._grace)
        except OSError:
            status = None
        session.process = None
        if status is None:
            self._unstoppable.append(pid)
            return False
        return True

    def _failed(self, error: ScreenCastError, *, halt: bool = False) -> ScreenCastError:
        """Ends the session as failed (stopping its encoder when `halt`) and returns `error`,
        completed with the PID of an encoder that could not be stopped."""
        session = self._session
        process = session.process if session is not None else None
        if (
            halt
            and session is not None
            and process is not None
            and not self._halt(session, process)
        ):
            error = EncodeFailedError(
                f"{error.message}; {self._unstoppable_error(session).message}"
            )
        self.last_error = error.message
        self._state = ScreenCastState.FAILED
        self._end_session()
        return error

    def _end_session(self) -> None:
        """Removes the session's directory; its encoder has already ended or been reported."""
        session = self._session
        if session is None:
            return
        shutil.rmtree(session.workdir, ignore_errors=True)
        self._session = None

    def _unstoppable_error(self, session: _Session) -> EncodeFailedError:
        pid = self._unstoppable[-1] if self._unstoppable else "unknown"
        return EncodeFailedError(
            f"the encoder (pid {pid}) did not stop after SIGKILL and may still be running"
        )

    def _exit_error(self, status: int, session: _Session) -> EncodeFailedError:
        detail = self._stderr_tail(session)
        return EncodeFailedError(
            f"the encoder exited with status {status}" + (f": {detail}" if detail else "")
        )

    @staticmethod
    def _stderr_tail(session: _Session) -> str:
        path = session.workdir / STDERR_NAME
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8", errors="replace").strip()[-_STDERR_TAIL:]
