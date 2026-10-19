"""D1 path A assembled: LAN address choice, one bounded duration, every end of a session."""

from __future__ import annotations

import http.client
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from types import TracebackType
from urllib.parse import urlsplit

import pytest

from control_tv.screen_streaming import (
    InvalidStreamConfiguration,
    PublishedStream,
    ReceiverTarget,
    StreamStartError,
    d1,
)
from control_tv.screen_streaming.h264_encoder import D0LiveEncoder
from live_encoder_fakes import FakeRunner, encoder


class FakeSocket:
    def __init__(self, local: str, error: OSError | None = None) -> None:
        self.local = local
        self.error = error
        self.connected: tuple[str, int] | None = None

    def __enter__(self) -> FakeSocket:
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def connect(self, address: tuple[str, int]) -> None:
        if self.error is not None:
            raise self.error
        self.connected = address

    def getsockname(self) -> tuple[str, int]:
        return self.local, 40000


def test_the_lan_address_is_the_one_that_reaches_the_tv() -> None:
    probe = FakeSocket("192.168.1.20")
    assert d1.lan_address_towards("192.168.1.30", lambda family, kind: probe) == "192.168.1.20"  # type: ignore[arg-type,return-value]
    assert probe.connected == ("192.168.1.30", 9)


def test_a_public_or_unreachable_route_is_refused() -> None:
    with pytest.raises(InvalidStreamConfiguration, match="not a home-network address"):
        d1.lan_address_towards("x", lambda family, kind: FakeSocket("8.8.4.4"))  # type: ignore[arg-type,return-value]
    unreachable = FakeSocket("0.0.0.0", OSError("network is unreachable"))
    with pytest.raises(InvalidStreamConfiguration, match="no route to the TV"):
        d1.lan_address_towards("x", lambda family, kind: unreachable)  # type: ignore[arg-type,return-value]


def test_the_no_receiver_only_shows_the_url() -> None:
    shown: list[str] = []
    receiver = d1.NoReceiver(shown.append)
    session = receiver.start(
        ReceiverTarget("no-tv"), PublishedStream("http://127.0.0.1:1/t/index.m3u8"), timeout=1
    )
    receiver.stop(session)
    assert shown == ["http://127.0.0.1:1/t/index.m3u8"]


@pytest.mark.parametrize("seconds", [0, -1, d1.MAX_D1_SECONDS + 1, True, 2.5])
def test_the_duration_is_bounded(seconds: int) -> None:
    with pytest.raises(InvalidStreamConfiguration, match="seconds"):
        d1.assemble(
            seconds=seconds, bind_address="127.0.0.1", receiver=d1.NoReceiver(print), device_id="x"
        )


def test_one_duration_bounds_the_encoder_and_a_little_longer_the_publication() -> None:
    session = d1.assemble(
        seconds=30, bind_address="127.0.0.1", receiver=d1.NoReceiver(print), device_id="x"
    )
    assert session.encoder._settings.max_session_seconds == 30  # FFmpeg's -t
    assert session.request.publication.max_session_seconds == 30 + d1.PUBLICATION_MARGIN_SECONDS


class Clock:
    def __init__(self, on_sleep: object = None) -> None:
        self.now = 0.0
        self.on_sleep = on_sleep

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        if callable(self.on_sleep):
            self.on_sleep()
        self.now += seconds


def fake_session(tmp_path: Path, seconds: int = 3) -> tuple[d1.D1Session, FakeRunner, list[str]]:
    runner = FakeRunner()
    shown: list[str] = []
    session = d1.assemble(
        seconds=seconds,
        bind_address="127.0.0.1",
        receiver=d1.NoReceiver(shown.append),
        device_id="no-tv",
        encoder=encoder(tmp_path, runner),
    )
    return session, runner, shown


def test_a_session_ends_at_its_duration_and_stops_everything(tmp_path: Path) -> None:
    session, runner, shown = fake_session(tmp_path)
    clock = Clock()

    assert session.run(clock.time, clock.sleep) == "duration"

    assert len(shown) == 1 and clock.now >= 3
    assert session.encoder.unreleased == () and session.publisher.unreleased == ()
    assert runner.group.releases == 1 and list(tmp_path.iterdir()) == []


def test_a_session_ends_as_soon_as_the_encoder_ends_by_itself(tmp_path: Path) -> None:
    session, runner, _ = fake_session(tmp_path, seconds=60)
    clock = Clock(on_sleep=lambda: setattr(runner.group, "ended", True))

    assert session.run(clock.time, clock.sleep) == "encoder ended"

    assert clock.now < 60 and session.publisher.unreleased == ()
    assert runner.group.releases == 1


def test_ctrl_c_ends_the_session_and_stops_everything(tmp_path: Path) -> None:
    session, runner, _ = fake_session(tmp_path, seconds=60)

    def interrupted() -> None:
        raise KeyboardInterrupt

    clock = Clock(on_sleep=interrupted)
    assert session.run(clock.time, clock.sleep) == "interrupted"
    assert session.publisher.unreleased == () and runner.group.releases == 1


def test_the_command_reports_the_end_or_the_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    session, runner, _ = fake_session(tmp_path, seconds=1)
    runner.group.ended = True  # FFmpeg already gone: the session ends at once
    monkeypatch.setattr(d1, "assemble", lambda **options: session)

    assert d1.main(["--no-tv"]) == 0
    assert "session ended: encoder ended; everything stopped" in capsys.readouterr().out

    def failing(**options: object) -> d1.D1Session:
        raise StreamStartError("no live encoder can run")

    monkeypatch.setattr(d1, "assemble", failing)
    assert d1.main(["--no-tv"]) == 1
    assert "error: no live encoder can run" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        d1.main([])  # --no-tv or --device is required


def _ffmpeg_has_libx264() -> bool:
    if shutil.which("ffmpeg") is None:
        return False
    listing = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, check=False
    )
    return " libx264 " in listing.stdout


@pytest.mark.skipif(not _ffmpeg_has_libx264(), reason="FFmpeg with libx264 is not installed")
def test_a_real_synthetic_session_serves_h264_hls_on_loopback(tmp_path: Path) -> None:
    """A real FFmpeg process, the real publisher on 127.0.0.1, no TV: the playlist and a segment
    are served while the session runs, and nothing remains afterwards."""
    urls: list[str] = []
    ready = threading.Event()

    def show(uri: str) -> None:
        urls.append(uri)
        ready.set()

    session = d1.assemble(
        seconds=6, bind_address="127.0.0.1", receiver=d1.NoReceiver(show), device_id="no-tv"
    )
    assert isinstance(session.encoder, D0LiveEncoder)
    ended: list[str] = []
    runner = threading.Thread(target=lambda: ended.append(session.run()))
    runner.start()
    assert ready.wait(20)
    parts = urlsplit(urls[0])
    assert parts.hostname == "127.0.0.1" and parts.port is not None

    playlist = b""
    deadline = time.monotonic() + 15
    while b".ts" not in playlist and time.monotonic() < deadline:
        connection = http.client.HTTPConnection("127.0.0.1", parts.port, timeout=5)
        connection.request("GET", parts.path)
        response = connection.getresponse()
        playlist = response.read() if response.status == 200 else b""
        time.sleep(0.3)
    assert playlist.startswith(b"#EXTM3U")
    segment = next(line for line in playlist.decode().splitlines() if line.endswith(".ts"))
    connection = http.client.HTTPConnection("127.0.0.1", parts.port, timeout=5)
    connection.request("GET", parts.path.rsplit("/", 1)[0] + "/" + segment)
    body = connection.getresponse().read()
    assert body[:1] == b"\x47"  # an MPEG-TS packet

    runner.join(30)
    assert ended == ["duration"] or ended == ["encoder ended"]
    assert session.encoder.unreleased == () and session.publisher.unreleased == ()
    with pytest.raises(ConnectionRefusedError):
        socket.create_connection(("127.0.0.1", parts.port), timeout=2).close()
