"""Opt-in, privacy-preserving command lifecycle traces for acceptance evidence."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from control_tv.domain import Command, ErrorCode


@dataclass(frozen=True, slots=True)
class TraceEvent:
    """One observed application-side phase; never evidence of physical TV behavior."""

    sequence: int
    client: str
    event: str
    request_id: str
    media_session_id: int | None = None
    command: str | None = None
    error_code: str | None = None


class TraceSink(Protocol):
    def write(self, event: TraceEvent) -> None: ...


class NullTraceSink:
    def write(self, event: TraceEvent) -> None:
        del event


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


class JsonLinesTraceSink:
    """Append local acceptance evidence without opening a network service."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    def write(self, event: TraceEvent) -> None:
        line = json.dumps(asdict(event), separators=(",", ":"), sort_keys=True)
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(self._path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
            try:
                os.fchmod(descriptor, 0o600)
            except Exception:
                os.close(descriptor)
                raise
            with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
                stream.write(line + "\n")


@dataclass(frozen=True, slots=True)
class CommandTrace:
    recorder: TraceRecorder
    request_id: str
    command: Command

    def record(
        self,
        event: str,
        *,
        media_session_id: int | None = None,
        error_code: ErrorCode | None = None,
    ) -> None:
        self.recorder.record(
            event,
            request_id=self.request_id,
            command=self.command,
            media_session_id=media_session_id,
            error_code=error_code,
        )


class TraceRecorder:
    """Creates correlated command traces; instrumentation failures are non-functional."""

    def __init__(
        self,
        sink: TraceSink | None = None,
        *,
        client: str = "control-tv",
        request_id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
        sequence_factory: Callable[[], int] = time.time_ns,
    ) -> None:
        self._sink = sink or NullTraceSink()
        self._client = client
        self._request_id_factory = request_id_factory
        self._sequence_factory = sequence_factory
        self._sequence = 0
        self._lock = threading.Lock()

    def command(self, command: Command) -> CommandTrace:
        trace = CommandTrace(self, self._request_id_factory(), command)
        trace.record("command_prepared")
        return trace

    def record(
        self,
        event: str,
        *,
        request_id: str,
        command: Command,
        media_session_id: int | None = None,
        error_code: ErrorCode | None = None,
    ) -> None:
        try:
            with self._lock:
                self._sequence = max(self._sequence + 1, self._sequence_factory())
                sequence = self._sequence
            self._sink.write(
                TraceEvent(
                    sequence=sequence,
                    client=self._client,
                    event=event,
                    request_id=request_id,
                    media_session_id=media_session_id,
                    command=command.value,
                    error_code=None if error_code is None else error_code.value,
                )
            )
        except Exception:
            # Acceptance evidence is optional and must never resend, suppress or alter a command.
            return

    def observation(self, event: str, *, request_id: str, media_session_id: int | None) -> None:
        """Correlate an independently observed receiver status with one command."""
        try:
            with self._lock:
                self._sequence = max(self._sequence + 1, self._sequence_factory())
                sequence = self._sequence
            self._sink.write(
                TraceEvent(
                    sequence=sequence,
                    client=self._client,
                    event=event,
                    request_id=request_id,
                    media_session_id=media_session_id,
                )
            )
        except Exception:
            return


def recorder_from_environment() -> TraceRecorder:
    """Build the opt-in local recorder used by packaged bridge processes."""
    destination = os.environ.get("CONTROL_TV_COMMAND_TRACE")
    if not destination:
        return TraceRecorder()
    client = os.environ.get("CONTROL_TV_TRACE_CLIENT", "control-tv")
    return TraceRecorder(JsonLinesTraceSink(Path(destination)), client=client)
