"""The TV's apps read by ControlTV Receiver, with no TV setting (issue #112).

A fake TV plays the receiver: when the remote opens its link, it posts to the reply address
it was given, as the real receiver does, over a real local socket."""

from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request

import pytest

from control_tv.adapters.receiver_apps import ReceiverAppList, parse_app_list
from control_tv.adapters.tv_remote import RECEIVER_LINK
from control_tv.domain import DeviceUnavailableError, OperationTimeoutError, TvApp

TOKEN = "t" * 32
HOST = "192.0.2.10"


def post(url: str, body: bytes) -> int:
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=2) as answer:
            return int(answer.status)
    except urllib.error.HTTPError as error:
        return error.code


class FakeReceiverTv:
    def __init__(self, answer: dict[str, object] | None, *, wrong_first: bool = False) -> None:
        self.answer = answer
        self.wrong_first = wrong_first
        self.links: list[str] = []
        self.statuses: list[int] = []

    def open_receiver_link(self, host: str, link: str) -> None:
        assert host == HOST
        assert RECEIVER_LINK.fullmatch(link)
        self.links.append(link)
        if self.answer is None:
            return  # no receiver installed: nothing answers
        answer = self.answer
        reply = re.search(r"reply=([^&]+)", link)
        token = re.search(r"token=([^&]+)", link)
        assert reply and token

        def send() -> None:
            if self.wrong_first:
                self.statuses.append(
                    post(reply.group(1), json.dumps({"token": "x" * 32, "apps": []}).encode())
                )
            body = json.dumps({**answer, "token": token.group(1)}).encode()
            self.statuses.append(post(reply.group(1), body))

        threading.Thread(target=send).start()


def lister(tv: FakeReceiverTv, timeout: float = 3.0) -> ReceiverAppList:
    return ReceiverAppList(tv, timeout=timeout, address_towards=lambda _: "127.0.0.1")


def test_the_receiver_sends_the_tvs_apps_with_its_own_labels() -> None:
    tv = FakeReceiverTv(
        {
            "apps": [
                {"package": "com.netflix.ninja", "label": "Netflix"},
                {"package": "fr.free.oqee", "label": "OQEE by Free"},
                {"package": "not a package", "label": "x"},
                {"package": "com.netflix.ninja", "label": "Netflix"},
            ]
        },
        wrong_first=True,
    )

    apps = lister(tv).list_apps(HOST)

    assert apps == (TvApp("com.netflix.ninja", "Netflix"), TvApp("fr.free.oqee", "OQEE by Free"))
    assert len(tv.links) == 1  # the link is opened once
    assert tv.statuses[0] == 400  # a list with another token is refused


def test_without_the_receiver_the_wait_ends_and_the_listener_is_closed() -> None:
    tv = FakeReceiverTv(None)

    with pytest.raises(OperationTimeoutError, match="install it on the TV"):
        lister(tv, timeout=0.2).list_apps(HOST)
    port = int(re.search(r":(\d+)/apps", tv.links[0]).group(1))  # type: ignore[union-attr]
    with pytest.raises(OSError):
        urllib.request.urlopen(f"http://127.0.0.1:{port}/apps", data=b"{}", timeout=1)


def test_no_route_to_the_tv_is_reported_without_opening_anything() -> None:
    tv = FakeReceiverTv({"apps": []})

    def unreachable(_: str) -> str:
        raise OSError("network is unreachable")

    with pytest.raises(DeviceUnavailableError):
        ReceiverAppList(tv, address_towards=unreachable).list_apps(HOST)
    assert tv.links == []


@pytest.mark.parametrize(
    "body",
    [b"not json", b"[]", json.dumps({"apps": []}).encode(), json.dumps({"token": TOKEN}).encode()],
)
def test_anything_but_a_list_for_this_token_is_not_a_list(body: bytes) -> None:
    assert parse_app_list(body, TOKEN) is None


def test_labels_are_trimmed_and_bounded() -> None:
    body = json.dumps(
        {
            "token": TOKEN,
            "apps": [{"package": "a.b", "label": "  " + "x" * 200}, {"package": "c.d"}],
        }
    ).encode()

    apps = parse_app_list(body, TOKEN)

    assert apps == (TvApp("c.d", None), TvApp("a.b", "x" * 80))  # sorted by what is shown
