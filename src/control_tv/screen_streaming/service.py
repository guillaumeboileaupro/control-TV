"""Lifecycle policy for a synthetic source, encoder, publisher and receiver.

The service starts nothing by itself.  Callers must invoke ``start`` explicitly.  Cleanup always
runs in reverse ownership order and attempts every step even when another step fails or is
interrupted (``KeyboardInterrupt``, ``SystemExit``): an interruption is re-raised only after every
step was attempted and the state recorded. A resource whose stop did not succeed stays owned
(``unreleased``), so a later ``stop``/``close`` retries exactly it and nothing is released twice.

Serialization: ``start`` and ``stop`` refuse to run beside another lifecycle call.  ``close`` is a
definitive shutdown: it is recorded first (no later ``start`` is accepted), then it waits (bounded
by ``close_timeout``) for an in-progress call.  A start that sees the recorded close between its
steps or before reporting RUNNING stops what it acquired and fails as cancelled, so a close that
could not wait is still carried out, never lost.
"""

from __future__ import annotations

import contextlib
import math
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from types import TracebackType
from typing import TypeVar

from control_tv.screen_streaming.model import (
    EncodedStream,
    EncodingProfile,
    InvalidStreamConfiguration,
    PublicationConfig,
    PublicationKind,
    PublishedStream,
    ReceiverSession,
    ReceiverTarget,
    ScreenStreamError,
    SessionState,
    StreamCleanupError,
    StreamStartError,
    SyntheticPattern,
    SyntheticVideoSource,
)
from control_tv.screen_streaming.ports import ScreenEncoder, StreamPublisher, StreamReceiver

_MAX_RECEIVER_TIMEOUT = 60.0
_DEFAULT_CLOSE_TIMEOUT = 90.0
_STARTABLE = {SessionState.IDLE, SessionState.STOPPED, SessionState.FAILED}
T = TypeVar("T")


@dataclass(frozen=True)
class StreamRequest:
    source: SyntheticVideoSource
    profile: EncodingProfile
    publication: PublicationConfig
    receiver: ReceiverTarget
    receiver_timeout: float = 10.0

    def __post_init__(self) -> None:
        if type(self.source) is not SyntheticVideoSource:
            raise InvalidStreamConfiguration("only the exact synthetic source is allowed here")
        if type(self.profile) is not EncodingProfile:
            raise InvalidStreamConfiguration("profile must be an EncodingProfile")
        if type(self.publication) is not PublicationConfig:
            raise InvalidStreamConfiguration("publication must be a PublicationConfig")
        if type(self.receiver) is not ReceiverTarget:
            raise InvalidStreamConfiguration("receiver must be a ReceiverTarget")
        timeout = self.receiver_timeout
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, int | float)
            or not math.isfinite(timeout)
            or not 0 < timeout <= _MAX_RECEIVER_TIMEOUT
        ):
            raise InvalidStreamConfiguration(
                "receiver_timeout must be a finite number from more than 0 "
                f"to {_MAX_RECEIVER_TIMEOUT:g}: {timeout!r}"
            )

    def revalidated(self) -> StreamRequest:
        """Rebuild every value so post-construction mutation cannot reach an adapter."""
        try:
            return StreamRequest(
                SyntheticVideoSource(SyntheticPattern(self.source.pattern)),
                EncodingProfile(self.profile.width, self.profile.height, self.profile.fps),
                PublicationConfig(
                    PublicationKind(self.publication.kind),
                    self.publication.max_session_seconds,
                ),
                ReceiverTarget(self.receiver.device_id),
                self.receiver_timeout,
            )
        except (AttributeError, TypeError, ValueError) as error:
            raise InvalidStreamConfiguration(
                f"stream request is malformed: {type(error).__name__}"
            ) from None


class _CloseRequested(Exception):
    """Internal: a recorded close() cancels the start in progress."""


@dataclass
class _Cleanup:
    failures: list[str]
    interruption: BaseException | None = None


@dataclass
class _OwnedSession:
    encoded: EncodedStream | None = None
    publication: PublishedStream | None = None
    receiver: ReceiverSession | None = None


class StreamingSessionService:
    """Coordinate one explicitly requested stream session through three injected ports."""

    def __init__(
        self,
        encoder: ScreenEncoder,
        publisher: StreamPublisher,
        receiver: StreamReceiver,
        *,
        close_timeout: float = _DEFAULT_CLOSE_TIMEOUT,
    ) -> None:
        if (
            isinstance(close_timeout, bool)
            or not isinstance(close_timeout, int | float)
            or not math.isfinite(close_timeout)
            or close_timeout <= 0
        ):
            raise InvalidStreamConfiguration(
                f"close_timeout must be a finite number of seconds above 0: {close_timeout!r}"
            )
        self._close_timeout = float(close_timeout)
        self._closed = threading.Event()
        self._encoder = encoder
        self._publisher = publisher
        self._receiver = receiver
        self._lock = threading.Lock()
        self._owned: _OwnedSession | None = None
        self._state = SessionState.IDLE
        self.last_error: str | None = None

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def closed(self) -> bool:
        """Whether close() was requested: no start is accepted any more."""
        return self._closed.is_set()

    @property
    def unreleased(self) -> tuple[str, ...]:
        """The resources still owned after a failed or interrupted cleanup (stop order)."""
        owned = self._owned
        if owned is None:
            return ()
        held = (
            ("receiver", owned.receiver),
            ("publisher", owned.publication),
            ("encoder", owned.encoded),
        )
        return tuple(name for name, resource in held if resource is not None)

    def start(self, request: StreamRequest) -> None:
        """Start only after this explicit call; clean every acquired resource on failure."""
        with self._exclusive():
            if self._closed.is_set():
                raise StreamStartError("the service is closed: no new session is started")
            if self._state not in _STARTABLE:
                raise StreamStartError(f"cannot start while {self._state.value}")
            if self._owned is not None:
                raise StreamStartError("cannot start while earlier resources still need cleanup")
            if type(request) is not StreamRequest:
                raise InvalidStreamConfiguration("request must be an exact StreamRequest")
            checked = request.revalidated()
            self.last_error = None
            self._state = SessionState.STARTING
            owned = _OwnedSession()
            self._owned = owned
            try:
                owned.encoded = self._encoder.start(checked.source, checked.profile)
                self._raise_if_closed()
                owned.publication = self._publisher.start(owned.encoded, checked.publication)
                self._raise_if_closed()
                owned.receiver = self._receiver.start(
                    checked.receiver,
                    owned.publication,
                    timeout=float(checked.receiver_timeout),
                )
                self._raise_if_closed()
            except _CloseRequested:
                cleanup = self._cleanup(owned)
                self._settle(owned)
                if cleanup.failures:
                    self._state = SessionState.FAILED
                    self.last_error = "start cancelled by close(); " + "; ".join(cleanup.failures)
                else:
                    self.last_error = None
                    self._state = SessionState.STOPPED
                if cleanup.interruption is not None:
                    raise cleanup.interruption from None
                raise StreamStartError("the start was cancelled by close()") from None
            except BaseException as error:
                cleanup = self._cleanup(owned)
                self._settle(owned)
                self._state = SessionState.FAILED
                self.last_error = self._start_failure(error, cleanup.failures)
                if not isinstance(error, Exception):
                    raise
                if cleanup.interruption is not None:
                    raise cleanup.interruption from error
                raise StreamStartError(self.last_error) from error
            self._state = SessionState.RUNNING

    def stop(self) -> None:
        """Stop in receiver/publisher/encoder order; idempotent outside an active session."""
        with self._exclusive():
            self._stop_owned_session()

    def close(self) -> None:
        """Definitive, idempotent shutdown. It is recorded before anything else, so no later start
        is accepted and a start in progress cancels itself; then it waits for an in-progress call
        (at most `close_timeout`) and stops what is owned. A cleanup failure stays visible in
        `state`, `last_error` and `unreleased`; an interruption is re-raised after cleanup."""
        self._closed.set()
        if not self._lock.acquire(timeout=self._close_timeout):
            self.last_error = (
                "close() is pending: another lifecycle call is still running and will release "
                "what it acquired"
            )
            return
        try:
            with contextlib.suppress(StreamCleanupError):
                self._stop_owned_session()
        finally:
            self._lock.release()

    def __enter__(self) -> StreamingSessionService:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _stop_owned_session(self) -> None:
        owned = self._owned
        if owned is None:
            return
        self._state = SessionState.STOPPING
        cleanup = self._cleanup(owned)
        self._settle(owned)
        if cleanup.failures:
            self._state = SessionState.FAILED
            self.last_error = "; ".join(cleanup.failures)
        else:
            self._state = SessionState.STOPPED
        if cleanup.interruption is not None:
            raise cleanup.interruption
        if cleanup.failures:
            raise StreamCleanupError("; ".join(cleanup.failures))

    def _raise_if_closed(self) -> None:
        if self._closed.is_set():
            raise _CloseRequested

    def _settle(self, owned: _OwnedSession) -> None:
        """Keep exactly the resources whose stop did not succeed, for a later retry."""
        self._owned = owned if self._has_resources(owned) else None

    @contextlib.contextmanager
    def _exclusive(self) -> Iterator[None]:
        if not self._lock.acquire(blocking=False):
            raise StreamStartError("another stream lifecycle call is in progress")
        try:
            yield
        finally:
            self._lock.release()

    def _cleanup(self, owned: _OwnedSession) -> _Cleanup:
        """Stop receiver, publisher, encoder: every step is attempted, whatever happens."""
        cleanup = _Cleanup([])
        if self._stop_owned("receiver", owned.receiver, self._receiver.stop, cleanup):
            owned.receiver = None
        if self._stop_owned("publisher", owned.publication, self._publisher.stop, cleanup):
            owned.publication = None
        if self._stop_owned("encoder", owned.encoded, self._encoder.stop, cleanup):
            owned.encoded = None
        return cleanup

    @staticmethod
    def _stop_owned(
        name: str,
        resource: T | None,
        stop: Callable[[T], None],
        cleanup: _Cleanup,
    ) -> bool:
        """True when `resource` is released; False keeps it owned (failed or interrupted)."""
        if resource is None:
            return True
        try:
            stop(resource)
        except Exception as error:
            cleanup.failures.append(f"{name} cleanup failed: {type(error).__name__}")
            return False
        except BaseException as interruption:
            cleanup.failures.append(f"{name} cleanup interrupted: {type(interruption).__name__}")
            if cleanup.interruption is None:
                cleanup.interruption = interruption
            return False
        return True

    @staticmethod
    def _has_resources(owned: _OwnedSession) -> bool:
        return any((owned.encoded, owned.publication, owned.receiver))

    @staticmethod
    def _start_failure(error: BaseException, cleanup: list[str]) -> str:
        if isinstance(error, ScreenStreamError):
            message = error.message
        else:
            message = f"stream start failed: {type(error).__name__}"
        if cleanup:
            message += "; " + "; ".join(cleanup)
        return message
