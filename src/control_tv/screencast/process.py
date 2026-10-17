"""Child processes the screen cast owns: started without a shell, in their own process group,
stopped with escalation (terminate, then kill) so no encoder outlives its session.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol


class ProcessTimeout(Exception):
    """`wait` ran out of time; the process may still be running."""


class ManagedProcess(Protocol):
    @property
    def pid(self) -> int: ...

    def poll(self) -> int | None: ...

    def wait(self, timeout: float) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


class ProcessRunner(Protocol):
    def start(self, argv: Sequence[str], *, stderr_path: Path) -> ManagedProcess: ...


class _PopenProcess:
    """A `Popen` in its own session: signals reach its whole group (FFmpeg and any helper)."""

    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        self._process = process

    @property
    def pid(self) -> int:
        return self._process.pid

    def poll(self) -> int | None:
        return self._process.poll()

    def wait(self, timeout: float) -> int:
        try:
            return self._process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            raise ProcessTimeout(f"process {self._process.pid} still running") from None

    def _signal_group(self, sig: signal.Signals) -> None:
        if self._process.poll() is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(self._process.pid, sig)

    def terminate(self) -> None:
        self._signal_group(signal.SIGTERM)

    def kill(self) -> None:
        self._signal_group(signal.SIGKILL)


class SubprocessRunner:
    """Starts a command from an argument vector: no shell, no stdin, stdout discarded, stderr to
    a private file (a pipe could fill up and block the encoder)."""

    def start(self, argv: Sequence[str], *, stderr_path: Path) -> ManagedProcess:
        with stderr_path.open("wb") as stderr:
            process = subprocess.Popen(
                list(argv),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=stderr,
                start_new_session=True,
                close_fds=True,
            )
        return _PopenProcess(process)


def stop_process(process: ManagedProcess, grace_seconds: float) -> int:
    """Ends `process`: SIGTERM to its group, then SIGKILL if it is still there after
    `grace_seconds`. Returns its exit status. Safe to call on a process that already ended."""
    status = process.poll()
    if status is not None:
        return status
    process.terminate()
    try:
        return process.wait(grace_seconds)
    except ProcessTimeout:
        process.kill()
        return process.wait(grace_seconds)
