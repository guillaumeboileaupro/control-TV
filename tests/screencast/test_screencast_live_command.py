"""The live (HLS) argument vector and the shared selection policy: pure, no process started."""

from __future__ import annotations

from pathlib import Path

import pytest

from control_tv.screen_streaming import EncodingProfile
from control_tv.screencast.capabilities import EncoderCapabilities, select_encoder, select_plan
from control_tv.screencast.ffmpeg import live_hls_command
from control_tv.screencast.model import (
    MAX_LIVE_SESSION_SECONDS,
    EncodeRequest,
    EncoderPreference,
    HlsSegmenting,
    InvalidScreenCastOption,
    SoftwareX264,
    SyntheticSource,
    VaapiCqp,
    VaapiDevice,
    VideoProfile,
)

OUT = Path("/private/live")
DEVICE = VaapiDevice("/dev/dri/renderD128")


def test_the_vaapi_live_command_is_constant_qp_hls_with_aligned_key_frames() -> None:
    argv = live_hls_command(
        "ffmpeg",
        SyntheticSource("smptebars"),
        EncodingProfile(width=1280, height=720, fps=25),
        VaapiCqp(DEVICE, qp=26),
        OUT,
        HlsSegmenting(segment_seconds=2, playlist_size=5),
        600,
    )

    assert argv == (
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-vaapi_device",
        "/dev/dri/renderD128",
        "-re",
        "-f",
        "lavfi",
        "-i",
        "smptebars=size=1280x720:rate=25",
        "-t",
        "600",
        "-vf",
        "format=nv12,hwupload",
        "-c:v",
        "h264_vaapi",
        "-rc_mode",
        "CQP",
        "-qp",
        "26",
        "-profile:v",
        "high",
        "-g",
        "50",
        "-keyint_min",
        "50",
        "-bf",
        "0",
        "-f",
        "hls",
        "-hls_time",
        "2",
        "-hls_list_size",
        "5",
        "-hls_flags",
        "delete_segments+independent_segments",
        "-hls_segment_type",
        "mpegts",
        "-hls_segment_filename",
        "/private/live/segment-%05d.ts",
        "-y",
        "/private/live/index.m3u8",
    )
    assert "-b:v" not in argv


def test_the_software_live_command_also_disables_scene_cut_key_frames() -> None:
    argv = live_hls_command(
        "ffmpeg",
        SyntheticSource(),
        EncodingProfile(fps=30),
        SoftwareX264(crf=21),
        OUT,
        HlsSegmenting(segment_seconds=1),
        30,
    )

    assert "-vaapi_device" not in argv and "libx264" in argv
    assert argv[argv.index("-g") + 1] == "30"
    assert argv[argv.index("-sc_threshold") + 1] == "0"


@pytest.mark.parametrize("seconds", [0, MAX_LIVE_SESSION_SECONDS + 1, True, 1.5])
def test_a_live_session_is_always_bounded(seconds: int) -> None:
    with pytest.raises(InvalidScreenCastOption, match="session_seconds"):
        live_hls_command(
            "ffmpeg",
            SyntheticSource(),
            EncodingProfile(),
            SoftwareX264(),
            OUT,
            HlsSegmenting(),
            seconds,
        )


@pytest.mark.parametrize(("segment", "playlist"), [(0, 6), (11, 6), (2, 2), (2, 21), (True, 6)])
def test_hls_segmenting_is_bounded(segment: int, playlist: int) -> None:
    with pytest.raises(InvalidScreenCastOption):
        HlsSegmenting(segment_seconds=segment, playlist_size=playlist)


def test_select_encoder_and_select_plan_are_the_same_policy() -> None:
    capabilities = EncoderCapabilities(frozenset({"libx264"}), False)
    request = EncodeRequest(
        SyntheticSource(), VideoProfile(), EncoderPreference.VAAPI_THEN_SOFTWARE, qp=30, crf=20
    )

    assert select_encoder(request, capabilities) == select_plan(
        EncoderPreference.VAAPI_THEN_SOFTWARE, DEVICE, qp=30, crf=20, capabilities=capabilities
    )


def test_the_synthetic_input_is_read_in_real_time() -> None:
    """Regression: without -re, a 3 s live session was encoded in 0.6 s on the owner's GPU."""
    argv = live_hls_command(
        "ffmpeg", SyntheticSource(), EncodingProfile(), SoftwareX264(), OUT, HlsSegmenting(), 60
    )
    assert argv.index("-re") < argv.index("-i")
