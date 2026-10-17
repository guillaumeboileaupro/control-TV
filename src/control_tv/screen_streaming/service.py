"""Lifecycle policy for a synthetic source, encoder, publisher and receiver.

The service starts nothing by itself.  Callers must invoke ``start`` explicitly.  Cleanup always
runs in reverse ownership order and attempts every step even when another step fails.
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
    ) -> None:
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

    def start(self, request: StreamRequest) -> None:
        """Start only after this explicit call; clean every acquired resource on failure."""
        with self._exclusive():
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
                owned.publication = self._publisher.start(owned.encoded, checked.publication)
                owned.receiver = self._receiver.start(
                    checked.receiver,
                    owned.publication,
                    timeout=float(checked.receiver_timeout),
                )
            except BaseException as error:
                cleanup = self._cleanup(owned)
                self._owned = owned if self._has_resources(owned) else None
                self._state = SessionState.FAILED
                self.last_error = self._start_failure(error, cleanup)
                if not isinstance(error, Exception):
                    raise
                raise StreamStartError(self.last_error) from error
            self._state = SessionState.RUNNING

    def stop(self) -> None:
        """Stop in receiver/publisher/encoder order; idempotent outside an active session."""
        with self._exclusive():
            owned = self._owned
            if owned is None:
                return
            self._state = SessionState.STOPPING
            failures = self._cleanup(owned)
            self._owned = owned if self._has_resources(owned) else None
            if failures:
                self._state = SessionState.FAILED
                self.last_error = "; ".join(failures)
                raise StreamCleanupError(self.last_error)
            self._state = SessionState.STOPPED

    def close(self) -> None:
        """Best-effort idempotent shutdown; failures remain visible in state and last_error."""
        with contextlib.suppress(ScreenStreamError):
            self.stop()

    def __enter__(self) -> StreamingSessionService:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    @contextlib.contextmanager
    def _exclusive(self) -> Iterator[None]:
        if not self._lock.acquire(blocking=False):
            raise StreamStartError("another stream lifecycle call is in progress")
        try:
            yield
        finally:
            self._lock.release()

    def _cleanup(self, owned: _OwnedSession) -> list[str]:
        failures: list[str] = []
        if self._stop_owned("receiver", owned.receiver, self._receiver.stop, failures):
            owned.receiver = None
        if self._stop_owned("publisher", owned.publication, self._publisher.stop, failures):
            owned.publication = None
        if self._stop_owned("encoder", owned.encoded, self._encoder.stop, failures):
            owned.encoded = None
        return failures

    @staticmethod
    def _stop_owned(
        name: str,
        resource: T | None,
        stop: Callable[[T], None],
        failures: list[str],
    ) -> bool:
        if resource is None:
            return True
        try:
            stop(resource)
        except Exception as error:
            failures.append(f"{name} cleanup failed: {type(error).__name__}")
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
