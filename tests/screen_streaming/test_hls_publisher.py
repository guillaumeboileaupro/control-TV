"""The D1 HLS publisher on the loopback interface only: no other host, no TV, no capture."""

from __future__ import annotations

import http.client
import os
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from control_tv.screen_streaming import (
    EncodedStream,
    InvalidStreamConfiguration,
    PublicationConfig,
    PublicationKind,
    PublishedStream,
    StreamCleanupError,
    StreamStartError,
)
from control_tv.screen_streaming.hls_publisher import (
    PLAYLIST_TYPE,
    SEGMENT_TYPE,
    HlsPublisher,
    home_network_address,
)

HLS = PublicationConfig(PublicationKind.HLS)
STREAM = EncodedStream("live-1")


@dataclass(frozen=True)
class Output:
    directory: Path
    playlist: Path


def hls_directory(root: Path, name: str = "live") -> Output:
    directory = root / name
    directory.mkdir(mode=0o700)
    (directory / "index.m3u8").write_text("#EXTM3U\n#EXTINF:2.0,\nsegment-00000.ts\n")
    (directory / "segment-00000.ts").write_bytes(b"\x47" * 188)
    (directory / "stderr.log").write_text("ffmpeg private log\n")
    return Output(directory, directory / "index.m3u8")


def publisher(output: Output, **options: object) -> HlsPublisher:
    return HlsPublisher(lambda stream: output, bind_address="127.0.0.1", **options)  # type: ignore[arg-type]


def held(hls: HlsPublisher) -> tuple[str, ...]:
    """Reads `unreleased` through a call, so each check sees the current value."""
    return hls.unreleased


def get(uri: str, path: str | None = None, method: str = "GET") -> http.client.HTTPResponse:
    parts = urlsplit(uri)
    assert parts.hostname is not None and parts.port is not None
    connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=5)
    connection.request(method, parts.path if path is None else path)
    return connection.getresponse()


def token_and_port(uri: str) -> tuple[str, int]:
    parts = urlsplit(uri)
    assert parts.port is not None
    return parts.path.split("/")[1], parts.port


def refused(uri: str) -> bool:
    _, port = token_and_port(uri)
    try:
        socket.create_connection(("127.0.0.1", port), timeout=2).close()
    except ConnectionRefusedError:
        return True
    return False


def test_the_playlist_and_its_segments_are_served_with_their_types(tmp_path: Path) -> None:
    hls = publisher(hls_directory(tmp_path))
    published = hls.start(STREAM, HLS)
    try:
        assert published.uri.startswith("http://127.0.0.1:") and published.uri.endswith(
            "/index.m3u8"
        )
        playlist = get(published.uri)
        assert playlist.status == 200 and playlist.getheader("Content-Type") == PLAYLIST_TYPE
        assert playlist.getheader("Access-Control-Allow-Origin") == "*"
        assert playlist.read().startswith(b"#EXTM3U")
        token, _ = token_and_port(published.uri)
        segment = get(published.uri, f"/{token}/segment-00000.ts")
        assert segment.status == 200 and segment.getheader("Content-Type") == SEGMENT_TYPE
        assert segment.read() == b"\x47" * 188
        head = get(published.uri, method="HEAD")
        assert head.status == 200 and head.read() == b""
        options = get(published.uri, method="OPTIONS")
        assert options.status == 204 and options.getheader("Access-Control-Allow-Origin") == "*"
    finally:
        hls.stop(published)


def test_only_the_token_and_the_publication_files_are_answered(tmp_path: Path) -> None:
    output = hls_directory(tmp_path)
    (tmp_path / "secret.ts").write_bytes(b"outside")
    (output.directory / "linked.ts").symlink_to(tmp_path / "secret.ts")
    (output.directory / "folder.ts").mkdir()
    hls = publisher(output)
    published = hls.start(STREAM, HLS)
    token, _ = token_and_port(published.uri)
    try:
        for path in (
            "/wrong-token-0123456789abcdef/index.m3u8",  # another host without the token
            f"/{token}/stderr.log",  # the encoder's own files
            f"/{token}/",  # no listing
            f"/{token}",
            f"/{token}/../secret.ts",
            f"/{token}/%2e%2e%2fsecret.ts",
            f"/{token}/sub/index.m3u8",
            f"/{token}/linked.ts",  # never through a link
            f"/{token}/folder.ts",
            f"/{token}/absent.ts",
            "/",
        ):
            response = get(published.uri, path)
            assert response.status == 404, path
            response.read()
        assert get(published.uri, f"/{token}/index.m3u8?x=1").status == 200
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            assert get(published.uri, method=method).status == 405
    finally:
        hls.stop(published)


def test_stop_closes_the_port_and_is_idempotent(tmp_path: Path) -> None:
    hls = publisher(hls_directory(tmp_path))
    published = hls.start(STREAM, HLS)
    assert held(hls) == (published.uri,)

    hls.stop(published)
    hls.stop(published)  # already released: confirmed, nothing touched

    assert refused(published.uri) and held(hls) == ()
    with pytest.raises(StreamCleanupError, match="owns no such publication"):
        hls.stop(PublishedStream("http://127.0.0.1:1/never/index.m3u8"))


def test_two_publications_are_isolated(tmp_path: Path) -> None:
    first, second = hls_directory(tmp_path, "a"), hls_directory(tmp_path, "b")
    (second.directory / "index.m3u8").write_text("#EXTM3U\n# second\n")
    outputs = {"live-a": first, "live-b": second}
    hls = HlsPublisher(lambda stream: outputs[stream.handle], bind_address="127.0.0.1")
    a = hls.start(EncodedStream("live-a"), HLS)
    b = hls.start(EncodedStream("live-b"), HLS)
    try:
        token_a, port_a = token_and_port(a.uri)
        token_b, port_b = token_and_port(b.uri)
        assert port_a != port_b and token_a != token_b
        assert b"second" in get(b.uri).read() and b"second" not in get(a.uri).read()
        crossed = get(a.uri, f"/{token_b}/index.m3u8")  # B's token on A's server
        assert crossed.status == 404
        hls.stop(a)
        assert refused(a.uri) and get(b.uri).status == 200  # B keeps serving
    finally:
        hls.close()
    assert refused(b.uri) and hls.unreleased == ()


def test_a_publication_ends_by_itself_at_its_session_bound(tmp_path: Path) -> None:
    hls = publisher(hls_directory(tmp_path))
    published = hls.start(STREAM, PublicationConfig(PublicationKind.HLS, max_session_seconds=0.2))
    deadline = time.monotonic() + 5
    while not refused(published.uri) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert refused(published.uri)
    hls.stop(published)  # still released exactly once
    assert hls.unreleased == ()


def open_descriptors() -> int:
    return len(os.listdir("/proc/self/fd"))


def test_repeated_starts_and_stops_leak_no_thread_or_socket(tmp_path: Path) -> None:
    hls = publisher(hls_directory(tmp_path))
    hls.stop(hls.start(STREAM, HLS))  # warm-up: lazy imports open nothing later
    threads, descriptors = threading.active_count(), open_descriptors()

    for _ in range(20):
        published = hls.start(STREAM, HLS)
        assert get(published.uri).status == 200
        hls.stop(published)

    deadline = time.monotonic() + 5
    while threading.active_count() > threads and time.monotonic() < deadline:
        time.sleep(0.05)
    assert threading.active_count() <= threads
    assert open_descriptors() <= descriptors
    assert hls.unreleased == ()


@pytest.mark.parametrize(
    "address",
    ["0.0.0.0", "8.8.8.8", "::1", "fe80::1", "localhost", "192.168.1.300", ""],
)
def test_only_one_home_network_ipv4_address_is_accepted(address: str) -> None:
    with pytest.raises(InvalidStreamConfiguration):
        home_network_address(address)


@pytest.mark.parametrize("address", ["127.0.0.1", "192.168.1.20", "10.0.0.5", "169.254.3.4"])
def test_private_link_local_and_loopback_addresses_are_accepted(address: str) -> None:
    assert home_network_address(address) == address


def test_a_non_hls_publication_or_a_weak_token_is_refused(tmp_path: Path) -> None:
    output = hls_directory(tmp_path)
    with pytest.raises(StreamStartError, match="HLS only"):
        publisher(output).start(STREAM, PublicationConfig(PublicationKind.CAST_STREAMING))
    for token in ("short", "has/slash" + "x" * 30, 7):
        weak = publisher(output, token_factory=lambda token=token: token)
        with pytest.raises(StreamStartError, match="token"):
            weak.start(STREAM, HLS)
        assert weak.unreleased == ()


def test_an_unusable_encoder_output_starts_nothing(tmp_path: Path) -> None:
    output = hls_directory(tmp_path)
    cases: list[Callable[[EncodedStream], object]] = [
        lambda stream: Output(output.directory, tmp_path / "index.m3u8"),  # outside its directory
        lambda stream: Output(output.directory, output.directory / "stderr.log"),  # not a playlist
        lambda stream: Output(tmp_path / "absent", tmp_path / "absent" / "index.m3u8"),
    ]
    link = tmp_path / "link"
    link.symlink_to(output.directory, target_is_directory=True)
    cases.append(lambda stream: Output(link, link / "index.m3u8"))
    for output_of in cases:
        hls = HlsPublisher(output_of, bind_address="127.0.0.1")  # type: ignore[arg-type]
        with pytest.raises(StreamStartError, match="HLS"):
            hls.start(STREAM, HLS)
        assert hls.unreleased == ()


def test_the_encoder_lookup_failing_is_a_start_error(tmp_path: Path) -> None:
    def unknown(stream: EncodedStream) -> Output:
        raise KeyError(stream.handle)

    def refused_by_encoder(stream: EncodedStream) -> Output:
        raise StreamStartError("this encoder owns no stream live-1")

    with pytest.raises(StreamStartError, match="output is unknown: KeyError"):
        HlsPublisher(unknown, bind_address="127.0.0.1").start(STREAM, HLS)
    with pytest.raises(StreamStartError, match="owns no stream"):
        HlsPublisher(refused_by_encoder, bind_address="127.0.0.1").start(STREAM, HLS)


@pytest.mark.parametrize("interruption", [KeyboardInterrupt(), SystemExit(3)])
def test_an_interrupted_start_leaves_no_listener(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interruption: BaseException
) -> None:
    hls = publisher(hls_directory(tmp_path))
    threads = threading.active_count()
    started: list[threading.Thread] = []
    real_start = threading.Timer.start

    def interrupted(self: threading.Timer) -> None:
        started.extend(
            thread for thread in threading.enumerate() if thread.name == "control-tv-hls"
        )
        raise interruption

    monkeypatch.setattr(threading.Timer, "start", interrupted)
    with pytest.raises(type(interruption)):
        hls.start(STREAM, HLS)
    monkeypatch.setattr(threading.Timer, "start", real_start)

    assert started and all(not thread.is_alive() for thread in started)  # serving had begun
    assert hls.unreleased == () and threading.active_count() <= threads


def test_a_port_that_cannot_be_opened_is_a_start_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from control_tv.screen_streaming import hls_publisher

    def no_port(*args: object, **kwargs: object) -> None:
        raise OSError("address in use")

    monkeypatch.setattr(hls_publisher._PublicationServer, "__init__", no_port)
    with pytest.raises(StreamStartError, match="could not listen: OSError"):
        publisher(hls_directory(tmp_path)).start(STREAM, HLS)


def test_close_reports_what_it_could_not_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hls = publisher(hls_directory(tmp_path))
    published = hls.start(STREAM, HLS)
    real_stop = hls.stop

    def failing(publication: PublishedStream) -> None:
        raise StreamCleanupError("stuck")

    monkeypatch.setattr(hls, "stop", failing)
    with pytest.raises(StreamCleanupError, match="stuck"):
        hls.close()
    monkeypatch.setattr(hls, "stop", real_stop)
    hls.close()
    assert refused(published.uri)


def test_concurrent_stops_release_a_publication_once(tmp_path: Path) -> None:
    hls = publisher(hls_directory(tmp_path))
    published = hls.start(STREAM, HLS)
    errors: list[BaseException] = []

    def stop() -> None:
        try:
            hls.stop(published)
        except BaseException as error:  # pragma: no cover - reported below
            errors.append(error)

    workers = [threading.Thread(target=stop) for _ in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(5)
    assert errors == [] and hls.unreleased == () and refused(published.uri)


def test_a_segment_that_cannot_be_read_is_not_served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hls = publisher(hls_directory(tmp_path))
    published = hls.start(STREAM, HLS)
    try:

        def rotated(*args: object, **kwargs: object) -> None:
            raise OSError("the rolling playlist deleted it")

        monkeypatch.setattr(os, "fdopen", rotated)
        response = get(published.uri)
        assert response.status == 404
        response.read()
    finally:
        monkeypatch.undo()
        hls.stop(published)


def test_a_failure_after_the_port_opened_closes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from control_tv.screen_streaming import hls_publisher

    opened: list[int] = []
    real_init = hls_publisher._PublicationServer.__init__

    def recording(self: object, *args: object) -> None:
        real_init(self, *args)  # type: ignore[arg-type]
        opened.append(self.server_address[1])  # type: ignore[attr-defined]

    def no_timer(*args: object, **kwargs: object) -> None:
        raise RuntimeError("no timer")

    monkeypatch.setattr(hls_publisher._PublicationServer, "__init__", recording)
    monkeypatch.setattr(threading, "Timer", no_timer)
    with pytest.raises(RuntimeError, match="no timer"):
        publisher(hls_directory(tmp_path)).start(STREAM, HLS)
    monkeypatch.undo()
    assert opened and refused(f"http://127.0.0.1:{opened[0]}/t/index.m3u8")


def test_a_segment_is_not_a_playlist(tmp_path: Path) -> None:
    output = hls_directory(tmp_path)
    segment = Output(output.directory, output.directory / "segment-00000.ts")
    with pytest.raises(StreamStartError, match=r"\.m3u8"):
        HlsPublisher(lambda stream: segment, bind_address="127.0.0.1").start(STREAM, HLS)
