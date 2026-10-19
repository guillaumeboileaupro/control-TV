"""Receiver-independent screen-stream orchestration prototype (D1 preparation).

This package contains contracts and lifecycle policy only.  It captures no display, encodes no
frame, opens no socket and contacts no receiver.
"""

from control_tv.screen_streaming.model import (
    MAX_SESSION_SECONDS,
    CapturedVideo,
    CaptureHandle,
    DisplaySelection,
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
from control_tv.screen_streaming.ports import (
    ScreenCapture,
    ScreenEncoder,
    StreamPublisher,
    StreamReceiver,
)
from control_tv.screen_streaming.service import StreamingSessionService, StreamRequest

__all__ = [
    "MAX_SESSION_SECONDS",
    "CaptureHandle",
    "CapturedVideo",
    "DisplaySelection",
    "EncodedStream",
    "EncodingProfile",
    "InvalidStreamConfiguration",
    "PublicationConfig",
    "PublicationKind",
    "PublishedStream",
    "ReceiverSession",
    "ReceiverTarget",
    "ScreenCapture",
    "ScreenEncoder",
    "ScreenStreamError",
    "SessionState",
    "StreamCleanupError",
    "StreamPublisher",
    "StreamReceiver",
    "StreamRequest",
    "StreamStartError",
    "StreamingSessionService",
    "SyntheticPattern",
    "SyntheticVideoSource",
]
