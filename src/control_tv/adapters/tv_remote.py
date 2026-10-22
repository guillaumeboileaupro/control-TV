"""The Android TV Remote channel of a Google TV: audible volume, mute, power and navigation.

A Google TV whose Cast volume is fixed ignores Cast volume and mute: the sound the owner
hears is set by the TV itself. The TV's own remote uses another channel, the Android TV
Remote protocol v2 (TLS on ports 6466/6467), which this adapter speaks through the optional
`androidtvremote2` library (`control-tv[tvremote]`).

Rules this adapter keeps:

- Pairing is explicit. The TV shows a code; the owner types it here. The client certificate
  and key live in a private directory (0700) as private files (0600); nothing else is kept.
- Only an allowlist of keys can be sent (`RemoteKey`), and text only to the TV's active text
  field (`type_text`), never logged or echoed. App launch and voice are not exposed here.
- One short connection per operation: connect, act, flush, close. No connection stays open
  and nothing listens, so there is no background session that could drop a key silently.
- A key is sent once. A failure after the key was handed to the connection is ambiguous: the
  TV may have acted. The adapter never resends; it reports what the TV said afterwards.
- What the TV reports (power, audible volume level and mute) is returned as observed;
  nothing is inferred from the key that was pressed.
- Error messages never contain a TV address.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import importlib.util
import ipaddress
import os
import re
import stat
import threading
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from pathlib import Path
from typing import Protocol, TypeVar, cast

from control_tv.domain import (
    CommandRejectedError,
    ControlError,
    DeviceUnavailableError,
    InvalidArgumentError,
    OperationTimeoutError,
    PairingRequiredError,
    UnsupportedOperationError,
)
from control_tv.domain.remote import (
    MAX_TEXT_LENGTH,
    VOLUME_KEYS,
    AudibleVolume,
    KeyPress,
    RemoteKey,
    RemoteObservation,
    TextEntry,
)

CLIENT_NAME = "control-TV"
CERT_FILE = "client-cert.pem"
KEY_FILE = "client-key.pem"
DEFAULT_TIMEOUT = 8.0
DEFAULT_SETTLE = 1.0
PAIRING_CODE = re.compile(r"[0-9A-Fa-f]{6}")
_SETTLE_STEP = 0.05

T = TypeVar("T")


class RemoteClient(Protocol):
    @property
    def is_on(self) -> object: ...

    @property
    def current_app(self) -> object: ...

    @property
    def volume_info(self) -> object: ...

    async def async_start_pairing(self) -> None: ...
    async def async_finish_pairing(self, code: str) -> None: ...
    async def async_connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def send_key_command(self, key: str) -> None: ...
    def send_text(self, text: str) -> None: ...


RemoteFactory = Callable[[str, str, str, str], RemoteClient]
CertGenerator = Callable[[str], tuple[bytes, bytes]]


def channel_installed() -> bool:
    """Whether this build embeds the optional library; checked without importing it."""
    try:
        return importlib.util.find_spec("androidtvremote2") is not None
    except (ImportError, ValueError):
        return False


def default_credentials_dir() -> Path:
    """Where this user's client certificate and key live: the user data directory.

    `$XDG_DATA_HOME/control-tv/tv-remote`, or `~/.local/share/control-tv/tv-remote`. The
    embedded Android runtime sets the home directory to the app's private files.
    """
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "control-tv" / "tv-remote"


def _volume(info: object) -> AudibleVolume | None:
    if not isinstance(info, Mapping):
        return None
    level, maximum, muted = info.get("level"), info.get("max"), info.get("muted")
    if (
        isinstance(level, bool)
        or isinstance(maximum, bool)
        or not isinstance(level, int)
        or not isinstance(maximum, int)
        or not isinstance(muted, bool)
        or maximum <= 0
        or not 0 <= level <= maximum
    ):
        return None
    return AudibleVolume(level=level, maximum=maximum, muted=muted)


def _observe(remote: RemoteClient) -> RemoteObservation:
    is_on = remote.is_on
    app = remote.current_app
    return RemoteObservation(
        is_on=is_on if isinstance(is_on, bool) else None,
        volume=_volume(remote.volume_info),
        current_app=app if isinstance(app, str) and app else None,
    )


def _field_reported(remote: RemoteClient) -> bool:
    """Whether the TV reported an active text field (its input-method edit counters).

    The library keeps them on its protocol object only; `androidtvremote2` is pinned, and a
    missing attribute reads as "not reported", never as an active field.
    """
    protocol = getattr(remote, "_remote_message_protocol", None)
    counter = getattr(protocol, "ime_field_counter", 0)
    return isinstance(counter, int) and not isinstance(counter, bool) and counter > 0


_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")


def _checked_text(text: object) -> str:
    """Printable text only, 1 to MAX_TEXT_LENGTH characters. Never echoed in a message."""
    if not isinstance(text, str) or not text:
        raise InvalidArgumentError("the text to type must be a non-empty string")
    if len(text) > MAX_TEXT_LENGTH:
        raise InvalidArgumentError(f"the text to type is limited to {MAX_TEXT_LENGTH} characters")
    if _CONTROL_CHARACTER.search(text):
        raise InvalidArgumentError("the text to type cannot contain control characters")
    return text


def _checked_host(host: str) -> str:
    """An IP address only: the remote channel is reached by the address discovery found."""
    try:
        return str(ipaddress.ip_address(host))
    except (TypeError, ValueError):
        raise InvalidArgumentError("the TV address is not an IP address") from None


def _library_errors() -> tuple[type[Exception], type[Exception], type[Exception]]:
    try:
        from androidtvremote2 import CannotConnect, ConnectionClosed, InvalidAuth
    except ImportError:
        raise UnsupportedOperationError(
            "the Android TV Remote channel is not installed (control-tv[tvremote])"
        ) from None
    return CannotConnect, ConnectionClosed, InvalidAuth


def _default_remote_factory(
    client_name: str, certfile: str, keyfile: str, host: str
) -> RemoteClient:
    from androidtvremote2 import AndroidTVRemote

    # Built inside the running loop, which the library requires. The input method is on: it
    # carries text to the TV's active field and reports the app in front.
    return cast(
        RemoteClient,
        AndroidTVRemote(client_name, certfile, keyfile, host, enable_ime=True),
    )


def _default_cert_generator(client_name: str) -> tuple[bytes, bytes]:
    from androidtvremote2.certificate_generator import generate_selfsigned_cert

    return generate_selfsigned_cert(client_name)


class TvRemoteChannel:
    """A synchronous facade over the Android TV Remote protocol, one TV address per call.

    Calls are serialized on one private event loop thread. The facade itself holds no
    connection between calls, except a pairing that was started and not yet finished.
    """

    def __init__(
        self,
        credentials_dir: Path,
        *,
        client_name: str = CLIENT_NAME,
        timeout: float = DEFAULT_TIMEOUT,
        settle: float = DEFAULT_SETTLE,
        remote_factory: RemoteFactory = _default_remote_factory,
        cert_generator: CertGenerator = _default_cert_generator,
    ) -> None:
        self._dir = Path(credentials_dir)
        self._client_name = client_name
        self._timeout = timeout
        self._settle = settle
        self._remote_factory = remote_factory
        self._cert_generator = cert_generator
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pairing: dict[str, RemoteClient] = {}

    # Public operations ---------------------------------------------------------------------

    def start_pairing(self, host: str) -> None:
        """Ask the TV to show a pairing code. Nothing else is sent to it."""
        host = _checked_host(host)

        async def start() -> None:
            self._discard_pairing(host)
            remote = self._remote_factory(self._client_name, *self._credentials(), host)
            try:
                await remote.async_start_pairing()
            except BaseException:
                remote.disconnect()
                raise
            self._pairing[host] = remote

        self._run(start)

    def finish_pairing(self, host: str, code: str) -> None:
        """Send the code the TV shows; the TV accepts it (paired) or refuses it."""
        host = _checked_host(host)
        if not isinstance(code, str) or not PAIRING_CODE.fullmatch(code.strip()):
            raise InvalidArgumentError("the pairing code is the 6 characters shown on the TV")
        _, _, invalid_auth = _library_errors()

        async def finish() -> None:
            remote = self._pairing.pop(host, None)
            if remote is None:
                raise PairingRequiredError("start pairing first: the TV is not showing a code")
            try:
                await remote.async_finish_pairing(code.strip().upper())
            except invalid_auth:
                raise CommandRejectedError("the TV refused the pairing code") from None
            finally:
                remote.disconnect()

        self._run(finish)

    def observe(self, host: str) -> RemoteObservation:
        """Connect, read what the TV reports (power, audible volume), and close."""
        host = _checked_host(host)

        async def read(remote: RemoteClient) -> RemoteObservation:
            await self._wait_for(lambda: remote.volume_info is not None)
            return _observe(remote)

        return self._run(lambda: self._session(host, read))

    def press(self, host: str, key: RemoteKey) -> KeyPress:
        """Send one key once, and report what the TV said before and after it.

        For a volume key the TV normally reports its new volume; the adapter waits for that
        report a short time, then returns whatever the TV said, changed or not.
        """
        host = _checked_host(host)
        if not isinstance(key, RemoteKey):
            raise InvalidArgumentError(f"not an allowed key: {key!r}")

        async def send(remote: RemoteClient) -> KeyPress:
            if key in VOLUME_KEYS:  # the volume it changes; other keys are not delayed
                await self._wait_for(lambda: remote.volume_info is not None)
            before = _observe(remote)
            remote.send_key_command(key.value)
            if key in VOLUME_KEYS:
                await self._wait_for(lambda: _observe(remote).volume != before.volume)
            return KeyPress(key=key, before=before, after=_observe(remote))

        return self._run(lambda: self._session(host, send))

    def type_text(self, host: str, text: str) -> TextEntry:
        """Send `text` once to the TV's active text field, replacing what the field holds.

        The adapter waits a short time for the TV to report an active field. It sends the text
        exactly once only when that field exists. The text never appears in an error or a result.
        """
        host = _checked_host(host)
        text = _checked_text(text)

        async def send(remote: RemoteClient) -> TextEntry:
            await self._wait_for(lambda: _field_reported(remote))
            reported = _field_reported(remote)
            if reported:
                remote.send_text(text)
            return TextEntry(length=len(text), field_reported=reported, observed=_observe(remote))

        return self._run(lambda: self._session(host, send))

    def close(self) -> None:
        """Drop a pending pairing and stop the event loop thread."""
        with self._lock:
            loop, self._loop = self._loop, None
        if loop is None:
            return

        async def drop() -> None:
            for host in list(self._pairing):
                self._discard_pairing(host)

        asyncio.run_coroutine_threadsafe(drop(), loop).result(self._timeout)
        loop.call_soon_threadsafe(loop.stop)

    # Internals ---------------------------------------------------------------------------

    async def _session(self, host: str, action: Callable[[RemoteClient], Awaitable[T]]) -> T:
        remote = self._remote_factory(self._client_name, *self._credentials(), host)
        try:
            await remote.async_connect()
            return await action(remote)
        finally:
            # Closing the connection flushes what was written to it before closing.
            remote.disconnect()

    async def _wait_for(self, condition: Callable[[], bool]) -> None:
        waited = 0.0
        while not condition() and waited < self._settle:
            await asyncio.sleep(_SETTLE_STEP)
            waited += _SETTLE_STEP

    def _discard_pairing(self, host: str) -> None:
        remote = self._pairing.pop(host, None)
        if remote is not None:
            remote.disconnect()

    def _credentials(self) -> tuple[str, str]:
        """The client certificate and key, created once in a private directory."""
        self._dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self._dir, 0o700)
        cert, key = self._dir / CERT_FILE, self._dir / KEY_FILE
        if not (cert.is_file() and key.is_file()):
            cert_pem, key_pem = self._cert_generator(self._client_name)
            _write_private(key, key_pem)
            _write_private(cert, cert_pem)
        for path in (cert, key):
            if stat.S_IMODE(path.stat().st_mode) != 0o600:
                os.chmod(path, 0o600)
        return str(cert), str(key)

    def _run(self, make: Callable[[], Coroutine[object, object, T]]) -> T:
        cannot_connect, connection_closed, invalid_auth = _library_errors()
        future = asyncio.run_coroutine_threadsafe(make(), self._event_loop())
        try:
            return future.result(self._timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise OperationTimeoutError("the TV did not answer in time") from None
        except ControlError:
            raise
        except invalid_auth:
            raise PairingRequiredError("this TV must be paired with control-TV first") from None
        except (cannot_connect, connection_closed, OSError):
            raise DeviceUnavailableError("the TV's remote channel could not be reached") from None

    def _event_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is None:
                loop = asyncio.new_event_loop()
                threading.Thread(
                    target=loop.run_forever, name="control-tv-remote", daemon=True
                ).start()
                self._loop = loop
            return self._loop


def _write_private(path: Path, content: bytes) -> None:
    """Write a file readable by its owner only, replacing an older one atomically."""
    partial = path.with_name(path.name + ".partial")
    partial.unlink(missing_ok=True)
    fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(content)
    os.replace(partial, path)
