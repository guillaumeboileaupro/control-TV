"""The real process runner, exercised with the test interpreter (no FFmpeg, GPU or network).

The group tests start a real leader and a real descendant in the leader's group, so they check
what the kernel does, not what a fake claims.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from control_tv.screencast.process import (
    ManagedProcess,
    ProcessTimeout,
    SubprocessRunner,
    _ProcessGroup,
    group_members_running,
    stop_process,
)


def python(code: str) -> tuple[str, ...]:
    return (sys.executable, "-c", code)


def running(pid: int) -> bool:
    """Whether `pid` is a live (non-zombie) process, read from /proc."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        return False
    return stat[stat.rfind(")") + 2] not in "ZX"


def wait_for(path: Path) -> str:
    deadline = time.monotonic() + 10
    while not path.exists():
        assert time.monotonic() < deadline, "the test process never got ready"
        time.sleep(0.01)
    return path.read_text()


@pytest.fixture
def started() -> Iterator[list[ManagedProcess]]:
    """Process groups a test starts; whatever a failing test leaves is killed at teardown."""
    processes: list[ManagedProcess] = []
    yield processes
    for process in processes:
        process.signal_group(signal.SIGKILL)
        stop_process(process, 2.0)


def start(started: list[ManagedProcess], tmp_path: Path, code: str) -> ManagedProcess:
    process = SubprocessRunner().start(python(code), stderr_path=tmp_path / "e.log")
    started.append(process)
    return process


def descendant_code(ready: Path) -> str:
    """A descendant in the leader's group that ignores SIGTERM and reports its PID."""
    child = (
        "import os, signal, time, pathlib;"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
        f"p = pathlib.Path({str(ready)!r}); t = p.with_suffix('.tmp');"
        "t.write_text(str(os.getpid())); os.replace(t, p); time.sleep(60)"
    )
    return (
        "import subprocess, sys, time, pathlib;"
        f"subprocess.Popen([sys.executable, '-c', {child!r}]);"
        f"p = pathlib.Path({str(ready)!r})\n"
        "while not p.exists(): time.sleep(0.01)\n"
    )


def test_a_finished_process_reports_its_status_and_stderr_goes_to_the_file(
    tmp_path: Path, started: list[ManagedProcess]
) -> None:
    process = start(started, tmp_path, "import sys; sys.stderr.write('boom'); sys.exit(3)")

    assert process.wait_exit(10.0) == 3
    assert (tmp_path / "e.log").read_text() == "boom"
    assert stop_process(process, 1.0) == 3  # already ended: nothing to signal
    assert process.release() == 3 and not process.group_alive()


def test_a_running_process_is_stopped_in_its_own_group(
    tmp_path: Path, started: list[ManagedProcess]
) -> None:
    process = start(started, tmp_path, "import time; time.sleep(60)")
    assert os.getpgid(process.pid) == process.pid  # its own process group, not ours

    with pytest.raises(ProcessTimeout):
        process.wait_exit(0.05)

    assert stop_process(process, 5.0) == -signal.SIGTERM
    assert not running(process.pid)


def test_a_descendant_ignoring_sigterm_is_killed_with_the_group(
    tmp_path: Path, started: list[ManagedProcess]
) -> None:
    ready = tmp_path / "ready"
    process = start(started, tmp_path, descendant_code(ready) + "time.sleep(60)")
    child = int(wait_for(ready))
    assert os.getpgid(child) == process.pid

    status = stop_process(process, 0.5)

    assert status == -signal.SIGTERM  # the leader obeyed SIGTERM ...
    assert not running(child)  # ... and its descendant, which ignored it, got SIGKILL
    assert not process.group_alive()


def test_a_leader_that_exits_keeps_its_group_id_until_its_descendants_are_gone(
    tmp_path: Path, started: list[ManagedProcess]
) -> None:
    ready = tmp_path / "ready"
    process = start(started, tmp_path, descendant_code(ready) + "sys.exit(0)")
    child = int(wait_for(ready))

    assert process.wait_exit(10.0) == 0
    assert process.group_alive() and running(child)
    assert process.release() is None  # not reaped while a member runs ...
    assert Path(f"/proc/{process.pid}").exists()  # ... so the leader's PID stays allocated
    assert os.getpgid(child) == process.pid

    assert stop_process(process, 0.5) == 0  # the leader's own status is kept
    assert not running(child)
    assert not Path(f"/proc/{process.pid}").exists()  # reaped once the group was empty


def test_no_signal_is_sent_to_a_released_group(
    tmp_path: Path, started: list[ManagedProcess], monkeypatch: pytest.MonkeyPatch
) -> None:
    process = start(started, tmp_path, "pass")
    process.wait_exit(10.0)
    assert process.release() == 0
    sent: list[int] = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: sent.append(pgid))

    process.signal_group(signal.SIGKILL)

    assert sent == []  # the id may already belong to another group
    assert stop_process(process, 0.1) == 0


def test_a_leader_reaped_elsewhere_reports_popen_status() -> None:
    popen = subprocess.Popen(python("raise SystemExit(4)"), start_new_session=True)
    popen.wait()  # reaped outside this module
    group = _ProcessGroup(popen, members_running=lambda pgid: False)

    assert group.exit_status() == 4
    assert group.release() == 4 and group.release() == 4


def test_waiting_for_the_exit_times_out_with_an_injected_clock() -> None:
    popen = subprocess.Popen(python("import time; time.sleep(60)"), start_new_session=True)
    ticks = iter([0.0, 0.5, 1.5])
    group = _ProcessGroup(popen, clock=lambda: next(ticks), sleep=lambda seconds: None)
    try:
        with pytest.raises(ProcessTimeout, match="still running"):
            group.wait_exit(1.0)
    finally:
        group.signal_group(signal.SIGKILL)
        popen.wait()


def write_stat(root: Path, pid: str, text: str) -> None:
    (root / pid).mkdir()
    (root / pid / "stat").write_text(text)


def test_group_membership_is_read_from_proc_robustly(tmp_path: Path) -> None:
    write_stat(tmp_path, "10", "10 (odd) name (x) Z 1 77 77")  # a zombie member does not count
    write_stat(tmp_path, "11", "11 (short) S")  # malformed
    write_stat(tmp_path, "12", "12 (x) S 1 notanumber 0")  # malformed
    (tmp_path / "self").mkdir()  # not a PID
    (tmp_path / "13").mkdir()  # no stat (ended meanwhile)
    write_stat(tmp_path, "14", "14 (other) S 1 88 88")  # another group

    assert not group_members_running(77, tmp_path)

    write_stat(tmp_path, "15", "15 (a ) b) R 1 77 77")
    assert group_members_running(77, tmp_path)
    assert group_members_running(77, tmp_path / "missing")  # unknown: never claimed gone


@dataclass
class _Unkillable:
    """A group that survives SIGKILL (as if blocked in a driver)."""

    signals: list[signal.Signals] = field(default_factory=list)
    releases: int = 0

    @property
    def pid(self) -> int:
        return 77

    def exit_status(self) -> int | None:
        return None

    def wait_exit(self, timeout: float) -> int:
        raise ProcessTimeout("still there")

    def group_alive(self) -> bool:
        return True

    def signal_group(self, sig: signal.Signals) -> None:
        self.signals.append(sig)

    def release(self) -> int | None:
        self.releases += 1
        return None


def test_a_group_surviving_sigkill_is_reported_and_not_released() -> None:
    process = _Unkillable()
    ticks = iter(float(n) for n in range(100))

    status = stop_process(process, 0.5, clock=lambda: next(ticks), sleep=lambda seconds: None)

    assert status is None
    assert process.signals == [signal.SIGTERM, signal.SIGKILL]
    assert process.releases == 0  # its id stays reserved for a later reclaim
