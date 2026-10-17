"""Typed values of the local screen-cast foundation (D0, synthetic source only).

Every value validates itself on construction, so no unchecked number, pattern or path ever reaches
a command line. Nothing here captures a screen, opens a network port or talks to a device.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

# FFmpeg's built-in test patterns this foundation may encode: a synthetic picture only.
SYNTHETIC_PATTERNS = frozenset({"testsrc2", "testsrc", "smptebars", "smptehdbars"})
_RENDER_NODE = re.compile(r"/dev/dri/renderD[0-9]{3}")


class ScreenCastError(Exception):
    """A screen-cast operation could not be done; `message` says why, for a person."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InvalidScreenCastOption(ScreenCastError):
    """A requested option (size, rate, quality, pattern, device) is outside what is allowed."""


class EncoderUnavailableError(ScreenCastError):
    """No allowed encoder can run here (missing encoder, missing device, fallback not allowed)."""


class EncodeFailedError(ScreenCastError):
    """The encoder process could not start, stopped with an error, or ran past its time."""


class ScreenCastStateError(ScreenCastError):
    """The operation is not allowed in the service's current state."""


class ScreenCastState(StrEnum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class EncoderPreference(StrEnum):
    """Which encoder a caller accepts. The software fallback is never implicit."""

    VAAPI_ONLY = "vaapi_only"
    SOFTWARE_ONLY = "software_only"
    VAAPI_THEN_SOFTWARE = "vaapi_then_software"


@dataclass(frozen=True)
class VaapiDevice:
    """A DRM render node, e.g. `/dev/dri/renderD128`."""

    path: str

    def __post_init__(self) -> None:
        if not _RENDER_NODE.fullmatch(self.path):
            raise InvalidScreenCastOption(
                f"the VA-API device must be a render node like /dev/dri/renderD128: {self.path!r}"
            )


@dataclass(frozen=True)
class SyntheticSource:
    """FFmpeg's generated test picture (`lavfi`): no desktop, no camera, no file."""

    pattern: str = "testsrc2"

    def __post_init__(self) -> None:
        if self.pattern not in SYNTHETIC_PATTERNS:
            allowed = ", ".join(sorted(SYNTHETIC_PATTERNS))
            raise InvalidScreenCastOption(
                f"the synthetic pattern must be one of {allowed}: {self.pattern!r}"
            )


def _require_int(name: str, value: int, low: int, high: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise InvalidScreenCastOption(f"{name} must be an integer from {low} to {high}: {value!r}")


@dataclass(frozen=True)
class VideoProfile:
    """Picture size, frame rate and length of one synthetic encode."""

    width: int = 1920
    height: int = 1080
    fps: int = 30
    duration_seconds: int = 10

    def __post_init__(self) -> None:
        _require_int("width", self.width, 16, 4096)
        _require_int("height", self.height, 16, 4096)
        _require_int("fps", self.fps, 1, 60)
        _require_int("duration_seconds", self.duration_seconds, 1, 600)
        if self.width % 2 or self.height % 2:
            raise InvalidScreenCastOption(
                f"H.264 needs an even width and height: {self.width}x{self.height}"
            )


@dataclass(frozen=True)
class VaapiCqp:
    """H.264 on the GPU through VA-API, constant QP (the only rate control the measured Intel
    iHD driver accepts here; a bitrate request fails, so none is ever set)."""

    device: VaapiDevice
    qp: int = 24

    def __post_init__(self) -> None:
        _require_int("qp", self.qp, 1, 51)


@dataclass(frozen=True)
class SoftwareX264:
    """H.264 on the CPU with libx264, constant rate factor: the explicit fallback."""

    crf: int = 23
    preset: str = "veryfast"

    def __post_init__(self) -> None:
        _require_int("crf", self.crf, 0, 51)
        if self.preset not in {"ultrafast", "superfast", "veryfast", "faster", "fast", "medium"}:
            raise InvalidScreenCastOption(f"unsupported libx264 preset: {self.preset!r}")


EncoderPlan = VaapiCqp | SoftwareX264


@dataclass(frozen=True)
class EncoderChoice:
    """The encoder that will run, and why a fallback was taken when it was."""

    plan: EncoderPlan
    fallback_reason: str | None = None


@dataclass(frozen=True)
class SyntheticEncodeRequest:
    """One local, bounded encode of a synthetic picture into a private file."""

    source: SyntheticSource
    profile: VideoProfile
    preference: EncoderPreference
    device: VaapiDevice = VaapiDevice("/dev/dri/renderD128")
    qp: int = 24
    crf: int = 23


@dataclass(frozen=True)
class EncodeResult:
    """A finished encode: what ran, what it produced and how long it took."""

    encoder: EncoderPlan
    fallback_reason: str | None
    output: Path
    output_bytes: int
    elapsed_seconds: float
