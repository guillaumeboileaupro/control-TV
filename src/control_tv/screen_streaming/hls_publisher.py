"""`StreamPublisher` for D1: the encoder's live HLS output served on the home network.

One small HTTP server per publication, bound to one private, link-local or loopback IPv4
address the composition root chooses (the address the TV reaches this computer on). Never a
public address or every interface; no port is opened on the router (no UPnP), no tunnel, no
cloud. The URL carries a random token, so a host of the same network that does not know it gets
nothing (RD8). Only the playlist and the `.ts` segments of the publication's own directory are
served: no listing, no other file, never through a symbolic link, GET and HEAD only, and no
request is logged (no client address in any log). Each publication ends by itself after its
`max_session_seconds`; `stop()` releases it (idempotent, never twice).

The HLS directory comes from the encoder through the injected `output_of` (for D0, the
`D0LiveEncoder.output` lookup), so the ports stay as they are. Starting this publisher is not a
reception by the TV: that is the receiver's job and is validated separately on hardware.
"""

from __future__ import annotations

import contextlib
import ipaddress
import os
import re
import secrets
import stat
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Protocol

from control_tv.screen_streaming.model import (
    EncodedStream,
    InvalidStreamConfiguration,
    PublicationConfig,
    PublicationKind,
    PublishedStream,
    StreamCleanupError,
    StreamStartError,
)

PLAYLIST_TYPE = "application/vnd.apple.mpegurl"
SEGMENT_TYPE = "video/mp2t"
_SERVED = re.compile(r"[A-Za-z0-9_-]{1,64}\.(m3u8|ts)")
_TOKEN = re.compile(r"[A-Za-z0-9_-]{22,128}")
_CONTENT_TYPES = {"m3u8": PLAYLIST_TYPE, "ts": SEGMENT_TYPE}
_STOP_TIMEOUT = 5.0


def _new_token() -> str:
    return secrets.token_urlsafe(24)


def home_network_address(value: str) -> str:
    """`value` if it is an IPv4 address of this computer's home network side: private,
    link-local or loopback. Every interface (0.0.0.0), a public address and IPv6 are refused."""
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise InvalidStreamConfiguration(f"not an IP address: {value!r}") from None
    if address.version != 4:
        raise InvalidStreamConfiguration("the HLS publisher binds one IPv4 address only")
    if address.is_unspecified or not (
        address.is_private or address.is_loopback or address.is_link_local
    ):
        raise InvalidStreamConfiguration(
            f"{value} is not a home-network address (private, link-local or loopback)"
        )
    return str(address)


class HlsOutput(Protocol):
    """What the publisher needs from the encoder (D0's `LiveOutput` is one): the HLS directory
    and its playlist file."""

    @property
    def directory(self) -> Path: ...

    @property
    def playlist(self) -> Path: ...


class _PublicationServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, address: tuple[str, int], token: str, directory: Path) -> None:
        self.token = token
        self.directory = directory
        super().__init__(address, _HlsRequestHandler)


def _expire(server: _PublicationServer) -> None:
    """The session bound: stop serving and close the socket; `stop()` still releases the rest."""
    server.shutdown()
    with contextlib.suppress(OSError):
        server.server_close()


class _HlsRequestHandler(BaseHTTPRequestHandler):
    server_version = "control-tv"
    sys_version = ""
    timeout = 10  # a stalled client cannot hold its thread forever

    def log_message(self, format: str, *args: object) -> None:
        """No request log: it would name the client's address."""

    def do_GET(self) -> None:
        self._serve(body=True)

    def do_HEAD(self) -> None:
        self._serve(body=False)

    def do_POST(self) -> None:
        self._refuse(405)

    do_PUT = do_POST
    do_DELETE = do_POST
    do_PATCH = do_POST

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _cors(self) -> None:
        # The Cast receiver fetches the playlist and segments from its own web origin.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")

    def _refuse(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _serve(self, *, body: bool) -> None:
        server = self.server
        if not isinstance(server, _PublicationServer):  # pragma: no cover - never otherwise
            self._refuse(500)
            return
        content = self._file(server)
        if content is None:
            self._refuse(404)  # the same answer for a wrong token and an absent file
            return
        data, content_type = content
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if body:
            self.wfile.write(data)

    def _file(self, server: _PublicationServer) -> tuple[bytes, str] | None:
        path = self.path.split("?", 1)[0]
        parts = path.split("/")
        if len(parts) != 3 or parts[0] or not secrets.compare_digest(parts[1], server.token):
            return None
        name = parts[2]
        match = _SERVED.fullmatch(name)
        if match is None:
            return None
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        try:
            descriptor = os.open(server.directory / name, flags)
        except OSError:
            return None
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                return None
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                return stream.read(), _CONTENT_TYPES[match.group(1)]
        except OSError:
            return None  # removed or rotated meanwhile (rolling playlist): not served
        finally:
            os.close(descriptor)


@dataclass
class _Publication:
    server: _PublicationServer
    thread: threading.Thread
    expiry: threading.Timer
    stopping: threading.Lock = field(default_factory=threading.Lock)


class HlsPublisher:
    """`StreamPublisher` serving one live HLS directory per publication on the home network."""

    def __init__(
        self,
        output_of: Callable[[EncodedStream], HlsOutput],
        *,
        bind_address: str,
        token_factory: Callable[[], str] = _new_token,
    ) -> None:
        self._output_of = output_of
        self._address = home_network_address(bind_address)
        self._token_factory = token_factory
        self._publications: dict[str, _Publication] = {}
        self._released: set[str] = set()
        self._lock = threading.Lock()

    @property
    def unreleased(self) -> tuple[str, ...]:
        """The published URIs not yet released (their server may still answer)."""
        with self._lock:
            return tuple(self._publications)

    def start(self, stream: EncodedStream, config: PublicationConfig) -> PublishedStream:
        if type(config) is not PublicationConfig or config.kind is not PublicationKind.HLS:
            raise StreamStartError("the HLS publisher publishes HLS only")
        output = self._checked_output(stream)
        token = self._token_factory()
        if not isinstance(token, str) or _TOKEN.fullmatch(token) is None:
            raise StreamStartError("the publication token is not a long URL-safe secret")
        server: _PublicationServer | None = None
        publication: _Publication | None = None
        try:
            server = _PublicationServer((self._address, 0), token, output.directory)
            port = server.server_address[1]
            uri = f"http://{self._address}:{port}/{token}/{output.playlist.name}"
            thread = threading.Thread(
                target=server.serve_forever, name="control-tv-hls", daemon=True
            )
            expiry = threading.Timer(config.max_session_seconds, _expire, (server,))
            expiry.daemon = True
            publication = _Publication(server, thread, expiry)
            thread.start()
            expiry.start()
            with self._lock:
                self._publications[uri] = publication
                self._released.discard(uri)
            return PublishedStream(uri)
        except BaseException as error:
            if publication is not None:
                self._release(publication)
            elif server is not None:
                server.server_close()
            if isinstance(error, OSError):
                raise StreamStartError(
                    f"the HLS publication could not listen: {type(error).__name__}"
                ) from None
            raise

    def stop(self, publication: PublishedStream) -> None:
        """Stop serving and close the listening socket. Idempotent: a publication already
        released succeeds without touching anything; one this publisher never made is refused."""
        with self._lock:
            owned = self._publications.get(publication.uri)
            if owned is None:
                if publication.uri in self._released:
                    return
                raise StreamCleanupError("this publisher owns no such publication")
        with owned.stopping:
            with self._lock:
                if self._publications.get(publication.uri) is not owned:
                    return  # a concurrent stop released it meanwhile
            self._release(owned)
            with self._lock:
                del self._publications[publication.uri]
                self._released.add(publication.uri)

    def ended(self, publication: PublishedStream) -> bool:
        """Whether this owned publication's bounded server has stopped by itself."""
        with self._lock:
            owned = self._publications.get(publication.uri)
        if owned is None:
            raise StreamStartError("this publisher owns no such publication")
        return not owned.thread.is_alive()

    def close(self) -> None:
        """Stop every publication still owned (application exit); every one is attempted."""
        failures: list[str] = []
        for uri in self.unreleased:
            try:
                self.stop(PublishedStream(uri))
            except StreamCleanupError as error:
                failures.append(error.message)
        if failures:
            raise StreamCleanupError("; ".join(failures))

    def _checked_output(self, stream: EncodedStream) -> HlsOutput:
        try:
            output = self._output_of(stream)
        except StreamStartError:
            raise
        except Exception as error:
            raise StreamStartError(
                f"the encoder's HLS output is unknown: {type(error).__name__}"
            ) from None
        directory = output.directory
        playlist = output.playlist
        if playlist.parent != directory or _SERVED.fullmatch(playlist.name) is None:
            raise StreamStartError("the HLS playlist must be a plain file of its directory")
        if not playlist.name.endswith(".m3u8"):
            raise StreamStartError("the HLS playlist must be an .m3u8 file")
        if not directory.is_dir() or directory.is_symlink():
            raise StreamStartError("the HLS directory must be a real directory")
        return output

    @staticmethod
    def _release(publication: _Publication) -> None:
        publication.expiry.cancel()
        if publication.thread.is_alive():
            publication.server.shutdown()
        with contextlib.suppress(OSError):
            publication.server.server_close()
        publication.thread.join(_STOP_TIMEOUT)
