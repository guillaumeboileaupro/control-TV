"""The real process runner, exercised with the test interpreter (no FFmpeg, GPU or network)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from control_tv.screencast.process import ProcessTimeout, SubprocessRunner, stop_process


def python(code: str) -> tuple[str, ...]:
    return (sys.executable, "-c", code)


def test_a_finished_process_reports_its_status_and_stderr_goes_to_the_file(tmp_path: Path) -> None:
    stderr = tmp_path / "stderr.log"

    process = SubprocessRunner().start(
        python("import sys; sys.stderr.write('boom'); sys.exit(3)"), stderr_path=stderr
    )

    assert process.wait(10.0) == 3
    assert stderr.read_text() == "boom"
    assert stop_process(process, 1.0) == 3  # already ended: nothing to signal


def test_a_running_process_is_stopped_in_its_own_group(tmp_path: Path) -> None:
    process = SubprocessRunner().start(
        python("import time; time.sleep(60)"), stderr_path=tmp_path / "e.log"
    )
    assert os.getpgid(process.pid) == process.pid  # its own process group, not ours

    with pytest.raises(ProcessTimeout):
        process.wait(0.05)
    status = stop_process(process, 5.0)

    assert status != 0
    assert process.poll() is not None


def test_a_process_ignoring_sigterm_is_killed(tmp_path: Path) -> None:
    ready = tmp_path / "ready"
    process = SubprocessRunner().start(
        python(
            "import signal, time, pathlib; signal.signal(signal.SIGTERM, signal.SIG_IGN);"
            f"pathlib.Path({str(ready)!r}).touch(); time.sleep(60)"
        ),
        stderr_path=tmp_path / "e.log",
    )
    for _ in range(200):
        if ready.exists():
            break
        with pytest.raises(ProcessTimeout):
            process.wait(0.05)

    status = stop_process(process, 0.3)

    assert status == -9
    process.terminate()  # signalling an ended process is a no-op
    process.kill()


class _Unkillable:
    """A process that ignores SIGTERM and survives SIGKILL (as if blocked in a driver)."""

    def __init__(self) -> None:
        self.signals: list[str] = []

    @property
    def pid(self) -> int:
        return 77

    def poll(self) -> int | None:
        return None

    def wait(self, timeout: float) -> int:
        raise ProcessTimeout("still there")

    def terminate(self) -> None:
        self.signals.append("TERM")

    def kill(self) -> None:
        self.signals.append("KILL")


def test_a_process_surviving_sigkill_is_reported_not_raised() -> None:
    process = _Unkillable()

    assert stop_process(process, 0.01) is None
    assert process.signals == ["TERM", "KILL"]
