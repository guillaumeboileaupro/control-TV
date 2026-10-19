"""D1 values must stay translatable to the merged D0 encoder (PR #81), so the D1 encoder adapter
can reuse D0 instead of duplicating it. No process, display or network is used."""

from __future__ import annotations

import pytest

from control_tv.screen_streaming import EncodingProfile, SyntheticPattern, SyntheticVideoSource
from control_tv.screencast import SyntheticSource, VideoProfile
from control_tv.screencast.ffmpeg import source_arguments


@pytest.mark.parametrize("pattern", list(SyntheticPattern))
def test_every_d1_synthetic_pattern_is_a_d0_synthetic_source(pattern: SyntheticPattern) -> None:
    d1 = SyntheticVideoSource(pattern)
    d0 = SyntheticSource(d1.pattern.value)

    arguments = source_arguments(d0, VideoProfile(duration_seconds=1))
    assert arguments[:2] == ("-f", "lavfi") and arguments[3].startswith(pattern.value)


@pytest.mark.parametrize(
    "profile",
    [
        EncodingProfile(),
        EncodingProfile(width=16, height=16, fps=1),
        EncodingProfile(width=4096, height=4096, fps=60),
    ],
)
def test_every_d1_encoding_profile_is_a_valid_d0_video_profile(profile: EncodingProfile) -> None:
    d0 = VideoProfile(profile.width, profile.height, profile.fps, duration_seconds=1)
    assert (d0.width, d0.height, d0.fps) == (profile.width, profile.height, profile.fps)
