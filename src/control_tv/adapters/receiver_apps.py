"""The TV's installed apps, read on the TV by ControlTV Receiver: no TV setting needed.

The TV's debugging (which lists apps over ADB) turns itself off. ControlTV Receiver, installed
once on the TV, can read the apps itself. The paired remote opens its app-list link once,
carrying where to answer: a one-request listener on this device, on the address that reaches
the TV, with a random token. The receiver posts the TV's launcher apps (package and the TV's own
label) there, then closes. Nothing else is accepted: another path, a wrong token, a body too
large or not the expected JSON is refused, and the listener stops after one list or its time.
"""

from __future__ import annotations

import hmac
import json
import secrets
import socket
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Protocol

from control_tv.adapters.tv_apps import checked_package
from control_tv.domain import ControlError, DeviceUnavailableError, OperationTimeoutError, TvApp

DEFAULT_TIMEOUT = 8.0
MAX_BODY = 256 * 1024
MAX_APPS = 500
MAX_LABEL = 80


class ReceiverLink(Protocol):
    def open_receiver_link(self, host: str, link: str) -> None: ...


def _address_towards(host: str) -> str:
    """This device's address on the network that reaches `host` (nothing is sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect((host, 9))
        address: str = probe.getsockname()[0]
    return address


def parse_app_list(body: bytes, token: str) -> tuple[TvApp, ...] | None:
    """The apps in a receiver's answer, or `None` when it is not one for this `token`."""
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    sent = data.get("token")
    entries = data.get("apps")
    if not isinstance(sent, str) or not hmac.compare_digest(sent, token):
        return None
    if not isinstance(entries, list):
        return None
    apps: dict[str, TvApp] = {}
    for entry in entries[:MAX_APPS]:
        if not isinstance(entry, dict):
            continue
        try:
            package = checked_package(entry.get("package"))
        except ControlError:
            continue
        label = entry.get("label")
        name = label.strip()[:MAX_LABEL] if isinstance(label, str) and label.strip() else None
        apps[package] = TvApp(package=package, name=name)
    return tuple(sorted(apps.values(), key=lambda app: (app.name or app.package).lower()))


class ReceiverAppList:
    def __init__(
        self,
        link: ReceiverLink,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        address_towards: Callable[[str], str] = _address_towards,
        new_token: Callable[[], str] = lambda: secrets.token_urlsafe(24),
    ) -> None:
        self._link = link
        self._timeout = timeout
        self._address_towards = address_towards
        self._new_token = new_token

    def list_apps(self, host: str) -> tuple[TvApp, ...]:
        try:
            address = self._address_towards(host)
        except OSError:
            raise DeviceUnavailableError("this device has no network route to the TV") from None
        token = self._new_token()
        received: list[tuple[TvApp, ...]] = []
        done = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                apps = None
                if self.path == "/apps" and 0 < length <= MAX_BODY:
                    apps = parse_app_list(self.rfile.read(length), token)
                self.send_response(204 if apps is not None else 400)
                self.end_headers()
                if apps is not None and not done.is_set():
                    received.append(apps)
                    done.set()

            def log_message(self, format: str, *args: object) -> None:
                del format, args  # never log the TV's address or the request

        try:
            server = ThreadingHTTPServer((address, 0), Handler)
        except OSError:
            raise DeviceUnavailableError("could not listen for the TV's app list") from None
        thread = threading.Thread(target=server.serve_forever, name="control-tv-app-list")
        thread.daemon = True
        thread.start()
        try:
            port = server.server_address[1]
            link = f"controltv-receiver://apps?reply=http://{address}:{port}/apps&token={token}"
            self._link.open_receiver_link(host, link)
            if not done.wait(self._timeout):
                raise OperationTimeoutError(
                    "ControlTV Receiver did not send the TV's app list: install it on the TV"
                )
        finally:
            server.shutdown()
            server.server_close()
        return received[0]
