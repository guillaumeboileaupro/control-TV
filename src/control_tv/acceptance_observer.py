"""The independent, read-only observer of the C6 acceptance step (docs/COMMAND_TRACE.md).

It runs as its own process with the `observer` role and its own trace. When a remote holds a
sent command's confirmation (`trace_files.FileConfirmationGate`), the observer:

1. records `observer_hold_seen` with the session it reads then; if that is already not the
   captured session, the switch may precede the send, so it records
   `observer_session_already_changed` and never releases;
2. reads the receiver status (read-only) until another session is reported, and records it as
   `independent_session_change`;
3. releases the hold with a random token and records `observer_release_sent` with that token.

The remote records the same token in `confirmation_released`: that link, not any clock, proves
the observer's evidence came after the send and before the confirmation. Every wait is bounded.
The observer never sends a command.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from control_tv.command_trace import TraceConfigurationError, TraceRecorder, TraceRole
from control_tv.domain import ControlError
from control_tv.trace_files import HeldCommand, held_commands, release_command

# Reads the receiver's current media session number (None when no media reports one).
SessionReader = Callable[[], int | None]


class ObserverOutcome(StrEnum):
    RELEASED = "released"
    CHANGED_BEFORE_HOLD = "changed_before_hold"
    TIMEOUT = "timeout"


@dataclass(frozen=True, slots=True)
class _Reading:
    """A session read in time (`session` may be None: no media reports one)."""

    session: int | None


class IndependentObserver:
    def __init__(
        self,
        gate_directory: Path,
        read_session: SessionReader,
        recorder: TraceRecorder,
        *,
        timeout_seconds: float = 60.0,
        poll_seconds: float = 0.1,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        token_factory: Callable[[], str] = lambda: secrets.token_hex(16),
        on_hold_seen: Callable[[], None] = lambda: None,
    ) -> None:
        if recorder.role is not TraceRole.OBSERVER or not recorder.active:
            raise TraceConfigurationError("the observer needs an active trace with its own role")
        self._directory = gate_directory
        self._read_session = read_session
        self._recorder = recorder
        self._timeout = timeout_seconds
        self._poll = poll_seconds
        self._clock = clock
        self._sleep = sleep
        self._token_factory = token_factory
        self._on_hold_seen = on_hold_seen  # tells the owner to switch the media now

    def observe(self) -> ObserverOutcome:
        """Observe one held command, within `timeout_seconds` overall."""
        deadline = self._clock() + self._timeout
        held = self._wait_for_hold(deadline)
        if held is None:
            return ObserverOutcome.TIMEOUT
        request_id = held.request_id
        seen = self._read_until(deadline, request_id, lambda session: True)
        if seen is None:
            return self._timed_out(request_id)
        self._recorder.observation(
            "observer_hold_seen", request_id=request_id, media_session_id=seen.session
        )
        if seen.session != held.media_session_id:
            self._recorder.observation(
                "observer_session_already_changed",
                request_id=request_id,
                media_session_id=seen.session,
            )
            return ObserverOutcome.CHANGED_BEFORE_HOLD
        self._on_hold_seen()
        changed = self._read_until(
            deadline,
            request_id,
            lambda session: session is not None and session != held.media_session_id,
        )
        if changed is None:
            return self._timed_out(request_id)
        self._recorder.observation(
            "independent_session_change", request_id=request_id, media_session_id=changed.session
        )
        token = self._token_factory()
        release_command(self._directory, request_id, token)
        self._recorder.observation("observer_release_sent", request_id=request_id, token=token)
        return ObserverOutcome.RELEASED

    def _wait_for_hold(self, deadline: float) -> HeldCommand | None:
        while self._clock() < deadline:
            held = held_commands(self._directory)
            if held:
                return held[0]
            self._sleep(self._poll)
        return None

    def _read_until(
        self, deadline: float, request_id: str, accept: Callable[[int | None], bool]
    ) -> _Reading | None:
        """The first accepted reading before `deadline`, or None (read errors are recorded)."""
        while self._clock() < deadline:
            try:
                session = self._read_session()
            except ControlError:
                self._recorder.observation("observer_read_failed", request_id=request_id)
            else:
                if accept(session):
                    return _Reading(session)
            self._sleep(self._poll)
        return None

    def _timed_out(self, request_id: str) -> ObserverOutcome:
        self._recorder.observation("observer_timeout", request_id=request_id)
        return ObserverOutcome.TIMEOUT
