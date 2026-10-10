"""The read-only diagnostic MCP server (`control_tv.mcp_diagnostic`) for gate G1.

It must stay unable to reach a TV and must return nothing private: one read-only tool with
no argument, no Cast, bridge or service import, and only fixed diagnostic fields (readiness,
version, call number, UTC time, scope) in its answer and its log.
"""

from __future__ import annotations

import json
import logging
import os
import stat
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import ClientSession, types
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.shared.memory import create_connected_server_and_client_session

import control_tv
from control_tv import mcp_diagnostic
from control_tv.mcp_diagnostic import SCOPE, TOOL, TOOL_NAME, DiagnosticTool, build_server

ROOT = Path(__file__).resolve().parents[2]
NOON = datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC)
FIELDS = {"server", "version", "ready", "call", "serverTimeUtc", "scope", "tvContacted"}
CONTROL_TOOLS = {
    "discover_devices",
    "get_status",
    "play",
    "pause",
    "stop",
    "seek",
    "set_volume",
    "set_muted",
}


def structured(result: types.CallToolResult) -> dict[str, Any]:
    assert result.structuredContent is not None
    return result.structuredContent


def test_there_is_one_read_only_tool_without_any_argument() -> None:
    assert TOOL.name == TOOL_NAME
    assert TOOL.inputSchema == {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }
    annotations = TOOL.annotations
    assert annotations is not None
    assert annotations.readOnlyHint is True
    assert annotations.destructiveHint is False
    assert annotations.idempotentHint is True
    assert annotations.openWorldHint is False


def test_a_call_answers_only_fixed_diagnostic_fields() -> None:
    tool = DiagnosticTool(now=lambda: NOON)

    first = tool.call(TOOL_NAME, {})
    second = tool.call(TOOL_NAME, None)

    assert first.isError is False
    assert structured(first) == {
        "server": "control-tv-diagnostic",
        "version": control_tv.__version__,
        "ready": True,
        "call": 1,
        "serverTimeUtc": "2026-10-08T12:00:00+00:00",
        "scope": SCOPE,
        "tvContacted": False,
    }
    assert set(structured(second)) == FIELDS
    assert structured(second)["call"] == 2
    assert "No TV was contacted" in first.content[0].text  # type: ignore[union-attr]


def test_the_server_time_is_always_reported_in_utc() -> None:
    paris = timezone(timedelta(hours=2))
    tool = DiagnosticTool(now=lambda: datetime(2026, 10, 8, 14, 0, 0, tzinfo=paris))

    assert structured(tool.call(TOOL_NAME, {}))["serverTimeUtc"] == "2026-10-08T12:00:00+00:00"


def test_other_tools_and_any_argument_are_refused_without_echo_or_count(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO", logger="control_tv.mcp_diagnostic")
    tool = DiagnosticTool(now=lambda: NOON)
    refused = [
        tool.call("set_muted", {"deviceId": "tv-private-id", "muted": True}),
        tool.call("discover_devices", {}),
        tool.call(TOOL_NAME, {"note": "my private words"}),
    ]

    for result in refused:
        assert result.isError is True
        assert structured(result)["error"]["code"] == "invalid_argument"
        text = json.dumps(structured(result)) + result.content[0].text  # type: ignore[union-attr]
        assert "tv-private-id" not in text and "my private words" not in text
    assert structured(tool.call(TOOL_NAME, {}))["call"] == 1
    assert caplog.messages == ["control_tv_diagnostic refused: invalid_argument"] * 3 + [
        "control_tv_diagnostic call #1 at 2026-10-08T12:00:00+00:00: ok"
    ]


def test_an_mcp_client_sees_exactly_the_diagnostic_tool_and_no_control_tool() -> None:
    async def scenario() -> tuple[list[str], types.CallToolResult]:
        server = build_server(DiagnosticTool(now=lambda: NOON))
        async with create_connected_server_and_client_session(server) as client:
            listed = await client.list_tools()
            called = await client.call_tool(TOOL_NAME, {})
            return [tool.name for tool in listed.tools], called

    names, called = anyio.run(scenario)

    assert names == [TOOL_NAME]
    assert not CONTROL_TOOLS & set(names)
    assert structured(called)["call"] == 1
    assert structured(called)["tvContacted"] is False


def test_the_self_check_lists_and_calls_the_tool_in_memory() -> None:
    report = anyio.run(mcp_diagnostic.self_check)

    assert report["ok"] is True
    assert report["tools"] == [TOOL_NAME]
    assert report["readOnly"] == [True]
    assert set(report["result"]) == FIELDS


def test_the_self_check_command_prints_its_report_and_exits_zero() -> None:
    environment = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    completed = subprocess.run(
        [sys.executable, "-m", "control_tv.mcp_diagnostic", "--self-check"],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )

    assert completed.returncode == 0
    report = json.loads(completed.stdout)
    assert report["ok"] is True
    assert report["tools"] == [TOOL_NAME]
    assert "control_tv_diagnostic call #1 at " in completed.stderr


def test_the_real_stdio_server_answers_and_logs_to_a_private_file(tmp_path: Path) -> None:
    log_file = tmp_path / "diagnostic.log"
    environment = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "control_tv.mcp_diagnostic", "--log-file", str(log_file)],
        env=environment,
    )

    async def scenario() -> tuple[types.InitializeResult, list[str], types.CallToolResult]:
        with open(os.devnull, "w") as errlog:
            async with (
                stdio_client(parameters, errlog=errlog) as (read, write),
                ClientSession(read, write) as client,
            ):
                init = await client.initialize()
                listed = await client.list_tools()
                called = await client.call_tool(TOOL_NAME, {})
                return init, [tool.name for tool in listed.tools], called

    init, names, called = anyio.run(scenario)

    assert init.serverInfo.name == "control-tv-diagnostic"
    assert init.serverInfo.version == control_tv.__version__
    assert names == [TOOL_NAME]
    assert structured(called)["call"] == 1
    lines = log_file.read_text(encoding="utf-8").splitlines()
    ours = [line for line in lines if line.startswith("control_tv.mcp_diagnostic: ")]
    assert len(ours) == 1
    assert ours[0].startswith("control_tv.mcp_diagnostic: control_tv_diagnostic call #1 at ")
    assert ours[0].endswith(": ok")
    # The SDK's own lines name only the request type (evidence that a client reached the
    # server even when it calls no tool), never a payload.
    others = [line for line in lines if line not in ours]
    assert others == [
        "mcp.server.lowlevel.server: Processing request of type ListToolsRequest",
        "mcp.server.lowlevel.server: Processing request of type CallToolRequest",
    ]
    assert stat.S_IMODE(log_file.stat().st_mode) == 0o600


def test_the_diagnostic_server_never_loads_the_cast_layer_or_the_control_service() -> None:
    script = (
        "import sys, anyio\n"
        "from control_tv import mcp_diagnostic\n"
        "assert anyio.run(mcp_diagnostic.self_check)['ok']\n"
        "loaded = [m for m in sys.modules if m.split('.')[0] in ('pychromecast', 'zeroconf')\n"
        "          or m in ('control_tv.bridge', 'control_tv.service', 'control_tv.adapters',\n"
        "                   'control_tv.mcp_server', 'control_tv.embedded')]\n"
        "print(sorted(loaded))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )

    assert completed.stdout.strip() == "[]"


def test_the_diagnostic_module_imports_nothing_that_can_reach_a_tv() -> None:
    source = (ROOT / "src" / "control_tv" / "mcp_diagnostic.py").read_text(encoding="utf-8")
    imports = [line for line in source.splitlines() if line.startswith(("import ", "from "))]

    assert not [line for line in imports if "control_tv." in line or "socket" in line]
    assert mcp_diagnostic.SERVER_NAME == "control-tv-diagnostic"


def test_main_logs_to_stderr_and_the_private_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_file = tmp_path / "private.log"
    started: list[Any] = []
    monkeypatch.setattr("control_tv.mcp_diagnostic.anyio.run", started.append)
    monkeypatch.setattr(
        "control_tv.mcp_diagnostic.logging.basicConfig", lambda **kwargs: started.append(kwargs)
    )

    mcp_diagnostic.main(["--log-file", str(log_file)])

    config, served = started
    assert served is mcp_diagnostic.serve_stdio
    file_handler = config["handlers"][1]
    file_handler.emit(logging.makeLogRecord({"msg": "control_tv_diagnostic call #1"}))
    file_handler.close()
    assert log_file.read_text(encoding="utf-8") == "control_tv_diagnostic call #1\n"
    assert stat.S_IMODE(log_file.stat().st_mode) == 0o600


def test_a_failed_self_check_exits_non_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("control_tv.mcp_diagnostic.anyio.run", lambda _: {"ok": False})
    monkeypatch.setattr("control_tv.mcp_diagnostic.logging.basicConfig", lambda **_: None)

    with pytest.raises(SystemExit) as exit_info:
        mcp_diagnostic.main(["--self-check"])

    assert exit_info.value.code == 1


def test_the_stdio_entry_point_serves_the_diagnostic_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    served: list[str] = []

    class FakeServer:
        def create_initialization_options(self) -> str:
            return "options"

        async def run(self, read: Any, write: Any, options: str) -> None:
            served.append(options)

    class FakeStdio:
        async def __aenter__(self) -> tuple[str, str]:
            return "read", "write"

        async def __aexit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(mcp_diagnostic, "build_server", FakeServer)
    monkeypatch.setattr(mcp_diagnostic, "stdio_server", FakeStdio)

    anyio.run(mcp_diagnostic.serve_stdio)

    assert served == ["options"]
