"""What this host can encode, and which encoder a request gets.

The probe only lists FFmpeg's encoders and checks the render node: listing an encoder is not proof
an encode succeeds (that is the encode's own result). Selection is a pure policy: the software
fallback is taken only when the caller asked for it, and the reason is kept.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from control_tv.screencast.ffmpeg import encoders_listing_command, parse_encoder_names
from control_tv.screencast.model import (
    EncoderChoice,
    EncodeRequest,
    EncoderPreference,
    EncoderUnavailableError,
    InvalidScreenCastOption,
    SoftwareX264,
    VaapiCqp,
    VaapiDevice,
)

VAAPI_ENCODER = "h264_vaapi"
SOFTWARE_ENCODER = "libx264"


@dataclass(frozen=True)
class EncoderCapabilities:
    encoders: frozenset[str]
    vaapi_device_usable: bool


class CapabilityProbe(Protocol):
    def probe(self, device: VaapiDevice) -> EncoderCapabilities: ...


# Runs one command with a bounded time and returns its standard output (raises on failure).
CommandOutput = Callable[[tuple[str, ...], float], str]


def _command_output(argv: tuple[str, ...], timeout: float) -> str:
    # Bytes that are not UTF-8 (an unusual locale or build) are replaced, never fatal.
    completed = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=True,
        stdin=subprocess.DEVNULL,
    )
    return completed.stdout


def _device_usable(path: str) -> bool:
    return os.path.exists(path) and os.access(path, os.R_OK | os.W_OK)


class FfmpegCapabilityProbe:
    """Asks the local FFmpeg for its encoders and checks the render node can be opened."""

    def __init__(
        self,
        ffmpeg: str = "ffmpeg",
        *,
        timeout: float = 10.0,
        run: CommandOutput = _command_output,
        device_usable: Callable[[str], bool] = _device_usable,
    ) -> None:
        self._ffmpeg = ffmpeg
        self._timeout = timeout
        self._run = run
        self._device_usable = device_usable

    def probe(self, device: VaapiDevice) -> EncoderCapabilities:
        try:
            listing = self._run(encoders_listing_command(self._ffmpeg), self._timeout)
        except (OSError, subprocess.SubprocessError) as error:
            raise EncoderUnavailableError(
                f"FFmpeg could not list its encoders: {type(error).__name__}"
            ) from None
        return EncoderCapabilities(
            encoders=parse_encoder_names(listing),
            vaapi_device_usable=self._device_usable(device.path),
        )


def select_encoder(request: EncodeRequest, capabilities: EncoderCapabilities) -> EncoderChoice:
    """The encoder for `request` on this host, or `EncoderUnavailableError` saying why not."""
    return select_plan(
        request.preference,
        request.device,
        qp=request.qp,
        crf=request.crf,
        capabilities=capabilities,
    )


def select_plan(
    preference: EncoderPreference,
    device: VaapiDevice,
    *,
    qp: int,
    crf: int,
    capabilities: EncoderCapabilities,
) -> EncoderChoice:
    """The one selection policy, shared by the bounded D0 encode and the D1 live encoder: VA-API
    constant QP when usable, libx264 only when the preference allows it (with its reason).

    The preference is compared by identity, so anything but an `EncoderPreference` member is
    refused: a string equal to a value would otherwise fall through to the VA-API path."""
    if type(preference) is not EncoderPreference:
        raise InvalidScreenCastOption(
            f"the encoder preference must be an EncoderPreference: {preference!r}"
        )
    software = SoftwareX264(crf=crf)
    if preference is EncoderPreference.SOFTWARE_ONLY:
        if SOFTWARE_ENCODER not in capabilities.encoders:
            raise EncoderUnavailableError("FFmpeg has no libx264 encoder")
        return EncoderChoice(software)

    missing: str | None = None
    if VAAPI_ENCODER not in capabilities.encoders:
        missing = "FFmpeg has no h264_vaapi encoder"
    elif not capabilities.vaapi_device_usable:
        missing = f"the VA-API render node {device.path} is missing or not accessible"
    if missing is None:
        return EncoderChoice(VaapiCqp(device=device, qp=qp))

    if preference is EncoderPreference.VAAPI_ONLY:
        raise EncoderUnavailableError(f"{missing}; the software fallback was not allowed")
    if SOFTWARE_ENCODER not in capabilities.encoders:
        raise EncoderUnavailableError(f"{missing}, and FFmpeg has no libx264 fallback either")
    return EncoderChoice(software, fallback_reason=missing)
