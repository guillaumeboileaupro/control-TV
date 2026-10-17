"""Local screen-cast foundation (D0): synthetic H.264 encode only.

No desktop capture, no network, no device. See docs/SCREENCAST_D0.md.
"""

from control_tv.screencast.capabilities import (
    CapabilityProbe,
    EncoderCapabilities,
    FfmpegCapabilityProbe,
    select_encoder,
)
from control_tv.screencast.model import (
    MAX_DURATION_SECONDS,
    MAX_OUTPUT_BYTES,
    EncodeFailedError,
    EncoderChoice,
    EncodeRequest,
    EncodeResult,
    EncoderPlan,
    EncoderPreference,
    EncoderUnavailableError,
    InvalidScreenCastOption,
    ScreenCastError,
    ScreenCastState,
    ScreenCastStateError,
    SoftwareX264,
    SourceKind,
    SyntheticSource,
    VaapiCqp,
    VaapiDevice,
    VideoProfile,
    VideoSource,
)
from control_tv.screencast.process import ManagedProcess, ProcessRunner, SubprocessRunner
from control_tv.screencast.service import ScreenCastService

__all__ = [
    "MAX_DURATION_SECONDS",
    "MAX_OUTPUT_BYTES",
    "CapabilityProbe",
    "EncodeFailedError",
    "EncodeRequest",
    "EncodeResult",
    "EncoderCapabilities",
    "EncoderChoice",
    "EncoderPlan",
    "EncoderPreference",
    "EncoderUnavailableError",
    "FfmpegCapabilityProbe",
    "InvalidScreenCastOption",
    "ManagedProcess",
    "ProcessRunner",
    "ScreenCastError",
    "ScreenCastService",
    "ScreenCastState",
    "ScreenCastStateError",
    "SoftwareX264",
    "SourceKind",
    "SubprocessRunner",
    "SyntheticSource",
    "VaapiCqp",
    "VaapiDevice",
    "VideoProfile",
    "VideoSource",
    "select_encoder",
]
