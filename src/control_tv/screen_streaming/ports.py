"""Small injectable ports for capture, encoding, publication and reception.

There are deliberately no concrete implementations in the D1 preparation spike.
"""

from __future__ import annotations

from typing import Protocol

from control_tv.screen_streaming.model import (
    CaptureHandle,
    DisplaySelection,
    EncodedStream,
    EncodingProfile,
    PublicationConfig,
    PublishedStream,
    ReceiverSession,
    ReceiverTarget,
    VideoSource,
)


class ScreenCapture(Protocol):
    """Own a future explicitly selected X11 capture and nothing else.

    ``stop`` must be idempotent because lifecycle recovery can retry an uncertain cleanup.
    """

    def start(self, display: DisplaySelection) -> CaptureHandle: ...

    def stop(self, capture: CaptureHandle) -> None: ...


class ScreenEncoder(Protocol):
    """Turn a typed source into an encoded-stream handle.

    The concrete adapter will be built from D0 after PR #81 is merged; this spike does not
    implement or duplicate encoding. ``stop`` must be idempotent.
    """

    def start(self, source: VideoSource, profile: EncodingProfile) -> EncodedStream: ...

    def stop(self, stream: EncodedStream) -> None: ...


class StreamPublisher(Protocol):
    """Expose an encoded stream for one receiver and own that exposure's lifetime.

    ``stop`` must be idempotent because a failed cleanup remains owned and is retried.
    """

    def start(self, stream: EncodedStream, config: PublicationConfig) -> PublishedStream: ...

    def stop(self, publication: PublishedStream) -> None: ...


class StreamReceiver(Protocol):
    """Start and stop receiver playback; success means started, not physically observed.

    ``stop`` must be idempotent because a failed cleanup remains owned and is retried.
    """

    def start(
        self,
        target: ReceiverTarget,
        publication: PublishedStream,
        *,
        timeout: float,
    ) -> ReceiverSession: ...

    def stop(self, session: ReceiverSession) -> None: ...
