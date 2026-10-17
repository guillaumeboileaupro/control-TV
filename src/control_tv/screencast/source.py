"""Video sources. D0 has one: FFmpeg's generated test picture (no desktop, camera or file)."""

from __future__ import annotations

from dataclasses import dataclass

from control_tv.screencast.model import InvalidScreenCastOption, SourceKind, VideoProfile

# FFmpeg's built-in test patterns this source may generate.
SYNTHETIC_PATTERNS = frozenset({"testsrc2", "testsrc", "smptebars", "smptehdbars"})


@dataclass(frozen=True)
class SyntheticSource:
    """A `lavfi` test pattern at the profile's size and rate."""

    pattern: str = "testsrc2"

    def __post_init__(self) -> None:
        if self.pattern not in SYNTHETIC_PATTERNS:
            allowed = ", ".join(sorted(SYNTHETIC_PATTERNS))
            raise InvalidScreenCastOption(
                f"the synthetic pattern must be one of {allowed}: {self.pattern!r}"
            )

    @property
    def kind(self) -> SourceKind:
        return SourceKind.SYNTHETIC

    def input_arguments(self, profile: VideoProfile) -> tuple[str, ...]:
        return (
            "-f",
            "lavfi",
            "-i",
            f"{self.pattern}=size={profile.width}x{profile.height}:rate={profile.fps}",
        )
