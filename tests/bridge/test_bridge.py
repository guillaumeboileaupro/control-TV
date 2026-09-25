"""Tests for the stdio JSON bridge that the Tauri shell spawns and talks to.

`dispatch`/`handle_line` are exercised against a real `ControlService` over the
deterministic `FakeTransport`, so no real Chromecast, network or subprocess is involved
here - proving the mechanism reaches the shared control layer without duplicating it.
`test_main_serves_a_ping_over_real_stdio` is the one exception: it spawns the actual
`python -m control_tv.bridge` entrypoint to prove the process boundary itself works.
"""

from __future__ import annotations

import json
import subprocess
import sys
from io import StringIO

import pytest

import control_tv
from control_tv.bridge import dispatch, handle_line, run
from control_tv.service import ControlService
from fakes import DEVICE_ID, FakeTransport


@pytest.fixture
def control() -> ControlService:
    return ControlService(FakeTransport())


def test_ping_reports_readiness_and_version(control: ControlService) -> None:
    response = dispatch(control, {"id": 1, "method": "ping", "params": {}})

    assert response == {
        "id": 1,
        "ok": True,
        "result": {"status": "ready", "controlTvVersion": control_tv.__version__},
    }


def test_ping_does_not_require_params(control: ControlService) -> None:
    response = dispatch(control, {"id": "abc", "method": "ping"})

    assert response["ok"] is True
    assert response["id"] == "abc"


def test_discover_devices_returns_transport_devices_as_json(control: ControlService) -> None:
    response = dispatch(control, {"id": 2, "method": "discover_devices", "params": {}})

    assert response["ok"] is True
    assert response["result"]["devices"] == [
        {
            "id": str(DEVICE_ID),
            "friendlyName": "Living room",
            "host": "192.168.1.20",
            "port": 8009,
            "kind": "unknown",
            "modelName": None,
        }
    ]


def test_discover_devices_forwards_the_timeout(
    control: ControlService, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[float] = []
    original = ControlService.discover_devices

    def spy(self: ControlService, *, timeout: float = 5.0) -> list[object]:
        seen.append(timeout)
        return list(original(self, timeout=timeout))

    monkeypatch.setattr(ControlService, "discover_devices", spy)

    dispatch(control, {"id": 3, "method": "discover_devices", "params": {"timeoutSeconds": 2.5}})

    assert seen == [2.5]


def test_discover_devices_rejects_a_non_numeric_timeout(control: ControlService) -> None:
    response = dispatch(
        control, {"id": 4, "method": "discover_devices", "params": {"timeoutSeconds": "soon"}}
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_argument"


def test_a_control_error_from_the_service_is_translated_not_raised(
    control: ControlService,
) -> None:
    """A real domain error (invalid discovery timeout) becomes an error response, not a crash."""
    response = dispatch(
        control, {"id": 5, "method": "discover_devices", "params": {"timeoutSeconds": -1.0}}
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_argument"
    assert "timeout" in response["error"]["message"]


def test_unknown_method_is_rejected(control: ControlService) -> None:
    response = dispatch(control, {"id": 6, "method": "levitate", "params": {}})

    assert response == {
        "id": 6,
        "ok": False,
        "error": {"code": "invalid_argument", "message": "unknown method: 'levitate'"},
    }


def test_non_object_params_is_rejected(control: ControlService) -> None:
    response = dispatch(control, {"id": 7, "method": "ping", "params": ["not", "an", "object"]})

    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_argument"


def test_malformed_json_is_reported_with_a_null_id(control: ControlService) -> None:
    response = handle_line(control, "{not json")

    assert response["id"] is None
    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_argument"
    assert "malformed JSON request" in response["error"]["message"]


def test_a_json_array_is_not_a_valid_request(control: ControlService) -> None:
    response = handle_line(control, "[1, 2, 3]")

    assert response["id"] is None
    assert response["ok"] is False


def test_run_serves_one_response_per_request_line_and_skips_blanks(
    control: ControlService,
) -> None:
    stdin = StringIO('{"id": 1, "method": "ping"}\n\n{"id": 2, "method": "ping"}\n')
    stdout = StringIO()

    run(control, stdin=stdin, stdout=stdout)

    lines = stdout.getvalue().splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["id"] for line in lines] == [1, 2]
    assert all(json.loads(line)["ok"] is True for line in lines)


def test_main_serves_a_ping_over_real_stdio() -> None:
    """End-to-end proof that `python -m control_tv.bridge` is what Rust will actually spawn."""
    process = subprocess.Popen(
        [sys.executable, "-m", "control_tv.bridge"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    try:
        assert process.stdin is not None
        assert process.stdout is not None
        process.stdin.write('{"id": 1, "method": "ping"}\n')
        process.stdin.flush()
        response = json.loads(process.stdout.readline())
    finally:
        if process.stdin is not None:
            process.stdin.close()
        process.wait(timeout=5)

    assert response == {
        "id": 1,
        "ok": True,
        "result": {"status": "ready", "controlTvVersion": control_tv.__version__},
    }
