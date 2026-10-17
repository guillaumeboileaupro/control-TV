"""The Android debug acceptance mode, Python side: explicit, before the first request, private."""

from __future__ import annotations

import json
import stat
from pathlib import Path

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
