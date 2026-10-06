"""The in-process bridge entry point the Android app calls (`control_tv.embedded`)."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

import control_tv
from control_tv import embedded


@pytest.fixture(autouse=True)
def fresh_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embedded, "_control", None)


def test_ping_answers_with_this_package_version() -> None:
    response = json.loads(embedded.handle('{"id": 1, "method": "ping", "params": {}}'))

    assert response == {
        "id": 1,
        "ok": True,
        "result": {"status": "ready", "controlTvVersion": control_tv.__version__},
    }


def test_a_malformed_line_is_an_error_response_never_an_exception() -> None:
    response = json.loads(embedded.handle("not json"))

    assert response["ok"] is False
    assert response["id"] is None
    assert response["error"]["code"] == "invalid_argument"


def test_an_undiscovered_device_is_refused_by_the_shared_service_without_network() -> None:
    line = json.dumps({"id": 7, "method": "get_status", "params": {"deviceId": "never-seen"}})

    response = json.loads(embedded.handle(line))

    assert response["id"] == 7
    assert response["error"]["code"] == "device_not_found"


def test_one_service_serves_every_request() -> None:
    embedded.handle('{"id": 1, "method": "ping", "params": {}}')
    first = embedded._control
    embedded.handle('{"id": 2, "method": "ping", "params": {}}')

    assert first is not None
    assert embedded._control is first


def test_the_embedded_runtime_uses_protobufs_pure_python_implementation() -> None:
    """Android has no protobuf native extension: the Cast transport must import with the
    pure-Python implementation, chosen by `embedded` before protobuf is first imported."""
    script = (
        "import json\n"
        "from control_tv import embedded\n"
        "embedded.handle(json.dumps({'id': 1, 'method': 'get_status',"
        " 'params': {'deviceId': 'x'}}))\n"
        "from google.protobuf.internal import api_implementation\n"
        "print(api_implementation.Type())\n"
    )
    environment = {
        k: v for k, v in os.environ.items() if k != "PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"
    }

    completed = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, env=environment, check=True
    )

    assert completed.stdout.strip() == "python"


def test_the_startup_diagnostic_proves_the_import_and_the_ping() -> None:
    line = embedded.startup_diagnostic()

    assert f"control_tv {control_tv.__version__} imported" in line
    assert line.endswith(f"embedded ping ok, controlTvVersion={control_tv.__version__}")
    assert line.startswith("python 3.")


def test_the_startup_diagnostic_never_touches_the_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from control_tv.adapters import PyChromecastTransport

    def no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("the diagnostic must not discover or contact a device")

    monkeypatch.setattr(PyChromecastTransport, "discover", no_network)

    assert "embedded ping ok" in embedded.startup_diagnostic()


def test_a_failed_startup_ping_is_reported_with_its_code_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing(line: str) -> str:
        return json.dumps(
            {"id": 0, "ok": False, "error": {"code": "internal_error", "message": "detail"}}
        )

    monkeypatch.setattr(embedded, "handle", failing)

    line = embedded.startup_diagnostic()

    assert line.endswith("embedded ping failed (internal_error)")
    assert "detail" not in line
