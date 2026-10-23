"""`StreamReceiver` for D1 path A: a Cast receiver loads the published HLS playlist.

It goes through the shared control layer (`TvControl`), like every other TV command: one
`load_media` of the publication's URL with the HLS content type, sent once, never retried. A
session is returned only when the receiver's status confirms that it loaded that URL; that is
"started", not "seen on the TV" (physical playback is validated on hardware, RD1-RD8). Stopping
stops the TV's media only while it is still this stream: content the person started since is
never stopped. Compatibility with any given receiver, the Freebox included, is not claimed here.
"""

from __future__ import annotations

import contextlib
import math
import threading
import uuid
from dataclasses import dataclass, field

from control_tv.domain import (
    CommandResult,
    Confirmation,
    ControlError,
    DeviceId,
    InvalidArgumentError,
    MediaRequest,
)
from control_tv.ports import TvControl
from control_tv.screen_streaming.model import (
    PublishedStream,
    ReceiverSession,
    ReceiverTarget,
    StreamCleanupError,
    StreamStartError,
)

HLS_CONTENT_TYPE = "application/x-mpegURL"


@dataclass
class _Session:
    device_id: DeviceId
    url: str
    receiver_session_id: str | None
    stopping: threading.Lock = field(default_factory=threading.Lock)


class CastHlsReceiver:
    """`StreamReceiver` loading a published HLS stream on a Cast receiver."""

    def __init__(self, control: TvControl) -> None:
        self._control = control
        self._sessions: dict[str, _Session] = {}
        self._released: set[str] = set()
        self._lock = threading.Lock()

    @property
    def unreleased(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._sessions)

    def start(
        self, target: ReceiverTarget, publication: PublishedStream, *, timeout: float
    ) -> ReceiverSession:
        """Load `publication` on `target`. `timeout` is checked; the shared control layer bounds
        the command and its confirmation with its own window."""
        if isinstance(timeout, bool) or not isinstance(timeout, int | float):
            raise StreamStartError("the receiver timeout must be a number of seconds")
        if not math.isfinite(timeout) or timeout <= 0:
            raise StreamStartError("the receiver timeout must be finite and positive")
        device_id = DeviceId(target.device_id)
        try:
            request = MediaRequest(url=publication.uri, content_type=HLS_CONTENT_TYPE)
        except InvalidArgumentError:
            raise StreamStartError("a Cast receiver loads an HTTP(S) publication only") from None
        try:
            result = self._control.load_media(device_id, request)
        except ControlError as error:
            raise StreamStartError(
                f"the receiver could not load the stream ({error.code.value})"
            ) from None
        if result.confirmation is not Confirmation.CONFIRMED:
            # It may be playing it anyway: stop it while it is still this stream, then fail.
            session = _Session(device_id, publication.uri, _receiver_session(result))
            # A failure here is not raised: the publication itself ends with the cleanup.
            with contextlib.suppress(StreamCleanupError):
                self._stop_if_still_ours(session)
            raise StreamStartError(
                f"the receiver did not confirm the stream ({result.confirmation.value})"
            )
        handle = f"cast-hls-{uuid.uuid4().hex}"
        receiver_session_id = _receiver_session(result)
        if receiver_session_id is None:
            session = _Session(device_id, publication.uri, None)
            with contextlib.suppress(StreamCleanupError):
                self._stop_if_still_ours(session)
            raise StreamStartError("the receiver did not report its application session")
        with self._lock:
            self._sessions[handle] = _Session(device_id, publication.uri, receiver_session_id)
        return ReceiverSession(handle)

    def stop(self, session: ReceiverSession) -> None:
        """Stop the TV's media if it is still this stream. Idempotent: a session already
        released succeeds; one this receiver never started is refused. A failure keeps it."""
        with self._lock:
            owned = self._sessions.get(session.handle)
            if owned is None:
                if session.handle in self._released:
                    return
                raise StreamCleanupError("this receiver owns no such session")
        with owned.stopping:
            with self._lock:
                if self._sessions.get(session.handle) is not owned:
                    return
            self._stop_if_still_ours(owned)
            with self._lock:
                del self._sessions[session.handle]
                self._released.add(session.handle)

    def _stop_if_still_ours(self, session: _Session) -> None:
        try:
            status = self._control.get_status(session.device_id)
        except ControlError as error:
            raise StreamCleanupError(
                f"the receiver state could not be read ({error.code.value})"
            ) from None
        media = status.media
        receiver = status.receiver
        current_receiver_session = None if receiver is None else receiver.app_session_id
        if media is not None and media.content_id != session.url:
            return  # another item now plays: never stop it or its application
        if media is not None:
            try:
                result = self._control.stop(session.device_id)
            except ControlError as error:
                raise StreamCleanupError(
                    f"the receiver could not be stopped ({error.code.value})"
                ) from None
            if result.confirmation is not Confirmation.CONFIRMED:
                raise StreamCleanupError(
                    f"the receiver stop was not confirmed ({result.confirmation.value})"
                )
        if (
            session.receiver_session_id is None
            or current_receiver_session != session.receiver_session_id
        ):
            return
        try:
            closed = self._control.close_app(session.device_id, session.receiver_session_id)
        except ControlError as error:
            raise StreamCleanupError(
                f"the receiver application could not be closed ({error.code.value})"
            ) from None
        if closed.confirmation is not Confirmation.CONFIRMED:
            raise StreamCleanupError(
                f"the receiver application close was not confirmed ({closed.confirmation.value})"
            )


def _receiver_session(result: CommandResult) -> str | None:
    observed = result.observed
    receiver = None if observed is None else observed.receiver
    return None if receiver is None else receiver.app_session_id
