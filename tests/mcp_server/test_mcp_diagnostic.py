"""The read-only diagnostic MCP server (`control_tv.mcp_diagnostic`) for gate G1.

It must stay unable to reach a TV: one read-only tool, no Cast, bridge or service import,
nothing but a call number, the server time and the caller's own note in its answer.
"""

from __future__ import annotations

import logging
import os
import stat
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import anyio
from mcp import ClientSession, types
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.shared.memory import create_connected_server_and_client_session

import control_tv
from control_tv import mcp_diagnostic
from control_tv.mcp_diagnostic import NOTE_MAX_LENGTH, TOOL, TOOL_NAME, DiagnosticTool, build_server

ROOT = Path(__file__).resolve().parents[2]
NOON = datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC)


def structured(result: types.CallToolResult) -> dict[str, Any]:
    assert result.structuredContent is not None
    return result.structuredContent


def test_there_is_one_read_only_tool_with_a_closed_schema() -> None:
    assert TOOL.name == TOOL_NAME
    assert TOOL.inputSchema["additionalProperties"] is False
    assert TOOL.inputSchema["required"] == []
    assert set(TOOL.inputSchema["properties"]) == {"note"}
    annotations = TOOL.annotations
    assert annotations is not None
    assert annotations.readOnlyHint is True
    assert annotations.destructiveHint is False
    assert annotations.idempotentHint is True
    assert annotations.openWorldHint is False


def test_a_call_answers_its_number_the_server_time_and_the_note_without_any_tv() -> None:
    tool = DiagnosticTool(now=lambda: NOON)

    first = tool.call(TOOL_NAME, {"note": "lance le test"})
    second = tool.call(TOOL_NAME, None)

    assert first.isError is False
    assert structured(first) == {
        "server": "control-tv-diagnostic",
        "version": control_tv.__version__,
        "call": 1,
        "serverTimeUtc": "2026-10-08T12:00:00+00:00",
        "note": "lance le test",
        "tvContacted": False,
    }
    assert structured(second)["call"] == 2
    assert structured(second)["note"] is None
    assert "No TV was contacted" in first.content[0].text  # type: ignore[union-attr]


def test_the_server_time_is_always_reported_in_utc() -> None:
    paris = timezone(timedelta(hours=2))
    tool = DiagnosticTool(now=lambda: datetime(2026, 10, 8, 14, 0, 0, tzinfo=paris))

    assert structured(tool.call(TOOL_NAME, {}))["serverTimeUtc"] == "2026-10-08T12:00:00+00:00"


def test_invalid_calls_are_refused_and_not_counted() -> None:
    tool = DiagnosticTool(now=lambda: NOON)
    refused = [
        tool.call("get_status", {}),
        tool.call(TOOL_NAME, {"deviceId": "tv-1"}),
        tool.call(TOOL_NAME, {"note": 42}),
        tool.call(TOOL_NAME, {"note": "x" * (NOTE_MAX_LENGTH + 1)}),
    ]

    for result in refused:
        assert result.isError is True
        assert structured(result)["error"]["code"] == "invalid_argument"
    assert structured(tool.call(TOOL_NAME, {"note": "x" * NOTE_MAX_LENGTH}))["call"] == 1


def test_the_log_names_the_call_but_never_the_note(caplog: Any) -> None:
    caplog.set_level("INFO", logger="control_tv.mcp_diagnostic")

    DiagnosticTool(now=lambda: NOON).call(TOOL_NAME, {"note": "my private words"})

    assert caplog.messages == [
        "diagnostic call #1 at 2026-10-08T12:00:00+00:00 (note: 16 characters)"
    ]


def test_an_mcp_client_lists_and_calls_the_tool() -> None:
    async def scenario() -> tuple[list[str], types.CallToolResult]:
        server = build_server(DiagnosticTool(now=lambda: NOON))
        async with create_connected_server_and_client_session(server) as client:
            listed = await client.list_tools()
            called = await client.call_tool(TOOL_NAME, {"note": "test"})
            return [tool.name for tool in listed.tools], called

    names, called = anyio.run(scenario)

    assert names == [TOOL_NAME]
    assert structured(called)["call"] == 1
    assert structured(called)["tvContacted"] is False


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
                called = await client.call_tool(TOOL_NAME, {"note": "secret words"})
                return init, [tool.name for tool in listed.tools], called

    init, names, called = anyio.run(scenario)

    assert init.serverInfo.name == "control-tv-diagnostic"
    assert init.serverInfo.version == control_tv.__version__
    assert names == [TOOL_NAME]
    assert structured(called)["call"] == 1
    logged = log_file.read_text(encoding="utf-8")
    assert "diagnostic call #1 at " in logged
    assert "secret words" not in logged
    assert stat.S_IMODE(log_file.stat().st_mode) == 0o600


def test_the_diagnostic_server_never_loads_the_cast_layer_or_the_control_service() -> None:
    script = (
        "import sys\n"
        "from control_tv.mcp_diagnostic import DiagnosticTool, TOOL_NAME\n"
        "DiagnosticTool().call(TOOL_NAME, {'note': 'x'})\n"
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


def test_main_parses_its_options_logs_to_stderr_and_the_private_file(
    tmp_path: Path, monkeypatch: Any
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
    file_handler.emit(logging.makeLogRecord({"msg": "diagnostic call #1"}))
    file_handler.close()
    assert log_file.read_text(encoding="utf-8") == "diagnostic call #1\n"
    assert stat.S_IMODE(log_file.stat().st_mode) == 0o600


def test_the_stdio_entry_point_serves_the_diagnostic_server(monkeypatch: Any) -> None:
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
