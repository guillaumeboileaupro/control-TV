"""FFmpeg argument vectors (pure: no process is started here).

Commands are argument tuples for `subprocess`, never shell strings; every value comes from a
validated model object, and the input arguments come from the `VideoSource` port, so the command
does not depend on the kind of source. The VA-API command always uses constant QP and never a
bitrate. Every command is bounded in time (`-t`) and output size (`-fs`).
"""

from __future__ import annotations

from pathlib import Path

from control_tv.screencast.model import (
    MAX_OUTPUT_BYTES,
    EncoderPlan,
    SoftwareX264,
    VaapiCqp,
    VideoProfile,
    VideoSource,
)


def encoder_arguments(plan: EncoderPlan) -> tuple[str, ...]:
    if isinstance(plan, VaapiCqp):
        return (
            "-vf",
            "format=nv12,hwupload",
            "-c:v",
            "h264_vaapi",
            "-rc_mode",
            "CQP",
            "-qp",
            str(plan.qp),
        )
    return _x264_arguments(plan)


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
        *source.input_arguments(profile),
        "-t",
        str(profile.duration_seconds),
        *encoder_arguments(plan),
        "-fs",
        str(MAX_OUTPUT_BYTES),
        "-y",
        str(output),
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
