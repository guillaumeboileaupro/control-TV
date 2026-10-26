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

from control_tv.adapters.receiver_apps import ReceiverAppList, parse_app_list, parse_launch_answer
from control_tv.adapters.tv_remote import RECEIVER_LINK
from control_tv.domain import (
    AppLaunch,
    CommandRejectedError,
    DeviceUnavailableError,
    InvalidArgumentError,
    OperationTimeoutError,
    TvApp,
)

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
        self.front: str | None = None
        self.front_reads = 0

    def front_app(self, host: str, package: str) -> str | None:
        assert host == HOST
        self.front_reads += 1
        return self.front

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

    with pytest.raises(OperationTimeoutError, match="install or update it on the TV"):
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


# Opening an app through the receiver (recette 2026-10-26: market://launch refused) ------------


def launch_tv(result: str, **extra: object) -> FakeReceiverTv:
    return FakeReceiverTv({"package": "tv.arte.plus7", "result": result, **extra})


def test_the_receiver_starts_the_app_and_the_app_in_front_is_then_read() -> None:
    tv = launch_tv("started")
    tv.front = "tv.arte.plus7"

    launched = lister(tv).launch_app(HOST, "tv.arte.plus7")

    assert launched == AppLaunch(package="tv.arte.plus7", front_app="tv.arte.plus7", started=True)
    assert len(tv.links) == 1 and tv.links[0].startswith(
        "controltv-receiver://launch?package=tv.arte.plus7&reply=http://127.0.0.1:"
    )
    assert "market://" not in tv.links[0]


@pytest.mark.parametrize(
    ("result", "message"),
    [("not_installed", "not installed"), ("refused", "refused")],
)
def test_an_app_the_tv_did_not_start_is_an_error_never_sent(result: str, message: str) -> None:
    tv = launch_tv(result)

    with pytest.raises(CommandRejectedError, match=message):
        lister(tv).launch_app(HOST, "tv.arte.plus7")
    assert tv.front_reads == 0


def test_no_answer_is_an_error_that_says_to_check_the_tv_not_a_success() -> None:
    tv = FakeReceiverTv(None)  # the TV refused the link (old receiver) or nothing answered

    with pytest.raises(OperationTimeoutError, match="Check the TV"):
        lister(tv, timeout=0.2).launch_app(HOST, "tv.arte.plus7")


def test_an_answer_for_another_app_or_request_is_ignored() -> None:
    other_app = launch_tv("started", package="com.netflix.ninja")

    with pytest.raises(OperationTimeoutError):
        lister(other_app, timeout=0.5).launch_app(HOST, "tv.arte.plus7")
    assert other_app.statuses == [400]
    assert (
        parse_launch_answer(b'{"token": "x", "package": "a.b", "result": "started"}', TOKEN, "a.b")
        is None
    )
    assert (
        parse_launch_answer(
            json.dumps({"token": TOKEN, "package": "a.b", "result": "maybe"}).encode(), TOKEN, "a.b"
        )
        is None
    )


def test_only_a_package_name_is_ever_put_in_a_launch_link() -> None:
    tv = launch_tv("started")

    with pytest.raises(InvalidArgumentError):
        lister(tv).launch_app(HOST, "market://launch?id=tv.arte.plus7")
    assert tv.links == []


def test_the_front_app_unknown_after_a_start_is_still_a_start() -> None:
    class Unreadable(FakeReceiverTv):
        def front_app(self, host: str, package: str) -> str | None:
            raise DeviceUnavailableError("the remote dropped")

    tv = Unreadable({"package": "tv.arte.plus7", "result": "started"})

    assert lister(tv).launch_app(HOST, "tv.arte.plus7").started is True
