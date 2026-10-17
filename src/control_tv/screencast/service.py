"""ScreenCastService (D0): one bounded, local encode of a synthetic picture at a time.

States: idle -> starting -> running -> (stopping ->) stopped, or failed. The service owns a private
working directory per session and the encoder process; `close()` (or leaving the `with` block)
always stops the process and removes the directory, whatever happened before. No screen is
captured, no network port is opened and no device is contacted: the display source, publisher and
receiver of later phases are separate ports, not implemented here.
"""

from __future__ import annotations

import atexit
import shutil
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from types import TracebackType

from control_tv.screencast.capabilities import CapabilityProbe, select_encoder
from control_tv.screencast.ffmpeg import synthetic_encode_command
from control_tv.screencast.model import (
    EncodeFailedError,
    EncoderChoice,
    EncodeResult,
    ScreenCastError,
    ScreenCastState,
    ScreenCastStateError,
    SyntheticEncodeRequest,
)
from control_tv.screencast.process import (
    ManagedProcess,
    ProcessRunner,
    ProcessTimeout,
    stop_process,
)

OUTPUT_NAME = "synthetic.mp4"
STDERR_NAME = "ffmpeg-stderr.log"
_STDERR_TAIL = 2000

_STARTABLE = {ScreenCastState.IDLE, ScreenCastState.STOPPED, ScreenCastState.FAILED}


class ScreenCastService:
    def __init__(
        self,
        runner: ProcessRunner,
        probe: CapabilityProbe,
        *,
        ffmpeg: str = "ffmpeg",
        stop_grace_seconds: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
        make_workdir: Callable[[], Path] = lambda: Path(
            tempfile.mkdtemp(prefix="control-tv-screencast-")
        ),
    ) -> None:
        self._runner = runner
        self._probe = probe
        self._ffmpeg = ffmpeg
        self._grace = stop_grace_seconds
        self._clock = clock
        self._make_workdir = make_workdir
        self._state = ScreenCastState.IDLE
        self._process: ManagedProcess | None = None
        self._workdir: Path | None = None
        self._choice: EncoderChoice | None = None
        self._started_at = 0.0
        self.last_error: str | None = None

    @property
    def state(self) -> ScreenCastState:
        return self._state

    @property
    def choice(self) -> EncoderChoice | None:
        """The encoder of the current or last session, with its fallback reason if any."""
        return self._choice

    # Lifecycle -------------------------------------------------------------------------------

    def start_synthetic(self, request: SyntheticEncodeRequest) -> EncoderChoice:
        """Starts one encode. Fails (and cleans up) without leaving a process or a directory."""
        if self._state not in _STARTABLE:
            raise ScreenCastStateError(f"cannot start while {self._state.value}")
        self._discard_session()
        self._state = ScreenCastState.STARTING
        self.last_error = None
        try:
            choice = select_encoder(request, self._probe.probe(request.device))
            workdir = self._make_workdir()
            workdir.chmod(0o700)
            self._workdir = workdir
            argv = synthetic_encode_command(
                self._ffmpeg, request.source, request.profile, choice.plan, workdir / OUTPUT_NAME
            )
            self._process = self._runner.start(argv, stderr_path=workdir / STDERR_NAME)
        except ScreenCastError as error:
            self._fail(error.message)
            raise
        except OSError as error:
            self._fail(f"the encoder could not start: {type(error).__name__}")
            raise EncodeFailedError(self.last_error or "the encoder could not start") from None
        self._choice = choice
        self._started_at = self._clock()
        self._state = ScreenCastState.RUNNING
        # An interpreter that exits normally stops the encoder too; a killed one cannot, but the
        # encode is bounded by its duration (at most 600 s), so it cannot run on indefinitely.
        atexit.register(self.close)
        return choice

    def wait(self, timeout: float) -> EncodeResult:
        """Waits for the running encode to finish. Past `timeout` the encoder is stopped and the
        session fails; a non-zero exit or an empty output fails it too."""
        if self._state is not ScreenCastState.RUNNING or self._process is None:
            raise ScreenCastStateError(f"no running encode (state: {self._state.value})")
        try:
            status = self._process.wait(timeout)
        except ProcessTimeout:
            self._state = ScreenCastState.STOPPING
            stop_process(self._process, self._grace)
            self._process = None
            self._fail(f"the encode did not finish within {timeout:g} s and was stopped")
            raise EncodeFailedError(self.last_error or "timeout") from None
        self._process = None
        output = self._workdir / OUTPUT_NAME if self._workdir is not None else None
        size = output.stat().st_size if output is not None and output.exists() else 0
        if status != 0 or output is None or size == 0:
            detail = self._stderr_tail()
            self._fail(
                f"the encoder exited with status {status}" + (f": {detail}" if detail else "")
            )
            raise EncodeFailedError(self.last_error or "encode failed")
        choice = self._choice
        if choice is None:
            self._fail("the encode finished without a recorded encoder")
            raise EncodeFailedError(self.last_error or "encode failed")
        self._state = ScreenCastState.STOPPED
        return EncodeResult(
            encoder=choice.plan,
            fallback_reason=choice.fallback_reason,
            output=output,
            output_bytes=size,
            elapsed_seconds=self._clock() - self._started_at,
        )

    def stop(self) -> None:
        """Stops a running encode; does nothing in any other state (idempotent)."""
        if self._state is not ScreenCastState.RUNNING or self._process is None:
            return
        self._state = ScreenCastState.STOPPING
        stop_process(self._process, self._grace)
        self._process = None
        self._state = ScreenCastState.STOPPED

    def close(self) -> None:
        """Stops any encode and removes the session's directory. Always safe to call."""
        atexit.unregister(self.close)
        try:
            self.stop()
        finally:
            self._discard_session()

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

    def _fail(self, message: str) -> None:
        if self._process is not None:
            stop_process(self._process, self._grace)
            self._process = None
        self.last_error = message
        self._state = ScreenCastState.FAILED
        self._remove_workdir()

    def _discard_session(self) -> None:
        if self._process is not None:
            stop_process(self._process, self._grace)
            self._process = None
        self._remove_workdir()

    def _remove_workdir(self) -> None:
        if self._workdir is not None:
            shutil.rmtree(self._workdir, ignore_errors=True)
            self._workdir = None

    def _stderr_tail(self) -> str:
        if self._workdir is None:
            return ""
        path = self._workdir / STDERR_NAME
        if not path.exists():
            return ""
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        return text[-_STDERR_TAIL:]
