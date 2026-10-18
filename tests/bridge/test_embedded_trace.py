"""The Android debug acceptance mode, Python side: explicit, before the first request, private."""

from __future__ import annotations

import importlib.util
import json
import stat
from pathlib import Path
from types import ModuleType

import pytest

from control_tv import bridge, embedded
from control_tv.command_trace import TraceConfigurationError, TraceRole


@pytest.fixture(autouse=True)
def fresh_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test is a new app process: no service built, no acceptance mode yet."""
    monkeypatch.setattr(embedded, "_control", None)
    monkeypatch.setattr(embedded, "_tracing", None)


def test_the_acceptance_trace_is_off_by_default() -> None:
    assert json.loads(embedded.acceptance_trace_status()) == {
        "active": False,
        "role": None,
        "hold": False,
        "file": "",
    }


def test_the_debug_build_switches_it_on_in_the_app_private_files(tmp_path: Path) -> None:
    directory = tmp_path / "files" / "acceptance"

    status = json.loads(embedded.configure_acceptance_trace(str(directory)))

    assert status == {
        "active": True,
        "role": "android",
        "hold": True,
        "file": str(directory / "command-trace.jsonl"),
    }
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    service = embedded._service()
    assert service._trace.role is TraceRole.ANDROID and service._gate is not None


def test_it_cannot_be_switched_on_after_the_first_request(tmp_path: Path) -> None:
    embedded.handle(json.dumps({"id": 1, "method": "ping", "params": {}}))

    with pytest.raises(RuntimeError, match="before the first request"):
        embedded.configure_acceptance_trace(str(tmp_path / "acceptance"))


def test_the_desktop_bridge_reads_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CONTROL_TV_COMMAND_TRACE", str(tmp_path / "evidence" / "t.jsonl"))
    monkeypatch.setenv("CONTROL_TV_TRACE_CLIENT", "ubuntu")
    monkeypatch.delenv("CONTROL_TV_CONFIRMATION_GATE", raising=False)

    service = bridge.default_service()

    assert service._trace.active and service._trace.role is TraceRole.UBUNTU
    assert service._gate is None


def test_the_desktop_bridge_refuses_a_trace_without_a_role(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CONTROL_TV_COMMAND_TRACE", str(tmp_path / "t.jsonl"))
    monkeypatch.delenv("CONTROL_TV_TRACE_CLIENT", raising=False)

    with pytest.raises(TraceConfigurationError, match="must be one of"):
        bridge.default_service()


def test_without_configuration_nothing_is_traced(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in ("CONTROL_TV_COMMAND_TRACE", "CONTROL_TV_CONFIRMATION_GATE"):
        monkeypatch.delenv(variable, raising=False)

    service = bridge.default_service()

    assert service._trace.active is False and service._gate is None


def load_evidence() -> ModuleType:
    path = Path(__file__).parents[2] / "scripts" / "acceptance_trace_evidence.py"
    spec = importlib.util.spec_from_file_location("acceptance_trace_evidence", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def widget_line(message_id: int, tap: str) -> str:
    """The request line `WidgetActionRunner` builds for a command: its tap as `requestId`."""
    return json.dumps(
        {
            "id": message_id,
            "method": "set_muted",
            "params": {"muted": True, "deviceId": "never-discovered"},
            "requestId": tap,
        }
    )


def test_a_widget_order_issued_twice_is_one_request_that_c7_rejects(tmp_path: Path) -> None:
    """The Android path: the embedded bridge, the real ControlService and the Android trace.
    The same tap issued twice keeps its identity, so C7 sees two sends of one request."""
    directory = tmp_path / "files" / "acceptance"
    embedded.configure_acceptance_trace(str(directory))
    tap = "0f8fad5b-d9cb-469f-a165-70867728950e"

    for message_id in (1, 2):
        answer = json.loads(embedded.handle(widget_line(message_id, tap)))
        assert answer["ok"] is False and answer["error"]["code"] == "device_not_found"
    embedded.handle(widget_line(3, "another-tap"))

    evidence = load_evidence()
    events = evidence.load_trace(directory / "command-trace.jsonl")
    sends = [event for event in events if event.event == "send_attempt"]
    assert [event.application_request_id for event in sends] == [tap, tap, "another-tap"]
    assert {event.client for event in sends} == {"android"}
    verdict, reason = evidence.classify_c7(events, sends[0].request_id)
    assert verdict.value == "FAIL" and "was sent 2 times (2 invocations)" in reason
    _, reason = evidence.classify_c7(events, sends[2].request_id)
    assert "sent 2 times" not in reason
