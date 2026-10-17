"""ScreenCastService (D0): one bounded, local encode at a time, of a synthetic picture only.

States: idle -> starting -> running -> (stopping ->) stopped, or failed; `stopping` is transient
and never outlives a call. The service owns one session at a time (a private 0700 directory, the
encoder's process group and the chosen encoder). `close()` (or leaving the `with` block) always
stops the encoder and removes the directory, and never raises.

An encode is `stopped` with a result only when its group is gone, the leader exited 0, its output
is a non-empty regular file under the size limit and FFmpeg logged no error; anything else is
`failed`, whether `wait()` or `stop()` noticed the end.

A process group that survives SIGKILL (for example blocked in a GPU driver) is **not** claimed
stopped: the session fails, the error names the PID, and the group is kept, unreaped (its id stays
reserved), in `unstoppable_pids`. `reclaim()`, `start()` and `close()` poll it again and release
it once the kernel lets it go; while one is kept, no new encode starts. If the kernel never lets it
go, it stays listed until the application exits (then the system reaps it when it ends).

Concurrency: the service serves **one call at a time**. A call made while another one runs (from
another thread) is refused with `ScreenCastStateError`; `close()` then does nothing and keeps its
interpreter-exit hook so that exit still stops the encoder.

No screen is captured, no network port is opened and no device is contacted. The D0 restriction
to the synthetic source is enforced on the request (exact source type) and again here, on a copy
rebuilt from plain values, before any probe, directory or process.
"""

from __future__ import annotations

import atexit
import contextlib
import math
import os
import shutil
import signal
import stat
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

from control_tv.screencast.capabilities import CapabilityProbe, select_encoder
from control_tv.screencast.ffmpeg import encode_command
from control_tv.screencast.model import (
    MAX_OUTPUT_BYTES,
    EncodeFailedError,
    EncoderChoice,
    EncodeRequest,
    EncodeResult,
    InvalidScreenCastOption,
    ScreenCastError,
    ScreenCastState,
    ScreenCastStateError,
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
_STDERR_READ_BYTES = 16 * 1024
_MAX_SECONDS = 24 * 3600.0

_STARTABLE = {ScreenCastState.IDLE, ScreenCastState.STOPPED, ScreenCastState.FAILED}


def _require_seconds(name: str, value: float, *, allow_zero: bool) -> None:
    valid = (
        not isinstance(value, bool)
        and isinstance(value, int | float)
        and math.isfinite(value)
        and (value >= 0 if allow_zero else value > 0)
        and value <= _MAX_SECONDS
    )
    if not valid:
        low = "0" if allow_zero else "more than 0"
        raise InvalidScreenCastOption(
            f"{name} must be a finite number of seconds from {low} to {_MAX_SECONDS:g}: {value!r}"
        )


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
        _require_seconds("stop_grace_seconds", stop_grace_seconds, allow_zero=True)
        self._runner = runner
        self._probe = probe
        self._ffmpeg = ffmpeg
        self._grace = float(stop_grace_seconds)
        self._clock = clock
        self._make_workdir = make_workdir
        self._on_exit = on_exit
        self._forget_on_exit = forget_on_exit
        self._busy = threading.Lock()
        self._state = ScreenCastState.IDLE
        self._session: _Session | None = None
        self._unreclaimed: list[ManagedProcess] = []
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
        """Process groups that survived SIGKILL and are kept, unreaped, for `reclaim()`."""
        return tuple(process.pid for process in self._unreclaimed)

    # Lifecycle -------------------------------------------------------------------------------

    def reclaim(self) -> tuple[int, ...]:
        """Polls the kept process groups again, releases those that have ended and returns the
        PIDs still kept."""
        with self._exclusive():
            self._reclaim()
            return self.unstoppable_pids

    def start(self, request: EncodeRequest) -> EncoderChoice:
        """Starts one encode. On any failure the service is `failed` and nothing is left behind.
        Only `ScreenCastError` escapes (an interruption such as Ctrl-C is cleaned, then re-raised).
        """
        with self._exclusive():
            if self._state not in _STARTABLE:
                raise ScreenCastStateError(f"cannot start while {self._state.value}")
            self._end_session()
            self.last_error = None
            self._reclaim()
            if self._unreclaimed:
                pids = ", ".join(str(pid) for pid in self.unstoppable_pids)
                raise self._failed(
                    EncodeFailedError(
                        f"an earlier encoder (pid {pids}) still runs after SIGKILL; no new encode "
                        "starts until it has ended (it may need a restart of the machine)"
                    )
                )
            self._state = ScreenCastState.STARTING
            try:
                choice = self._launch(request)
            except ScreenCastError as error:
                raise self._failed(error, halt=True) from None
            except Exception as error:
                raise self._failed(
                    EncodeFailedError(f"the encode could not start: {type(error).__name__}"),
                    halt=True,
                ) from error
            except BaseException:
                self._failed(EncodeFailedError("the start was interrupted"), halt=True)
                raise
            self._state = ScreenCastState.RUNNING
            return choice

    def wait(self, timeout: float) -> EncodeResult:
        """Waits for the running encode. Past `timeout` the encoder is stopped and the session
        fails; see the module for what makes a result. Only `ScreenCastError` escapes."""
        _require_seconds("timeout", timeout, allow_zero=False)
        with self._exclusive():
            session = self._session
            process = session.process if session is not None else None
            if self._state is not ScreenCastState.RUNNING or session is None or process is None:
                raise ScreenCastStateError(f"no running encode (state: {self._state.value})")
            try:
                process.wait_exit(timeout)
            except ProcessTimeout:
                raise self._failed(
                    EncodeFailedError(f"the encode did not finish within {timeout:g} s"),
                    halt=True,
                ) from None
            except OSError as error:
                raise self._failed(
                    EncodeFailedError(
                        f"the encoder could not be waited for: {type(error).__name__}"
                    ),
                    halt=True,
                ) from None
            return self._finish(session, process)

    def stop(self) -> None:
        """Stops a running encode; does nothing in any other state (idempotent).

        An encode that had already ended is judged like `wait()` judges it (`stopped` only if its
        result is valid, `failed` otherwise, raising); an encoder interrupted here is `stopped`;
        a group that survives SIGKILL fails the session and raises `EncodeFailedError`.
        """
        with self._exclusive():
            self._stop()

    def close(self) -> None:
        """Stops any encode, removes the session's directory and polls kept groups again. Never
        raises; if another call is in progress it does nothing (see the module)."""
        if not self._busy.acquire(blocking=False):
            self.last_error = "close() was refused: another screen-cast call is in progress"
            return
        try:
            self._forget_on_exit(self.close)
            with contextlib.suppress(ScreenCastError):
                self._stop()  # a failure is recorded in `last_error` and `unstoppable_pids`
        except Exception as error:
            self.last_error = f"close() met an internal error: {type(error).__name__}"
            self._state = ScreenCastState.FAILED
        finally:
            self._end_session()
            self._reclaim()
            self._busy.release()

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

    @contextlib.contextmanager
    def _exclusive(self) -> Iterator[None]:
        if not self._busy.acquire(blocking=False):
            raise ScreenCastStateError(
                "another screen-cast call is in progress; the service serves one call at a time"
            )
        try:
            yield
        finally:
            self._busy.release()

    def _launch(self, request: EncodeRequest) -> EncoderChoice:
        if not isinstance(request, EncodeRequest):
            raise InvalidScreenCastOption(f"not an encode request: {type(request).__name__}")
        checked = request.revalidated()  # D0 source policy, before any probe or process
        choice = select_encoder(checked, self._probe.probe(checked.device))
        workdir = self._new_workdir()
        self._session = _Session(workdir, choice, None, self._clock())
        argv = encode_command(
            self._ffmpeg, checked.source, checked.profile, choice.plan, workdir / OUTPUT_NAME
        )
        try:
            self._session.process = self._runner.start(argv, stderr_path=workdir / STDERR_NAME)
        except OSError as error:
            raise EncodeFailedError(
                f"the encoder could not start: {type(error).__name__}"
            ) from None
        # A normal interpreter exit stops the encoder too. A parent killed with SIGKILL cannot;
        # the encode then still ends by itself (`-t`, at most MAX_DURATION_SECONDS) and its file
        # cannot pass MAX_OUTPUT_BYTES (`-fs`).
        self._on_exit(self.close)
        return choice

    def _new_workdir(self) -> Path:
        try:
            workdir = self._make_workdir()
        except OSError as error:
            raise EncodeFailedError(
                f"the session directory could not be created: {type(error).__name__}"
            ) from None
        try:
            workdir.chmod(0o700)
        except OSError as error:
            shutil.rmtree(workdir, ignore_errors=True)
            raise EncodeFailedError(
                f"the session directory could not be made private: {type(error).__name__}"
            ) from None
        return workdir

    def _stop(self) -> None:
        session = self._session
        process = session.process if session is not None else None
        if self._state is not ScreenCastState.RUNNING or session is None or process is None:
            return
        if process.exit_status() is not None:
            self._finish(session, process)
            return
        self._state = ScreenCastState.STOPPING
        if self._halt(session, process) is None:
            raise self._failed(self._unstoppable_error(process.pid))
        self._state = ScreenCastState.STOPPED

    def _finish(self, session: _Session, process: ManagedProcess) -> EncodeResult:
        """Judges an encode whose leader has exited: cleans what is left of its group, then
        checks status, output and stderr. `stopped` with a result, or `failed` and raises."""
        status = self._halt(session, process)
        if status is None:
            raise self._failed(self._unstoppable_error(process.pid))
        output = session.workdir / OUTPUT_NAME
        try:
            size = self._regular_file_size(output)
            detail = self._stderr_tail(session)
        except OSError as error:
            raise self._failed(
                EncodeFailedError(f"the encode's files could not be read: {type(error).__name__}")
            ) from None
        problem: str | None = None
        if status != 0:
            problem = f"the encoder exited with status {status}"
        elif size is None:
            problem = "the encoder exited with status 0 but its output is not a regular file"
        elif size == 0:
            problem = "the encoder exited with status 0 but produced no output"
        elif size >= MAX_OUTPUT_BYTES:
            problem = f"the output reached the {MAX_OUTPUT_BYTES} byte limit and was cut"
        elif detail:
            problem = "the encoder exited with status 0 but reported errors"
        if problem is not None or size is None:
            message = problem or "the encode has no output"
            raise self._failed(EncodeFailedError(message + (f": {detail}" if detail else "")))
        self._state = ScreenCastState.STOPPED
        return EncodeResult(
            encoder=session.choice.plan,
            fallback_reason=session.choice.fallback_reason,
            output=output,
            output_bytes=size,
            elapsed_seconds=self._clock() - session.started_at,
        )

    def _halt(self, session: _Session, process: ManagedProcess) -> int | None:
        """Stops the session's process group and returns the leader's status, or None (the group
        kept for `reclaim()`) if it could not be confirmed gone."""
        try:
            status = stop_process(process, self._grace)
        except Exception:
            status = None  # signalling or reading failed: never claim it stopped
        session.process = None
        if status is None:
            self._unreclaimed.append(process)
        return status

    def _reclaim(self) -> None:
        kept: list[ManagedProcess] = []
        for process in self._unreclaimed:
            try:
                if process.group_alive():
                    process.signal_group(signal.SIGKILL)  # still unreaped: the id is ours
                process.release()
                if process.group_alive():
                    kept.append(process)
            except Exception:
                kept.append(process)
        self._unreclaimed = kept

    def _failed(self, error: ScreenCastError, *, halt: bool = False) -> ScreenCastError:
        """Ends the session as failed (stopping its encoder when `halt`) and returns `error`,
        completed with the PID of a group that could not be stopped."""
        session = self._session
        process = session.process if session is not None else None
        if (
            halt
            and session is not None
            and process is not None
            and self._halt(session, process) is None
        ):
            unstoppable = self._unstoppable_error(process.pid)
            error = EncodeFailedError(f"{error.message}; {unstoppable.message}")
        self.last_error = error.message
        self._state = ScreenCastState.FAILED
        self._end_session()
        return error

    def _end_session(self) -> None:
        """Removes the session's directory; its process group has ended or is kept."""
        session = self._session
        if session is None:
            return
        shutil.rmtree(session.workdir, ignore_errors=True)
        if session.workdir.exists():
            note = f"the session directory {session.workdir} could not be removed"
            self.last_error = f"{self.last_error}; {note}" if self.last_error else note
        self._session = None

    @staticmethod
    def _unstoppable_error(pid: int) -> EncodeFailedError:
        return EncodeFailedError(
            f"the encoder (pid {pid}) could not be confirmed stopped after SIGKILL and may still "
            "be running; it is kept for reclaim()"
        )

    @staticmethod
    def _regular_file_size(path: Path) -> int | None:
        """The size of a regular file at `path`, 0 if there is none, None if it is something else
        (a directory, a link, a device)."""
        try:
            info = path.lstat()
        except FileNotFoundError:
            return 0
        return info.st_size if stat.S_ISREG(info.st_mode) else None

    @staticmethod
    def _stderr_tail(session: _Session) -> str:
        path = session.workdir / STDERR_NAME
        try:
            with path.open("rb") as stderr:
                stderr.seek(0, os.SEEK_END)
                stderr.seek(max(0, stderr.tell() - _STDERR_READ_BYTES))
                data = stderr.read()
        except FileNotFoundError:
            return ""
        return data.decode("utf-8", errors="replace").strip()[-_STDERR_TAIL:]
