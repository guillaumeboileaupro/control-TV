"""Opt-in, privacy-preserving command lifecycle traces for acceptance evidence (C6/C7).

What a trace can prove, and what it cannot. It records **application-side invocations**: that
the product entered the transport once for a request, against which media session, and what it
then read back. It does not prove a network transmission, a reception by the TV or a physical
effect; those need packet or receiver-side capture and the owner's observation.

Integrity. Within one request every event carries a contiguous `index` from 0, and a command
ends with exactly one terminal event (`TERMINAL_EVENTS`). A lost write leaves a gap or no
terminal event, so a validator rejects that request's evidence (fail-closed). After a failed
write the recorder also writes `trace_incomplete` before the request's next event, and the file
sink leaves a `.incomplete` marker beside the file; both are best effort (a full disk can lose
them too): the gap is what makes a loss detectable.

Ordering. `sequence` orders the events of **one** recorder (one process, one client) and is
never compared across clients or devices: their clocks are unrelated. Order across clients is
established only causally, by the confirmation hold's token handshake (`ConfirmationGate`).

Privacy. Events carry an opaque request id, a client role, a media session number, a command
name, an error code and a hold token: never a device id or address, a media URL or title, a
command argument, a credential or exception text.

Tracing never changes a command: a trace failure is swallowed, nothing is resent, and with no
sink configured nothing is recorded at all.
"""

from __future__ import annotations

import contextlib
import re
import threading
import uuid
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from control_tv.domain import Command, ErrorCode

# The events that end a command's trace; exactly one per command.
TERMINAL_EVENTS = frozenset(
    {
        "command_confirmed",
        "command_unconfirmed",
        "confirmation_skipped",
        "ambiguous_result",
        "not_sent",
        "command_rejected",
    }
)
TRACE_INCOMPLETE = "trace_incomplete"

_APPLICATION_REQUEST_ID = re.compile(r"[A-Za-z0-9-]{1,64}")
# The application request the current command belongs to (set by the bridge for each request it
# runs): every invocation, attempt and replay of that request carries it, so C7 can count them.
_APPLICATION_REQUEST: ContextVar[str | None] = ContextVar("application_request", default=None)


def is_application_request_id(value: object) -> bool:
    """Whether `value` can identify an application request (letters, digits, dashes, 1-64)."""
    return isinstance(value, str) and _APPLICATION_REQUEST_ID.fullmatch(value) is not None


@contextlib.contextmanager
def application_request(request_id: str) -> Iterator[None]:
    """Run commands as part of application request `request_id`: each command trace created
    inside records it, so every invocation of one request is correlated across traces."""
    if not is_application_request_id(request_id):
        raise TraceConfigurationError(f"invalid application request id: {request_id!r}")
    token = _APPLICATION_REQUEST.set(request_id)
    try:
        yield
    finally:
        _APPLICATION_REQUEST.reset(token)


class TraceConfigurationError(Exception):
    """Acceptance tracing was requested with an invalid configuration (it is never guessed)."""


class TraceRole(StrEnum):
    """Who wrote a trace: one of the two remotes, or the independent read-only observer."""

    UBUNTU = "ubuntu"
    ANDROID = "android"
    OBSERVER = "observer"


@dataclass(frozen=True, slots=True)
class TraceEvent:
    """One observed application-side phase; never evidence of physical TV behavior."""

    sequence: int
    client: str
    event: str
    request_id: str
    index: int
    media_session_id: int | None = None
    command: str | None = None
    error_code: str | None = None
    token: str | None = None
    # The application request (bridge request) this command belongs to; None outside one.
    application_request_id: str | None = None


class TraceSink(Protocol):
    def write(self, event: TraceEvent) -> None: ...


class MemoryTraceSink:
    """Thread-safe deterministic sink for tests and simulated acceptance."""

    def __init__(self) -> None:
        self._events: list[TraceEvent] = []
        self._lock = threading.Lock()

    def write(self, event: TraceEvent) -> None:
        with self._lock:
            self._events.append(event)

    def events(self) -> tuple[TraceEvent, ...]:
        with self._lock:
            return tuple(self._events)


@dataclass(frozen=True, slots=True)
class HoldRequest:
    """A sent playback command whose confirmation waits for the independent observer."""

    request_id: str
    command: Command
    media_session_id: int


@dataclass(frozen=True, slots=True)
class HoldOutcome:
    """`released` with the observer's token, or not (bounded timeout)."""

    released: bool
    token: str | None = None


class ConfirmationGate(Protocol):
    """Acceptance-only port: hold a sent command's confirmation until an independent observer
    has recorded the session change, for at most a bounded time. Absent in normal use."""

    def hold(self, request: HoldRequest) -> HoldOutcome: ...


@dataclass(frozen=True, slots=True)
class CommandTrace:
    recorder: TraceRecorder
    request_id: str
    command: Command
    application_request_id: str | None = None

    def record(
        self,
        event: str,
        *,
        media_session_id: int | None = None,
        error_code: ErrorCode | None = None,
        token: str | None = None,
    ) -> None:
        self.recorder.record(
            event,
            request_id=self.request_id,
            command=self.command,
            media_session_id=media_session_id,
            error_code=error_code,
            token=token,
            application_request_id=self.application_request_id,
        )


class TraceRecorder:
    """Creates correlated command traces; instrumentation failures are non-functional."""

    def __init__(
        self,
        sink: TraceSink | None = None,
        *,
        role: TraceRole | None = None,
        request_id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
    ) -> None:
        if sink is not None and role is None:
            raise TraceConfigurationError("an active trace needs an explicit client role")
        self._sink = sink
        self._role = role
        self._request_id_factory = request_id_factory
        self._sequence = 0
        self._indexes: dict[str, int] = {}
        self._incomplete: set[str] = set()
        self._lock = threading.Lock()

    @property
    def active(self) -> bool:
        return self._sink is not None

    @property
    def role(self) -> TraceRole | None:
        return self._role

    def command(self, command: Command) -> CommandTrace:
        trace = CommandTrace(self, self._request_id_factory(), command, _APPLICATION_REQUEST.get())
        trace.record("command_prepared")
        return trace

    def observation(
        self,
        event: str,
        *,
        request_id: str,
        media_session_id: int | None = None,
        token: str | None = None,
    ) -> None:
        """Record what the independent observer saw for one command (observer role only)."""
        if self._role is not TraceRole.OBSERVER:
            raise TraceConfigurationError("only the observer role records observations")
        self.record(event, request_id=request_id, media_session_id=media_session_id, token=token)

    def record(
        self,
        event: str,
        *,
        request_id: str,
        command: Command | None = None,
        media_session_id: int | None = None,
        error_code: ErrorCode | None = None,
        token: str | None = None,
        application_request_id: str | None = None,
    ) -> None:
        sink, role = self._sink, self._role
        if sink is None or role is None:
            return
        if request_id in self._incomplete and event != TRACE_INCOMPLETE:
            self.record(
                TRACE_INCOMPLETE,
                request_id=request_id,
                command=command,
                application_request_id=application_request_id,
            )
        with self._lock:
            self._sequence += 1
            index = self._indexes.get(request_id, 0)
            self._indexes[request_id] = index + 1
            stamped = TraceEvent(
                sequence=self._sequence,
                client=role.value,
                event=event,
                request_id=request_id,
                index=index,
                media_session_id=media_session_id,
                command=None if command is None else command.value,
                error_code=None if error_code is None else error_code.value,
                token=token,
                application_request_id=application_request_id,
            )
        try:
            sink.write(stamped)
        except Exception:
            # Evidence is optional and must never resend, suppress or alter a command; the gap in
            # `index` (and `trace_incomplete`, when it can still be written) exposes the loss.
            with self._lock:
                self._incomplete.add(request_id)
            return
        if event == TRACE_INCOMPLETE:
            with self._lock:
                self._incomplete.discard(request_id)
