"""Receiver-independent screen-stream orchestration (D1).

This package exports the contracts and the lifecycle policy; importing it captures no display,
encodes no frame, opens no socket and contacts no receiver. The adapters live in their own
modules and act only when a caller starts them: `h264_encoder` (D0's live encoder),
`hls_publisher` (the home-network HLS publication) and `cast_receiver` (path A's receiver).
"""

from control_tv.screen_streaming.composition import (
    HlsCompositionConfig,
    HlsStreamingSession,
    assemble_hls_session,
    routed_lan_ipv4,
)
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
    "HlsCompositionConfig",
    "HlsStreamingSession",
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
    "assemble_hls_session",
    "routed_lan_ipv4",
]
