"""The Android TV Remote channel of a Google TV: audible volume, mute, power and navigation.

A Google TV whose Cast volume is fixed ignores Cast volume and mute: the sound the owner
hears is set by the TV itself. The TV's own remote uses another channel, the Android TV
Remote protocol v2 (TLS on ports 6466/6467), which this adapter speaks through the optional
`androidtvremote2` library (`control-tv[tvremote]`).

Rules this adapter keeps:

- Pairing is explicit. The TV shows a code; the owner types it here. A distinct installation
  identity, the client certificate and key live in a private directory (0700) as private files
  (0600); nothing else is kept.
- Only an allowlist of keys can be sent (`RemoteKey`), and text only to the TV's active text
  field (`type_text`), never logged or echoed. Apps are opened through ControlTV Receiver's
  links (`open_receiver_link`): some Google TVs refuse `market://launch` (`ActivityNotFound`,
  recette 2026-10-26), so no such link is ever sent. Voice is a bounded, explicit session whose
  PCM chunks are forwarded in memory and never retained.
- One connection per TV, reused only while it is demonstrably open: the library has not
  reported it lost and its transport is not closing (the TV pings it every 5 s and the
  library closes it after 16 s without a message). It is closed after `KEEP_OPEN` seconds
  without use, on any failure, on pairing and on `close()`; a new one is made on the next
  call. Reconnecting for every key cost a TLS session and the TV's remote handshake per key.
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
import secrets
import stat
import threading
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from contextlib import suppress
from hashlib import sha256
from pathlib import Path
from typing import Protocol, TypeVar, cast

from control_tv.adapters.tv_apps import checked_package
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
IDENTITY_FILE = "client-identity"
CERTIFICATE_IDENTITY_FILE = "certificate-identity"
LEGACY_CERT_FILE = "client-cert.legacy.pem"
LEGACY_KEY_FILE = "client-key.legacy.pem"
DEFAULT_TIMEOUT = 8.0
DEFAULT_SETTLE = 1.0
# A TV that wakes or goes to standby reports its new power state later than a volume change;
# bounded well under DEFAULT_TIMEOUT, which also covers connecting.
DEFAULT_POWER_SETTLE = 4.0
# How long a connection stays open after its last use, for the next key to reuse it.
KEEP_OPEN = 30.0
MAX_VOICE_CHUNK = 20 * 1024
# The only links this adapter opens: ControlTV Receiver's app list and app launch.
_REPLY = r"reply=http://\d{1,3}(?:\.\d{1,3}){3}:\d{1,5}/{path}&token=[A-Za-z0-9_-]{16,64}"
RECEIVER_LINK = re.compile(
    r"controltv-receiver://apps\?"
    + _REPLY.replace("{path}", "apps")
    + r"|controltv-receiver://launch\?package=[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+&"
    + _REPLY.replace("{path}", "launch")
)
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

    @property
    def is_voice_enabled(self) -> object: ...

    async def async_start_pairing(self) -> None: ...
    async def async_finish_pairing(self, code: str) -> None: ...
    async def async_connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def send_key_command(self, key: str) -> None: ...
    def send_text(self, text: str) -> None: ...
    def send_launch_app_command(self, app_link_or_app_id: str) -> None: ...
    async def start_voice(self) -> VoiceSession: ...


class VoiceSession(Protocol):
    def send_chunk(self, chunk: bytes) -> bool: ...

    def end(self) -> None: ...


RemoteFactory = Callable[[str, str, str, str], RemoteClient]
CertGenerator = Callable[[str], tuple[bytes, bytes]]
CertificateNameReader = Callable[[bytes], str]
IdentityGenerator = Callable[[], str]


def _new_identity_suffix() -> str:
    return secrets.token_hex(4)


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


def _open(remote: RemoteClient) -> bool:
    """Whether the library still holds this connection open (lost or closing: not open).

    Read from the library's protocol object, like `_field_reported`; a missing attribute
    reads as "not open", so the next call connects again.
    """
    protocol = getattr(remote, "_remote_message_protocol", None)
    lost = getattr(protocol, "on_con_lost", None)
    transport = getattr(protocol, "transport", None)
    if not isinstance(lost, asyncio.Future) or lost.done() or transport is None:
        return False
    closing = getattr(transport, "is_closing", None)
    return callable(closing) and closing() is False


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
        AndroidTVRemote(client_name, certfile, keyfile, host, enable_ime=True, enable_voice=True),
    )


def _default_cert_generator(client_name: str) -> tuple[bytes, bytes]:
    from androidtvremote2.certificate_generator import generate_selfsigned_cert

    return generate_selfsigned_cert(client_name)


def _default_certificate_name(certificate: bytes) -> str:
    """Return the certificate's single common name without accepting an ambiguous subject."""
    from cryptography import x509
    from cryptography.x509.oid import NameOID

    try:
        names = x509.load_pem_x509_certificate(certificate).subject.get_attributes_for_oid(
            NameOID.COMMON_NAME
        )
    except ValueError:
        raise RuntimeError("the stored remote certificate is invalid") from None
    if len(names) != 1 or not isinstance(names[0].value, str):
        raise RuntimeError("the stored remote certificate has no unique client name")
    return names[0].value


class TvRemoteChannel:
    """A synchronous facade over the Android TV Remote protocol, one TV address per call.

    Calls are serialized on one private event loop thread. Between calls the facade keeps at
    most one open connection per TV, for `keep_open` seconds, and a pairing that was started
    and not yet finished.
    """

    def __init__(
        self,
        credentials_dir: Path,
        *,
        client_name: str = CLIENT_NAME,
        timeout: float = DEFAULT_TIMEOUT,
        settle: float = DEFAULT_SETTLE,
        power_settle: float = DEFAULT_POWER_SETTLE,
        keep_open: float = KEEP_OPEN,
        remote_factory: RemoteFactory = _default_remote_factory,
        cert_generator: CertGenerator = _default_cert_generator,
        certificate_name_reader: CertificateNameReader = _default_certificate_name,
        identity_generator: IdentityGenerator = _new_identity_suffix,
    ) -> None:
        self._dir = Path(credentials_dir)
        self._client_name = client_name
        self._timeout = timeout
        self._settle = settle
        self._power_settle = power_settle
        self._keep_open = keep_open
        self._remote_factory = remote_factory
        self._cert_generator = cert_generator
        self._certificate_name_reader = certificate_name_reader
        self._identity_generator = identity_generator
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pairing: dict[str, RemoteClient] = {}
        self._connections: dict[str, RemoteClient] = {}
        self._expiry: dict[str, asyncio.TimerHandle] = {}
        self._voice: dict[str, VoiceSession] = {}

    # Public operations ---------------------------------------------------------------------

    def start_pairing(self, host: str) -> None:
        """Ask the TV to show a pairing code. Nothing else is sent to it."""
        host = _checked_host(host)

        async def start() -> None:
            self._discard_pairing(host)
            self._drop_connection(host)
            remote = self._new_remote(host)
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
                # A connection made before pairing may carry the old identity.
                self._drop_connection(host)

        self._run(finish)

    def observe(self, host: str) -> RemoteObservation:
        """Connect, read what the TV reports (power, audible volume), and close."""
        host = _checked_host(host)

        async def read(remote: RemoteClient, fresh: bool) -> RemoteObservation:
            if fresh:
                await self._wait_for(lambda: remote.volume_info is not None)
            return _observe(remote)

        return self._run(lambda: self._session(host, read))

    def press(self, host: str, key: RemoteKey) -> KeyPress:
        """Send one key once, and report what the TV said before and after it.

        For a volume key the TV normally reports its new volume, and for Power its new power
        state; the adapter waits for that report a short time, then returns whatever the TV
        said, changed or not.
        """
        host = _checked_host(host)
        if not isinstance(key, RemoteKey):
            raise InvalidArgumentError(f"not an allowed key: {key!r}")

        async def send(remote: RemoteClient, fresh: bool) -> KeyPress:
            # What the key changes is read first, on a new connection only (an open one has
            # already received what the TV reports); navigation keys are not delayed.
            if fresh and key in VOLUME_KEYS:
                await self._wait_for(lambda: remote.volume_info is not None)
            elif fresh and key is RemoteKey.POWER:
                await self._wait_for(lambda: _observe(remote).is_on is not None)
            before = _observe(remote)
            remote.send_key_command(key.value)
            # A TV that reported no volume is not waited for: it would not report a change.
            if key in VOLUME_KEYS and before.volume is not None:
                await self._wait_for(lambda: _observe(remote).volume != before.volume)
            elif key is RemoteKey.POWER:
                await self._wait_for(
                    lambda: _observe(remote).is_on != before.is_on, self._power_settle
                )
            return KeyPress(key=key, before=before, after=_observe(remote))

        return self._run(lambda: self._session(host, send))

    def type_text(self, host: str, text: str) -> TextEntry:
        """Send `text` once to the TV's active text field, replacing what the field holds.

        The adapter waits a short time for the TV to report an active field. It sends the text
        exactly once only when that field exists. The text never appears in an error or a result.
        """
        host = _checked_host(host)
        text = _checked_text(text)

        async def send(remote: RemoteClient, fresh: bool) -> TextEntry:
            del fresh
            await self._wait_for(lambda: _field_reported(remote))
            reported = _field_reported(remote)
            if reported:
                remote.send_text(text)
            return TextEntry(length=len(text), field_reported=reported, observed=_observe(remote))

        return self._run(lambda: self._session(host, send))

    def front_app(self, host: str, package: str) -> str | None:
        """The app the TV reports in front, waited for a short time to become `package`.

        Read only: nothing is sent to the TV. `None` when the TV does not say.
        """
        host = _checked_host(host)
        package = checked_package(package)

        async def read(remote: RemoteClient, fresh: bool) -> str | None:
            del fresh
            await self._wait_for(
                lambda: _observe(remote).current_app == package, self._power_settle
            )
            return _observe(remote).current_app

        return self._run(lambda: self._session(host, read))

    def open_receiver_link(self, host: str, link: str) -> None:
        """Open one of ControlTV Receiver's links on the TV, once (see `receiver_apps`).

        Only that link's exact shape is sent: a reply address on this network and a token.
        """
        host = _checked_host(host)
        if not isinstance(link, str) or RECEIVER_LINK.fullmatch(link) is None:
            raise InvalidArgumentError("not a ControlTV Receiver app-list link")

        async def send(remote: RemoteClient, fresh: bool) -> None:
            del fresh
            remote.send_launch_app_command(link)

        self._run(lambda: self._session(host, send))

    def start_voice(self, host: str) -> None:
        """Open one Assistant voice session; no audio is retained by this adapter."""
        host = _checked_host(host)

        async def start(remote: RemoteClient, fresh: bool) -> None:
            del fresh
            if host in self._voice:
                raise CommandRejectedError("a voice session is already active")
            if remote.is_voice_enabled is not True:
                raise UnsupportedOperationError("this TV does not accept remote microphone audio")
            self._voice[host] = await remote.start_voice()

        self._run(lambda: self._session(host, start))

    def send_voice(self, host: str, chunk: bytes) -> None:
        """Send one in-memory PCM chunk to the active session, once."""
        host = _checked_host(host)
        if not isinstance(chunk, bytes) or not 0 < len(chunk) <= MAX_VOICE_CHUNK:
            raise InvalidArgumentError(
                "voice audio must be a non-empty PCM chunk of at most 20 KiB"
            )

        async def send() -> None:
            remote = self._connections.get(host)
            session = self._voice.get(host)
            if remote is None or session is None or not _open(remote):
                self._drop_connection(host)
                raise DeviceUnavailableError("the TV voice session is not connected")
            if not session.send_chunk(chunk):
                self._drop_connection(host)
                raise DeviceUnavailableError("the TV voice session ended")
            self._expire_later(host)

        self._run(send)

    def stop_voice(self, host: str) -> None:
        """End an active voice session. Repeated cleanup is harmless."""
        host = _checked_host(host)

        async def stop() -> None:
            session = self._voice.pop(host, None)
            if session is None:
                return
            try:
                session.end()
            except BaseException:
                self._drop_connection(host)
                raise
            self._expire_later(host)

        self._run(stop)

    def close(self) -> None:
        """Drop a pending pairing and stop the event loop thread."""
        with self._lock:
            loop, self._loop = self._loop, None
        if loop is None:
            return

        async def drop() -> None:
            for host in list(self._pairing):
                self._discard_pairing(host)
            for host in list(self._connections):
                self._drop_connection(host)

        asyncio.run_coroutine_threadsafe(drop(), loop).result(self._timeout)
        loop.call_soon_threadsafe(loop.stop)

    # Internals ---------------------------------------------------------------------------

    async def _session(self, host: str, action: Callable[[RemoteClient, bool], Awaitable[T]]) -> T:
        """Run `action` on the TV's open connection, or on a new one (`fresh`)."""
        remote = self._connections.get(host)
        if remote is not None and not _open(remote):
            self._drop_connection(host)
            remote = None
        fresh = remote is None
        if remote is None:
            remote = self._new_remote(host)
            try:
                await remote.async_connect()
            except BaseException:
                remote.disconnect()
                raise
            self._connections[host] = remote
        try:
            result = await action(remote, fresh)
        except BaseException:
            # A failed or cancelled call never leaves its connection for the next one.
            self._drop_connection(host)
            raise
        if _open(remote):
            self._expire_later(host)
        else:
            self._drop_connection(host)
        return result

    def _expire_later(self, host: str) -> None:
        handle = self._expiry.pop(host, None)
        if handle is not None:
            handle.cancel()
        loop = asyncio.get_running_loop()
        self._expiry[host] = loop.call_later(self._keep_open, self._drop_connection, host)

    def _drop_connection(self, host: str) -> None:
        handle = self._expiry.pop(host, None)
        if handle is not None:
            handle.cancel()
        session = self._voice.pop(host, None)
        if session is not None:
            with suppress(Exception):
                session.end()
        remote = self._connections.pop(host, None)
        if remote is not None:
            # Closing the connection flushes what was written to it before closing.
            remote.disconnect()

    async def _wait_for(self, condition: Callable[[], bool], limit: float | None = None) -> None:
        bound = self._settle if limit is None else limit
        waited = 0.0
        while not condition() and waited < bound:
            await asyncio.sleep(_SETTLE_STEP)
            waited += _SETTLE_STEP

    def _discard_pairing(self, host: str) -> None:
        remote = self._pairing.pop(host, None)
        if remote is not None:
            remote.disconnect()

    def _new_remote(self, host: str) -> RemoteClient:
        identity, cert, key = self._credentials()
        return self._remote_factory(identity, cert, key, host)

    def _credentials(self) -> tuple[str, str, str]:
        """The persistent installation identity, certificate and private key.

        Older installations have a certificate named only ``control-TV``. Their stable identity
        is derived from that certificate, then that legacy pair is backed up and replaced once:
        the TV requires one explicit final pairing, after which both installations can coexist.
        """
        self._dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self._dir, 0o700)
        cert, key, identity_file, certificate_identity_file = (
            self._dir / CERT_FILE,
            self._dir / KEY_FILE,
            self._dir / IDENTITY_FILE,
            self._dir / CERTIFICATE_IDENTITY_FILE,
        )
        complete = cert.is_file() and key.is_file()
        identity = self._read_identity(identity_file)
        if identity is None:
            suffix = (
                sha256(cert.read_bytes()).hexdigest()[:8]
                if complete
                else self._identity_generator()
            )
            if re.fullmatch(r"[0-9a-f]{8}", suffix) is None:
                raise RuntimeError("the generated remote identity is invalid")
            identity = f"{self._client_name} {suffix}"
            _write_private(identity_file, identity.encode("utf-8"))
        certificate_identity = self._read_identity(certificate_identity_file)
        if complete and certificate_identity != identity:
            if self._certificate_name_reader(cert.read_bytes()) != identity:
                self._migrate_legacy_credentials(cert, key, identity)
            _write_private(certificate_identity_file, identity.encode("utf-8"))
        elif not complete:
            cert_pem, key_pem = self._cert_generator(identity)
            _write_private(key, key_pem)
            _write_private(cert, cert_pem)
            _write_private(certificate_identity_file, identity.encode("utf-8"))
        private_files = [identity_file, certificate_identity_file, cert, key]
        private_files.extend(
            path
            for path in (self._dir / LEGACY_CERT_FILE, self._dir / LEGACY_KEY_FILE)
            if path.is_file()
        )
        for path in private_files:
            if stat.S_IMODE(path.stat().st_mode) != 0o600:
                os.chmod(path, 0o600)
        return identity, str(cert), str(key)

    def _migrate_legacy_credentials(self, cert: Path, key: Path, identity: str) -> None:
        """Replace a legacy named pair once, retaining its exact bytes as private evidence."""
        legacy_cert = self._dir / LEGACY_CERT_FILE
        legacy_key = self._dir / LEGACY_KEY_FILE
        if not legacy_cert.exists():
            _write_private(legacy_cert, cert.read_bytes())
        if not legacy_key.exists():
            _write_private(legacy_key, key.read_bytes())
        cert_pem, key_pem = self._cert_generator(identity)
        # Key first is deliberate: an interruption leaves the old certificate name in place,
        # so the next launch retries the bounded migration instead of accepting a mismatched pair.
        _write_private(key, key_pem)
        _write_private(cert, cert_pem)

    def _read_identity(self, path: Path) -> str | None:
        if not path.is_file():
            return None
        try:
            identity = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return None
        pattern = rf"{re.escape(self._client_name)} [0-9a-f]{{8}}"
        return identity if re.fullmatch(pattern, identity) is not None else None

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
            if (self._dir / LEGACY_CERT_FILE).is_file():
                message = "pair this TV once after control-TV updated its installation identity"
            else:
                message = "this TV must be paired with control-TV first"
            raise PairingRequiredError(message) from None
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
