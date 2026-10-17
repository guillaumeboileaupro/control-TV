"""Child processes the screen cast owns: started without a shell, each in its own process group,
and stopped as a **group** (terminate, then kill) so neither the encoder nor anything it started
outlives its session.

Why the group id cannot be recycled: the group's id is the leader's PID. The leader is observed
with `waitid(..., WNOWAIT)` and **reaped only once no member of its group is left**. Until then
it stays a zombie, its PID stays allocated, and the kernel cannot hand that number to another
process or group, so a signal to the group can only reach this group. After the reap, no signal
is ever sent to it again.

Limits (Linux): membership is read from `/proc`; a descendant that leaves the group on purpose
(`setsid`, `setpgid`) is no longer covered; a process stuck in the kernel can survive SIGKILL,
and is then reported and kept (unreaped) for a later `release`, never claimed stopped.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

_POLL_SECONDS = 0.02


class ProcessTimeout(Exception):
    """`wait_exit` ran out of time; the process may still be running."""


class ManagedProcess(Protocol):
    """One started process group, identified by its leader."""

    @property
    def pid(self) -> int:
        """The leader's PID, which is also the group id; reserved until `release`."""
        ...

    def exit_status(self) -> int | None:
        """The leader's exit status (negative for a signal), or None while it runs. Does not
        release the leader."""
        ...

    def wait_exit(self, timeout: float) -> int:
        """Waits up to `timeout` seconds for the leader to exit; raises `ProcessTimeout`."""
        ...

    def group_alive(self) -> bool:
        """Whether any member of the group (the leader included) still runs."""
        ...

    def signal_group(self, sig: signal.Signals) -> None:
        """Signals every member of the group; does nothing once the group is released."""
        ...

    def release(self) -> int | None:
        """Reaps the leader when no member is left and returns its status; None (nothing done)
        while any member still runs."""
        ...


class ProcessRunner(Protocol):
    def start(self, argv: Sequence[str], *, stderr_path: Path) -> ManagedProcess: ...


def _status_from_waitid(result: os.waitid_result) -> int:
    if result.si_code == os.CLD_EXITED:
        return result.si_status
    return -result.si_status  # killed or dumped: the signal number, negative like Popen


def group_members_running(pgid: int, proc_root: Path = Path("/proc")) -> bool:
    """Whether a process other than a zombie has `pgid` as its process group (from `/proc`)."""
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return True  # cannot tell: never claim the group is gone
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text(encoding="ascii", errors="replace")
        except OSError:
            continue  # ended meanwhile, or not readable
        # "pid (comm) state ppid pgrp ...": comm may hold spaces and parentheses.
        fields = stat[stat.rfind(")") + 1 :].split()
        if len(fields) < 3 or not fields[2].isdigit():
            continue
        if int(fields[2]) == pgid and fields[0] not in {"Z", "X"}:
            return True
    return False


class _ProcessGroup:
    """A `Popen` started in its own session (group id = its PID)."""

    def __init__(
        self,
        process: subprocess.Popen[bytes],
        *,
        members_running: Callable[[int], bool] = group_members_running,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._process = process
        self._pgid = process.pid
        self._members_running = members_running
        self._clock = clock
        self._sleep = sleep
        self._status: int | None = None
        self._released = False

    @property
    def pid(self) -> int:
        return self._pgid

    def exit_status(self) -> int | None:
        if self._status is None and not self._released:
            try:
                result = os.waitid(os.P_PID, self._pgid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            except ChildProcessError:
                result = None
                self._status = self._process.returncode  # reaped elsewhere (not by this module)
            if result is not None:
                self._status = _status_from_waitid(result)
        return self._status

    def wait_exit(self, timeout: float) -> int:
        deadline = self._clock() + timeout
        while (status := self.exit_status()) is None:
            if self._clock() >= deadline:
                raise ProcessTimeout(f"process {self._pgid} still running")
            self._sleep(_POLL_SECONDS)
        return status

    def group_alive(self) -> bool:
        if self._released:
            return False
        return self.exit_status() is None or self._members_running(self._pgid)

    def signal_group(self, sig: signal.Signals) -> None:
        if self._released:
            return  # the id may belong to someone else now
        with contextlib.suppress(ProcessLookupError):
            os.killpg(self._pgid, sig)

    def release(self) -> int | None:
        if self._released:
            return self._status
        if self.group_alive():
            return None
        self._process.wait()  # reaps the zombie leader: the id is free from here on
        self._released = True
        return self._status


class SubprocessRunner:
    """Starts a command from an argument vector: no shell, no stdin, stdout discarded, stderr to
    a private file (a pipe could fill up and block the encoder), in a new session and group."""

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
        return _ProcessGroup(process)


def _group_ends_within(
    process: ManagedProcess,
    seconds: float,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> bool:
    deadline = clock() + seconds
    while process.group_alive():
        if clock() >= deadline:
            return False
        sleep(_POLL_SECONDS)
    return True


def stop_process(
    process: ManagedProcess,
    grace_seconds: float,
    *,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> int | None:
    """Ends the whole group: SIGTERM, then SIGKILL to what is left after `grace_seconds`, then
    releases the leader. Also cleans descendants left behind by a leader that already exited.

    Returns the leader's exit status, or `None` when a member is still alive after SIGKILL and
    another `grace_seconds` (for example blocked in a driver): the group is then **not** released
    (its id stays reserved) and the caller must keep `process` and say so. Never raises for a slow
    process."""
    if not process.group_alive():
        return process.release()
    for sig in (signal.SIGTERM, signal.SIGKILL):
        process.signal_group(sig)
        if _group_ends_within(process, grace_seconds, clock, sleep):
            return process.release()
    return None
