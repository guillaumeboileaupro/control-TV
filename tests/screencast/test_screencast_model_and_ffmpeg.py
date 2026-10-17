"""Validated values and FFmpeg argument vectors of the synthetic encode (no process started)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from control_tv.screencast.ffmpeg import (
    encode_command,
    encoders_listing_command,
    parse_encoder_names,
    source_arguments,
)
from control_tv.screencast.model import (
    MAX_OUTPUT_BYTES,
    EncodeRequest,
    EncoderPreference,
    InvalidScreenCastOption,
    SoftwareX264,
    SourceKind,
    SyntheticSource,
    VaapiCqp,
    VaapiDevice,
    VideoProfile,
)

OUT = Path("/private/session/synthetic.mp4")


def test_the_vaapi_command_is_the_measured_cqp_encode_and_never_a_bitrate() -> None:
    argv = encode_command(
        "ffmpeg",
        SyntheticSource(),
        VideoProfile(),
        VaapiCqp(VaapiDevice("/dev/dri/renderD128"), qp=24),
        OUT,
    )

    assert argv == (
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-vaapi_device",
        "/dev/dri/renderD128",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=1920x1080:rate=30",
        "-t",
        "10",
        "-vf",
        "format=nv12,hwupload",
        "-c:v",
        "h264_vaapi",
        "-rc_mode",
        "CQP",
        "-qp",
        "24",
        "-fs",
        str(MAX_OUTPUT_BYTES),
        "-y",
        str(OUT),
    )
    assert "-b:v" not in argv and "-maxrate" not in argv


def test_the_software_fallback_command_is_explicit_libx264() -> None:
    argv = encode_command(
        "/usr/bin/ffmpeg",
        SyntheticSource("smptebars"),
        VideoProfile(width=1280, height=720, fps=25, duration_seconds=3),
        SoftwareX264(crf=20, preset="ultrafast"),
        OUT,
    )

    assert "-vaapi_device" not in argv and "h264_vaapi" not in argv
    assert argv[argv.index("-i") + 1] == "smptebars=size=1280x720:rate=25"
    assert argv[argv.index("-c:v") + 1] == "libx264"
    assert argv[argv.index("-crf") + 1] == "20"
    assert argv[argv.index("-preset") + 1] == "ultrafast"
    assert argv[-2:] == ("-y", str(OUT))


def test_only_synthetic_input_is_ever_built() -> None:
    argv = encode_command("ffmpeg", SyntheticSource(), VideoProfile(), SoftwareX264(), OUT)

    assert argv[argv.index("-f") + 1] == "lavfi"
    assert not {"x11grab", "kmsgrab", "pulse", "alsa", "v4l2"} & set(argv)
    assert not any(arg.startswith(("http", "rtp", "udp", "tcp", "rtmp", "srt")) for arg in argv)


@pytest.mark.parametrize(
    "build",
    [
        lambda: VaapiDevice("/dev/dri/card0"),
        lambda: VaapiDevice("/dev/dri/renderD128; rm -rf /"),
        lambda: VaapiDevice("renderD128"),
        lambda: SyntheticSource("x11grab"),
        lambda: SyntheticSource("testsrc2,format=rgb24"),
        lambda: VideoProfile(width=1921),
        lambda: VideoProfile(width=0),
        lambda: VideoProfile(height=8192),
        lambda: VideoProfile(fps=0),
        lambda: VideoProfile(fps=120),
        lambda: VideoProfile(duration_seconds=0),
        lambda: VideoProfile(duration_seconds=121),
        lambda: EncodeRequest(
            SyntheticSource(), VideoProfile(), EncoderPreference.VAAPI_ONLY, qp=0
        ),
        lambda: EncodeRequest(
            SyntheticSource(), VideoProfile(), EncoderPreference.VAAPI_ONLY, qp=52
        ),
        lambda: EncodeRequest(
            SyntheticSource(), VideoProfile(), EncoderPreference.SOFTWARE_ONLY, crf=52
        ),
        lambda: VideoProfile(width=True),
        lambda: VaapiCqp(VaapiDevice("/dev/dri/renderD128"), qp=0),
        lambda: VaapiCqp(VaapiDevice("/dev/dri/renderD128"), qp=52),
        lambda: SoftwareX264(crf=60),
        lambda: SoftwareX264(preset="placebo; echo"),
    ],
)
def test_invalid_options_are_refused_before_any_command(build: object) -> None:
    assert callable(build)
    with pytest.raises(InvalidScreenCastOption):
        build()


def test_the_encoder_listing_is_parsed_without_its_legend() -> None:
    listing = """Encoders:
 V..... = Video
 A..... = Audio
 S..... = Subtitle
 .F.... = Frame-level multithreading
 ------
 V....D libx264              libx264 H.264 / AVC / MPEG-4 AVC
 V....D h264_vaapi           H.264/AVC (VAAPI) (codec h264)
 V..... h264_qsv             H.264 / AVC (Intel Quick Sync Video)
 A....D aac                  AAC (Advanced Audio Coding)
"""

    assert parse_encoder_names(listing) == frozenset({"libx264", "h264_vaapi", "h264_qsv", "aac"})
    assert encoders_listing_command("ffmpeg")[-1] == "-encoders"


class LookAlikeSource:
    """Claims to be synthetic but would hand FFmpeg a desktop capture (the P1 bypass)."""

    @property
    def kind(self) -> SourceKind:
        return SourceKind.SYNTHETIC

    def input_arguments(self, profile: VideoProfile) -> tuple[str, ...]:
        return ("-f", "x11grab", "-i", ":0.0")


@dataclass(frozen=True)
class SubclassedSource(SyntheticSource):
    """A subclass of the real source: refused too (it could override anything)."""


def forged(pattern: str) -> SyntheticSource:
    """A real source whose pattern was changed after its validation."""
    source = SyntheticSource()
    object.__setattr__(source, "pattern", pattern)
    return source


@pytest.mark.parametrize(
    "source",
    [LookAlikeSource(), SubclassedSource(), "testsrc2", None],
    ids=["look-alike", "subclass", "string", "none"],
)
def test_only_the_exact_synthetic_source_type_is_accepted(source: object) -> None:
    with pytest.raises(InvalidScreenCastOption, match="only the synthetic test source"):
        EncodeRequest(source, VideoProfile(), EncoderPreference.SOFTWARE_ONLY)  # type: ignore[arg-type]
    with pytest.raises(InvalidScreenCastOption, match="only the synthetic test source"):
        source_arguments(source, VideoProfile())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "pattern", ["x11grab", "movie=/etc/passwd", "testsrc2,format=rgb24", "testsrc2:size=1x1"]
)
def test_a_pattern_forged_after_validation_never_reaches_the_command(pattern: str) -> None:
    with pytest.raises(InvalidScreenCastOption, match="unknown synthetic pattern"):
        encode_command("ffmpeg", forged(pattern), VideoProfile(), SoftwareX264(), OUT)


def test_the_adapter_alone_turns_the_source_into_input_arguments() -> None:
    source = SyntheticSource("smptebars")

    assert source_arguments(source, VideoProfile(width=640, height=480, fps=25)) == (
        "-f",
        "lavfi",
        "-i",
        "smptebars=size=640x480:rate=25",
    )
    assert not hasattr(source, "input_arguments")  # a specification, not a command


def test_a_request_is_rebuilt_and_checked_again_from_plain_values() -> None:
    request = EncodeRequest(SyntheticSource(), VideoProfile(), EncoderPreference.VAAPI_ONLY)

    assert request.revalidated() == request

    for name, value in [
        ("source", LookAlikeSource()),
        ("source", forged("x11grab")),
        ("qp", 0),
        ("preference", "anything"),
        ("device", VaapiDevice.__new__(VaapiDevice)),
    ]:
        broken = EncodeRequest(SyntheticSource(), VideoProfile(), EncoderPreference.VAAPI_ONLY)
        object.__setattr__(broken, name, value)
        with pytest.raises(InvalidScreenCastOption):
            broken.revalidated()

    profile = VideoProfile()
    object.__setattr__(profile, "fps", "30:x")
    broken = EncodeRequest(SyntheticSource(), VideoProfile(), EncoderPreference.VAAPI_ONLY)
    object.__setattr__(broken, "profile", profile)
    with pytest.raises(InvalidScreenCastOption, match="fps"):
        broken.revalidated()


def test_every_command_is_bounded_in_time_and_size() -> None:
    argv = encode_command(
        "ffmpeg", SyntheticSource(), VideoProfile(duration_seconds=120), SoftwareX264(), OUT
    )

    assert argv[argv.index("-t") + 1] == "120"
    assert argv[argv.index("-fs") + 1] == str(MAX_OUTPUT_BYTES)
    assert SyntheticSource().kind is SourceKind.SYNTHETIC
