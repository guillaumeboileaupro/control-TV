"""D0 service integration without FFmpeg, a GPU, display, network or device.

The executable double keeps the real capability probe, command construction, subprocess group
and service lifecycle in the path. Unit tests cover those pieces separately; these tests protect
their wiring.
"""

from __future__ import annotations

import stat
import textwrap
from collections.abc import Callable
from pathlib import Path

from control_tv.screencast import (
    EncodeRequest,
    EncoderPreference,
    FfmpegCapabilityProbe,
    ScreenCastService,
    ScreenCastState,
    SoftwareX264,
    SubprocessRunner,
    SyntheticSource,
    VideoProfile,
)


def executable_double(tmp_path: Path, *, wait: bool = False) -> Path:
    program = tmp_path / "ffmpeg-double"
    behavior = "time.sleep(30)" if wait else "pathlib.Path(sys.argv[-1]).write_bytes(b'h264')"
    program.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env python3
            import pathlib
            import sys
            import time

            if "-encoders" in sys.argv:
                print(" V..... h264_vaapi H.264 / AVC")
                print(" V..... libx264 H.264 / AVC")
                raise SystemExit(0)
            {behavior}
            """
        ),
        encoding="utf-8",
    )
    program.chmod(program.stat().st_mode | stat.S_IXUSR)
    return program


def request() -> EncodeRequest:
    return EncodeRequest(
        SyntheticSource("testsrc2"),
        VideoProfile(width=64, height=64, fps=1, duration_seconds=1),
        EncoderPreference.VAAPI_THEN_SOFTWARE,
    )


def workdir_factory(path: Path) -> tuple[Path, Callable[[], Path]]:
    def create() -> Path:
        path.mkdir()
        return path

    return path, create


def test_service_selects_starts_waits_and_cleans_through_real_ports(tmp_path: Path) -> None:
    executable = executable_double(tmp_path)
    workdir, make_workdir = workdir_factory(tmp_path / "session")
    service = ScreenCastService(
        SubprocessRunner(),
        FfmpegCapabilityProbe(str(executable), device_usable=lambda _path: False),
        ffmpeg=str(executable),
        make_workdir=make_workdir,
    )

    with service:
        choice = service.start(request())
        result = service.wait(5)
        assert choice.plan == SoftwareX264()
        assert (
            choice.fallback_reason
            == "the VA-API render node /dev/dri/renderD128 is missing or not accessible"
        )
        assert result.encoder == choice.plan and result.output.read_bytes() == b"h264"
        assert service.state is ScreenCastState.STOPPED
    assert not workdir.exists()


def test_service_stops_and_cleans_a_running_real_process_group(tmp_path: Path) -> None:
    executable = executable_double(tmp_path, wait=True)
    workdir, make_workdir = workdir_factory(tmp_path / "session")
    service = ScreenCastService(
        SubprocessRunner(),
        FfmpegCapabilityProbe(str(executable), device_usable=lambda _path: False),
        ffmpeg=str(executable),
        stop_grace_seconds=0.2,
        make_workdir=make_workdir,
    )

    with service:
        service.start(request())
        service.stop()
        assert service.state is ScreenCastState.STOPPED
        assert service.unstoppable_pids == ()
    assert not workdir.exists()
