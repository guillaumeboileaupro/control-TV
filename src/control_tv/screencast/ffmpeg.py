"""FFmpeg argument vectors (pure: no process is started here).

Commands are argument tuples for `subprocess`, never shell strings; every value comes from a
validated model object. Sources are typed specifications: this adapter alone turns a known source
type into input arguments (`source_arguments`), and refuses anything else, so no source can hand
FFmpeg its own arguments. The VA-API command always uses constant QP and never a bitrate. Every
command is bounded in time (`-t`) and output size (`-fs`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from control_tv.screencast.model import (
    HLS_PLAYLIST,
    HLS_SEGMENT_PATTERN,
    MAX_LIVE_SESSION_SECONDS,
    MAX_OUTPUT_BYTES,
    SYNTHETIC_PATTERNS,
    EncoderPlan,
    HlsSegmenting,
    InvalidScreenCastOption,
    LiveSource,
    SoftwareX264,
    VaapiCqp,
    VideoProfile,
    VideoSource,
    X11GrabSource,
    require_d0_source,
)


class FrameFormat(Protocol):
    """Picture size and rate, as both the bounded D0 `VideoProfile` and the D1 live
    `EncodingProfile` carry them (validated by their own constructors)."""

    @property
    def width(self) -> int: ...

    @property
    def height(self) -> int: ...

    @property
    def fps(self) -> int: ...


def source_arguments(source: LiveSource, profile: FrameFormat) -> tuple[str, ...]:
    """FFmpeg input arguments for a known source type; `InvalidScreenCastOption` otherwise."""
    if type(source) is X11GrabSource:
        return (
            "-f",
            "x11grab",
            "-draw_mouse",
            "1",
            "-framerate",
            str(profile.fps),
            "-video_size",
            f"{source.width}x{source.height}",
            "-i",
            f"{source.display}+{source.x},{source.y}",
        )
    synthetic = require_d0_source(source)
    if synthetic.pattern not in SYNTHETIC_PATTERNS:  # changed after construction
        raise InvalidScreenCastOption(f"unknown synthetic pattern: {synthetic.pattern!r}")
    size = f"{profile.width}x{profile.height}"
    return ("-f", "lavfi", "-i", f"{synthetic.pattern}=size={size}:rate={profile.fps}")


def encoder_arguments(plan: EncoderPlan, scale: tuple[int, int] | None = None) -> tuple[str, ...]:
    """The encoder's arguments; `scale` resizes the picture first (a captured screen to the
    requested size)."""
    resize = "" if scale is None else f"scale={scale[0]}:{scale[1]},"
    if isinstance(plan, VaapiCqp):
        return (
            "-vf",
            f"{resize}format=nv12,hwupload",
            "-c:v",
            "h264_vaapi",
            "-rc_mode",
            "CQP",
            "-qp",
            str(plan.qp),
        )
    scaled = () if scale is None else ("-vf", resize.rstrip(","))
    return (*scaled, *_x264_arguments(plan))


def _x264_arguments(plan: SoftwareX264) -> tuple[str, ...]:
    return (
        "-c:v",
        "libx264",
        "-preset",
        plan.preset,
        "-tune",
        "zerolatency",
        "-crf",
        str(plan.crf),
        "-pix_fmt",
        "yuv420p",
    )


def encode_command(
    ffmpeg: str,
    source: VideoSource,
    profile: VideoProfile,
    plan: EncoderPlan,
    output: Path,
) -> tuple[str, ...]:
    """The full argument vector: quiet, no stdin, one bounded encode of `source` to `output`."""
    device = ("-vaapi_device", plan.device.path) if isinstance(plan, VaapiCqp) else ()
    return (
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        *device,
        *source_arguments(source, profile),
        "-t",
        str(profile.duration_seconds),
        *encoder_arguments(plan),
        "-fs",
        str(MAX_OUTPUT_BYTES),
        "-y",
        str(output),
    )


def live_hls_command(
    ffmpeg: str,
    source: LiveSource,
    frame: FrameFormat,
    plan: EncoderPlan,
    output_directory: Path,
    segmenting: HlsSegmenting,
    session_seconds: int,
) -> tuple[str, ...]:
    """A live H.264 encode cut into HLS segments in `output_directory` (playlist `index.m3u8`):
    the same source and encoder arguments as the bounded encode, plus a key frame at the start of
    every segment, no B-frames (lower latency), the H.264 High profile, a rolling playlist whose
    old segments are deleted, and an end after `session_seconds` (`-t`) whatever happens.

    The synthetic picture is read at its own frame rate (`-re`): without it FFmpeg generates it
    as fast as it can, so a "live" session would be produced in a fraction of its duration. A
    captured X11 region is real time already (no `-re`) and is scaled to `frame`'s size."""
    if (
        isinstance(session_seconds, bool)
        or not isinstance(session_seconds, int)
        or not 1 <= session_seconds <= MAX_LIVE_SESSION_SECONDS
    ):
        raise InvalidScreenCastOption(
            f"session_seconds must be an integer from 1 to {MAX_LIVE_SESSION_SECONDS}: "
            f"{session_seconds!r}"
        )
    device = ("-vaapi_device", plan.device.path) if isinstance(plan, VaapiCqp) else ()
    keyframes = str(segmenting.keyframe_interval(frame.fps))
    # libx264 alone would add key frames at scene cuts and misalign segments.
    scene_cut = () if isinstance(plan, VaapiCqp) else ("-sc_threshold", "0")
    captured = type(source) is X11GrabSource
    pacing = () if captured else ("-re",)
    scale = (frame.width, frame.height) if captured else None
    return (
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        *device,
        *pacing,
        *source_arguments(source, frame),
        "-t",
        str(session_seconds),
        *encoder_arguments(plan, scale),
        "-profile:v",
        "high",
        "-g",
        keyframes,
        "-keyint_min",
        keyframes,
        *scene_cut,
        "-bf",
        "0",
        "-f",
        "hls",
        "-hls_time",
        str(segmenting.segment_seconds),
        "-hls_list_size",
        str(segmenting.playlist_size),
        "-hls_flags",
        "delete_segments+independent_segments",
        "-hls_segment_type",
        "mpegts",
        "-hls_segment_filename",
        str(output_directory / HLS_SEGMENT_PATTERN),
        "-y",
        str(output_directory / HLS_PLAYLIST),
    )


def encoders_listing_command(ffmpeg: str) -> tuple[str, ...]:
    """Lists the encoders FFmpeg was built with (a listing is not proof an encode works)."""
    return (ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-encoders")


def parse_encoder_names(listing: str) -> frozenset[str]:
    """Encoder names from `ffmpeg -encoders`: lines like ` V....D h264_vaapi  H.264 ...`."""
    names: set[str] = set()
    for line in listing.splitlines():
        parts = line.split()
        # Legend lines (` V..... = Video`) share the flag column; their second word is `=`.
        if len(parts) >= 2 and len(parts[0]) == 6 and parts[0][0] in "VAS" and parts[1] != "=":
            names.add(parts[1])
    return frozenset(names)
