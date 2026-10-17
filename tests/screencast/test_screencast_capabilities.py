"""Encoder probe and selection policy: the software fallback is never implicit."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from control_tv.screencast import capabilities
from control_tv.screencast.capabilities import (
    EncoderCapabilities,
    FfmpegCapabilityProbe,
    select_encoder,
)
from control_tv.screencast.model import (
    EncoderPreference,
    EncoderUnavailableError,
    SoftwareX264,
    SyntheticEncodeRequest,
    SyntheticSource,
    VaapiCqp,
    VaapiDevice,
    VideoProfile,
)

BOTH = frozenset({"h264_vaapi", "libx264"})


def request(preference: EncoderPreference) -> SyntheticEncodeRequest:
    return SyntheticEncodeRequest(SyntheticSource(), VideoProfile(), preference, qp=26, crf=21)


def test_vaapi_is_chosen_with_cqp_when_available() -> None:
    choice = select_encoder(
        request(EncoderPreference.VAAPI_THEN_SOFTWARE), EncoderCapabilities(BOTH, True)
    )

    assert choice.plan == VaapiCqp(VaapiDevice("/dev/dri/renderD128"), qp=26)
    assert choice.fallback_reason is None


@pytest.mark.parametrize(
    ("capabilities", "reason"),
    [
        (EncoderCapabilities(frozenset({"libx264"}), True), "no h264_vaapi encoder"),
        (EncoderCapabilities(BOTH, False), "missing or not accessible"),
    ],
)
def test_the_fallback_is_taken_only_when_allowed_and_says_why(
    capabilities: EncoderCapabilities, reason: str
) -> None:
    choice = select_encoder(request(EncoderPreference.VAAPI_THEN_SOFTWARE), capabilities)

    assert choice.plan == SoftwareX264(crf=21)
    assert choice.fallback_reason is not None and reason in choice.fallback_reason
    with pytest.raises(EncoderUnavailableError, match="not allowed"):
        select_encoder(request(EncoderPreference.VAAPI_ONLY), capabilities)


def test_no_encoder_at_all_is_reported() -> None:
    nothing = EncoderCapabilities(frozenset(), False)

    with pytest.raises(EncoderUnavailableError, match="no libx264 fallback"):
        select_encoder(request(EncoderPreference.VAAPI_THEN_SOFTWARE), nothing)
    with pytest.raises(EncoderUnavailableError, match="no libx264"):
        select_encoder(request(EncoderPreference.SOFTWARE_ONLY), nothing)


def test_software_only_never_touches_the_gpu() -> None:
    choice = select_encoder(
        request(EncoderPreference.SOFTWARE_ONLY), EncoderCapabilities(BOTH, True)
    )

    assert choice == choice.__class__(SoftwareX264(crf=21))


def test_the_probe_lists_encoders_and_checks_the_render_node() -> None:
    calls: list[tuple[tuple[str, ...], float]] = []

    def run(argv: tuple[str, ...], timeout: float) -> str:
        calls.append((argv, timeout))
        return " V....D h264_vaapi  H.264 (VAAPI)\n V....D libx264  x264\n"

    probe = FfmpegCapabilityProbe(
        "ffmpeg", timeout=3.0, run=run, device_usable=lambda p: p.endswith("128")
    )

    capabilities = probe.probe(VaapiDevice("/dev/dri/renderD128"))

    assert capabilities == EncoderCapabilities(BOTH, True)
    assert calls == [
        (("ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-encoders"), 3.0)
    ]


@pytest.mark.parametrize(
    "error", [FileNotFoundError("ffmpeg"), subprocess.TimeoutExpired("ffmpeg", 3.0)]
)
def test_a_probe_that_cannot_run_ffmpeg_reports_it(error: Exception) -> None:
    def run(argv: tuple[str, ...], timeout: float) -> str:
        raise error

    probe = FfmpegCapabilityProbe(run=run, device_usable=lambda path: True)

    with pytest.raises(EncoderUnavailableError, match="could not list its encoders"):
        probe.probe(VaapiDevice("/dev/dri/renderD128"))


def test_the_default_command_runner_returns_stdout_and_raises_on_failure() -> None:
    assert capabilities._command_output((sys.executable, "-c", "print('ok')"), 10.0) == "ok\n"
    with pytest.raises(subprocess.CalledProcessError):
        capabilities._command_output((sys.executable, "-c", "raise SystemExit(2)"), 10.0)


def test_the_default_device_check_needs_an_existing_readable_writable_node(tmp_path: Path) -> None:
    node = tmp_path / "renderD128"
    node.write_bytes(b"")

    assert capabilities._device_usable(str(node)) is True
    assert capabilities._device_usable(str(tmp_path / "missing")) is False
