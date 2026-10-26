"""The TV's apps through ControlTV Receiver, on the TV itself: no TV setting needed.

The TV's debugging (which lists and opens apps over ADB) turns itself off, and some Google TVs
refuse the remote's `market://launch` links (recette 2026-10-26: `ActivityNotFoundException`,
`INVALID_ARGUMENT`). ControlTV Receiver, installed once on the TV, does both itself. The paired
remote opens one of its links once, carrying where to answer: a one-request listener on this
device, on the address that reaches the TV, with a random token.

- `list_apps`: the receiver posts the TV's launcher apps (package and the TV's own label).
- `launch_app`: the receiver starts that app's own launch activity, then answers what happened:
  `started`, `not_installed` or `refused`. Only `started` is reported as started; the app in
  front is then read from the TV's remote. No answer is never taken for a success.

Nothing else is accepted: another path, a wrong token, a body too large or not the expected
JSON is refused, and the listener stops after one answer or its time.
"""

from __future__ import annotations

import hmac
import json
import secrets
import socket
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Protocol, TypeVar

from control_tv.adapters.tv_apps import checked_package
from control_tv.domain import (
    AppLaunch,
    CommandRejectedError,
    ControlError,
    DeviceUnavailableError,
    OperationTimeoutError,
    TvApp,
)

DEFAULT_TIMEOUT = 8.0
MAX_BODY = 256 * 1024
MAX_APPS = 500
MAX_LABEL = 80

T = TypeVar("T")


class ReceiverLink(Protocol):
    def open_receiver_link(self, host: str, link: str) -> None: ...

    def front_app(self, host: str, package: str) -> str | None: ...


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


def parse_launch_answer(body: bytes, token: str, package: str) -> str | None:
    """The receiver's launch result for this `token` and `package`, or `None`."""
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    sent, result = data.get("token"), data.get("result")
    if not isinstance(sent, str) or not hmac.compare_digest(sent, token):
        return None
    if data.get("package") != package or result not in LAUNCH_RESULTS:
        return None
    return str(result)


LAUNCH_RESULTS = ("started", "not_installed", "refused")
NO_ANSWER = (
    "ControlTV Receiver did not answer: install or update it on the TV. "
    "Check the TV before trying again"
)


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
        def link(reply: str, token: str) -> str:
            return f"controltv-receiver://apps?reply={reply}&token={token}"

        return self._ask(host, "/apps", link, parse_app_list)

    def launch_app(self, host: str, package: str) -> AppLaunch:
        """Open `package` once through the receiver; `started` only on its word."""
        package = checked_package(package)

        def link(reply: str, token: str) -> str:
            return f"controltv-receiver://launch?package={package}&reply={reply}&token={token}"

        def parse(body: bytes, token: str) -> str | None:
            return parse_launch_answer(body, token, package)

        result = self._ask(host, "/launch", link, parse)
        if result == "not_installed":
            raise CommandRejectedError("this app is not installed on the TV; nothing was opened")
        if result == "refused":
            raise CommandRejectedError("the TV refused to open this app")
        try:
            front = self._link.front_app(host, package)
        except ControlError:
            front = None  # started, as the TV said; only the app in front is unknown
        return AppLaunch(package=package, front_app=front, started=True)

    def _ask(
        self,
        host: str,
        path: str,
        make_link: Callable[[str, str], str],
        parse: Callable[[bytes, str], T | None],
    ) -> T:
        """Open one receiver link and wait for its single answer on a one-request listener."""
        try:
            address = self._address_towards(host)
        except OSError:
            raise DeviceUnavailableError("this device has no network route to the TV") from None
        token = self._new_token()
        received: list[T] = []
        done = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                answer = None
                if self.path == path and 0 < length <= MAX_BODY:
                    answer = parse(self.rfile.read(length), token)
                self.send_response(204 if answer is not None else 400)
                self.end_headers()
                if answer is not None and not done.is_set():
                    received.append(answer)
                    done.set()

            def log_message(self, format: str, *args: object) -> None:
                del format, args  # never log the TV's address or the request

        try:
            server = ThreadingHTTPServer((address, 0), Handler)
        except OSError:
            raise DeviceUnavailableError("could not listen for the TV's answer") from None
        thread = threading.Thread(target=server.serve_forever, name="control-tv-receiver")
        thread.daemon = True
        thread.start()
        try:
            port = server.server_address[1]
            self._link.open_receiver_link(host, make_link(f"http://{address}:{port}{path}", token))
            if not done.wait(self._timeout):
                raise OperationTimeoutError(NO_ANSWER)
        finally:
            server.shutdown()
            server.server_close()
        return received[0]
