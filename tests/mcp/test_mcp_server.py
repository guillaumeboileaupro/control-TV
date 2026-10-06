"""The local MCP server (`control_tv.mcp_server`) over the shared control layer.

Everything runs against a real `ControlService` over the in-memory `FakeTransport` (no
Chromecast, no network), except the stdio test, which starts the real server process with
the real PyChromecast transport and only calls tools that never reach the network.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.shared.memory import create_connected_server_and_client_session

import control_tv
from control_tv import mcp_server
from control_tv.domain import (
    CommandRejectedError,
    DeviceUnavailableError,
    OperationTimeoutError,
)
from control_tv.mcp_server import COMMAND_TOOLS, READ_TOOLS, TOOLS, ControlTvTools, build_server
from control_tv.service import ControlService
from fakes import DEVICE_ID, MOVIE_URL, FakeClock, FakeTransport
from mcp import ClientSession, types

ROOT = Path(__file__).resolve().parents[2]
ID = str(DEVICE_ID)
SECRET_HOST = "192.168.1.20"


def service(transport: FakeTransport) -> ControlService:
    clock = FakeClock()
    transport.clock = clock
    return ControlService(transport, clock=clock.monotonic, sleep=clock.sleep)


def call(
    tools: ControlTvTools, tool: str, arguments: dict[str, Any] | None
) -> types.CallToolResult:
    return anyio.run(tools.call, tool, arguments)


def structured(result: types.CallToolResult) -> dict[str, Any]:
    assert result.structuredContent is not None
    return result.structuredContent


def text(result: types.CallToolResult) -> str:
    [block] = result.content
    assert isinstance(block, types.TextContent)
    return block.text


def everything(result: types.CallToolResult) -> str:
    return text(result) + json.dumps(result.structuredContent)


@pytest.fixture
def transport() -> FakeTransport:
    return FakeTransport()


@pytest.fixture
def tools(transport: FakeTransport) -> ControlTvTools:
    tools = ControlTvTools(service(transport))
    call(tools, "discover_devices", {})
    transport.calls.clear()
    return tools


# --- Tool registration and schemas ----------------------------------------------------------


def test_exactly_the_eight_planned_tools_are_registered() -> None:
    assert [tool.name for tool in TOOLS] == [
        "discover_devices",
        "get_status",
        "play",
        "pause",
        "stop",
        "seek",
        "set_volume",
        "set_muted",
    ]


def test_every_schema_is_closed_and_requires_the_stable_device_id() -> None:
    for tool in TOOLS:
        schema = tool.inputSchema
        assert schema["additionalProperties"] is False
        if tool.name != "discover_devices":
            assert "deviceId" in schema["required"]
    schemas = {tool.name: tool.inputSchema for tool in TOOLS}
    assert schemas["set_muted"]["properties"]["muted"] == {"type": "boolean"}
    assert schemas["set_volume"]["properties"]["level"] == {
        "type": "number",
        "minimum": 0,
        "maximum": 1,
    }
    assert schemas["seek"]["required"] == ["deviceId", "positionSeconds"]


def test_reads_are_read_only_and_commands_are_never_advertised_as_idempotent() -> None:
    for tool in TOOLS:
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is (tool.name in READ_TOOLS)
        assert tool.annotations.idempotentHint is (tool.name in READ_TOOLS)
        assert tool.annotations.destructiveHint is False
    assert set(READ_TOOLS) | set(COMMAND_TOOLS) == {tool.name for tool in TOOLS}


# --- Reads and privacy ------------------------------------------------------------------------


def test_discovery_returns_ids_and_names_but_never_addresses(transport: FakeTransport) -> None:
    tools = ControlTvTools(service(transport))

    result = call(tools, "discover_devices", {"timeoutSeconds": 2})

    assert result.isError is False
    assert structured(result) == {
        "devices": [{"id": ID, "friendlyName": "Living room", "kind": "unknown", "modelName": None}]
    }
    assert f"deviceId {ID}" in text(result)
    assert SECRET_HOST not in everything(result) and "8009" not in everything(result)


def test_status_never_exposes_the_media_content_id(tools: ControlTvTools) -> None:
    result = call(tools, "get_status", {"deviceId": ID})

    status = structured(result)["status"]
    assert result.isError is False
    assert status["media"]["playbackState"] == "playing"
    assert "contentId" not in status["media"]
    assert MOVIE_URL not in everything(result)


# --- Commands: sent versus confirmed, one send, no retry --------------------------------------


def test_a_confirmed_command_is_sent_once_and_reported_as_confirmed(
    tools: ControlTvTools, transport: FakeTransport
) -> None:
    result = call(tools, "pause", {"deviceId": ID})

    body = structured(result)
    assert result.isError is False
    assert (body["delivery"], body["confirmation"]) == ("sent", "confirmed")
    assert "contentId" not in body["observed"]["media"]
    assert transport.sent() == ["pause"]


def test_an_unconfirmed_command_is_not_a_success_and_is_never_retried(
    tools: ControlTvTools, transport: FakeTransport
) -> None:
    transport.ignore_commands = True

    result = call(tools, "pause", {"deviceId": ID})

    assert result.isError is False
    assert structured(result)["confirmation"] == "unconfirmed"
    assert "NOT confirmed" in text(result) and "Do not resend" in text(result)
    assert "get_status" in text(result)
    assert transport.attempted() == ["pause"]


def test_a_command_sent_without_verification_is_not_worded_as_confirmed(
    transport: FakeTransport,
) -> None:
    tools = ControlTvTools(ControlService(transport, confirm_timeout=None))
    call(tools, "discover_devices", {})

    result = call(tools, "play", {"deviceId": ID})

    assert structured(result)["confirmation"] == "not_checked"
    assert "not checked" in text(result) and "confirmed" not in text(result)
    assert transport.attempted() == ["play"]


def test_a_delivery_timeout_is_ambiguous_and_never_resent(
    tools: ControlTvTools, transport: FakeTransport
) -> None:
    transport.fail_commands_with = OperationTimeoutError(f"timed out sending to {SECRET_HOST}")

    result = call(tools, "stop", {"deviceId": ID})

    body = structured(result)
    assert result.isError is True
    assert body["error"]["code"] == "timeout"
    assert body["delivery"] == "unknown"
    assert "may or may not have reached the TV" in text(result)
    assert SECRET_HOST not in everything(result)
    assert transport.attempted() == ["stop"]


@pytest.mark.parametrize(
    ("error", "code", "delivery"),
    [
        (
            DeviceUnavailableError(f"device uuid-1 is unavailable: [Errno 113] {SECRET_HOST}"),
            "device_unavailable",
            "not_sent",
        ),
        (
            CommandRejectedError(f"device uuid-1 rejected pause: {SECRET_HOST}"),
            "command_rejected",
            "sent",
        ),
    ],
)
def test_command_errors_keep_their_code_and_delivery_without_leaking_details(
    tools: ControlTvTools,
    transport: FakeTransport,
    error: Exception,
    code: str,
    delivery: str,
) -> None:
    transport.fail_commands_with = error  # type: ignore[assignment]

    result = call(tools, "pause", {"deviceId": ID})

    body = structured(result)
    assert (body["error"]["code"], body["delivery"]) == (code, delivery)
    assert SECRET_HOST not in everything(result) and "Errno" not in everything(result)
    assert transport.attempted() == ["pause"]


def test_an_unsupported_seek_is_refused_before_anything_is_sent(
    tools: ControlTvTools, transport: FakeTransport
) -> None:
    transport.tv.supports_seek = False

    result = call(tools, "seek", {"deviceId": ID, "positionSeconds": 30})

    assert structured(result)["error"]["code"] == "unsupported_operation"
    assert structured(result)["delivery"] == "not_sent"
    assert transport.attempted() == []


def test_set_muted_sends_the_absolute_state_even_when_already_muted(
    tools: ControlTvTools, transport: FakeTransport
) -> None:
    transport.tv.muted = True

    result = call(tools, "set_muted", {"deviceId": ID, "muted": True})

    assert structured(result)["confirmation"] == "confirmed"
    assert ("set_muted", (DEVICE_ID, True)) in transport.calls
    assert transport.tv.muted is True


def test_volume_is_an_absolute_level_with_no_raise_limit_in_the_mcp_adapter(
    tools: ControlTvTools, transport: FakeTransport
) -> None:
    transport.tv.volume = 0.1

    result = call(tools, "set_volume", {"deviceId": ID, "level": 0.9})

    assert structured(result)["confirmation"] == "confirmed"
    assert transport.tv.volume == 0.9


def test_a_content_id_quoted_in_a_command_detail_is_hidden(
    tools: ControlTvTools, transport: FakeTransport
) -> None:
    secret = "https://private.example/movie?token=abc"
    transport.status_effects = [lambda: None, lambda: setattr(transport.tv, "content_id", secret)]

    result = call(tools, "pause", {"deviceId": ID})

    body = structured(result)
    assert body["confirmation"] == "unconfirmed"
    assert "<hidden>" in body["detail"]
    assert secret not in everything(result) and MOVIE_URL not in everything(result)


def test_an_unknown_device_is_not_found_without_discovery_or_any_command(
    tools: ControlTvTools, transport: FakeTransport
) -> None:
    for tool, arguments in [("get_status", {}), ("pause", {}), ("set_muted", {"muted": True})]:
        result = call(tools, tool, {"deviceId": "never-discovered", **arguments})

        assert structured(result)["error"]["code"] == "device_not_found"
        assert "discover_devices" in text(result)
    assert not any(name == "discover" for name, _ in transport.calls)
    assert transport.attempted() == []


# --- Input boundaries: the shared validation answers, nothing is sent -------------------------


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("set_volume", {"deviceId": ID, "level": True}),
        ("set_volume", {"deviceId": ID, "level": False}),
        ("set_volume", {"deviceId": ID, "level": "0.5"}),
        ("set_volume", {"deviceId": ID, "level": 1.5}),
        ("set_volume", {"deviceId": ID, "level": -0.1}),
        ("set_volume", {"deviceId": ID}),
        ("set_muted", {"deviceId": ID, "muted": 1}),
        ("set_muted", {"deviceId": ID, "muted": "true"}),
        ("set_muted", {"deviceId": ID}),
        ("seek", {"deviceId": ID, "positionSeconds": True}),
        ("seek", {"deviceId": ID, "positionSeconds": "30"}),
        ("seek", {"deviceId": ID, "positionSeconds": float("nan")}),
        ("seek", {"deviceId": ID, "positionSeconds": -1}),
        ("pause", {}),
        ("pause", {"deviceId": True}),
        ("pause", {"deviceId": ""}),
        ("pause", {"deviceId": ID, "force": True}),
        ("discover_devices", {"timeoutSeconds": True}),
        ("discover_devices", {"timeoutSeconds": "5"}),
        ("discover_devices", {"timeoutSeconds": 0}),
        ("reboot", {"deviceId": ID}),
    ],
)
def test_an_invalid_argument_is_refused_and_nothing_is_sent(
    tools: ControlTvTools, transport: FakeTransport, tool: str, arguments: dict[str, Any]
) -> None:
    result = call(tools, tool, arguments)

    assert result.isError is True
    assert structured(result)["error"]["code"] == "invalid_argument"
    assert transport.attempted() == []
    assert not any(name == "discover" for name, _ in transport.calls)


# --- Unexpected failures never leak their text ------------------------------------------------


def test_an_unexpected_service_failure_is_internal_and_its_text_stays_local(
    tools: ControlTvTools, transport: FakeTransport
) -> None:
    def explode(*args: object) -> None:
        raise RuntimeError(f"socket to {SECRET_HOST} failed in /home/someone/.venv/x.py")

    transport.pause = explode  # type: ignore[method-assign, assignment]

    result = call(tools, "pause", {"deviceId": ID})

    body = structured(result)
    assert (body["error"]["code"], body["delivery"]) == ("internal_error", "unknown")
    assert SECRET_HOST not in everything(result) and "/home/" not in everything(result)
    assert "RuntimeError" not in everything(result)


def test_an_adapter_failure_becomes_an_internal_error_result(transport: FakeTransport) -> None:
    def broken(control: ControlService, request: dict[str, Any]) -> dict[str, Any]:
        raise KeyError(f"secret {SECRET_HOST}")

    tools = ControlTvTools(service(transport), dispatcher=broken)

    result = call(tools, "get_status", {"deviceId": ID})

    assert result.isError is True
    assert structured(result)["error"]["code"] == "internal_error"
    assert SECRET_HOST not in everything(result)


# --- One call at a time; cancellation never replays -------------------------------------------


class BlockingDispatcher:
    """Records dispatches; `release` lets the first one finish."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.started = threading.Event()
        self.methods: list[str] = []
        self.running = 0
        self.max_running = 0
        self._guard = threading.Lock()

    def __call__(self, control: ControlService, request: dict[str, Any]) -> dict[str, Any]:
        with self._guard:
            self.methods.append(request["method"])
            self.running += 1
            self.max_running = max(self.max_running, self.running)
        self.started.set()
        self.release.wait(5)
        with self._guard:
            self.running -= 1
        return {"id": request["id"], "ok": True, "result": {"status": None}}


def test_tool_calls_run_one_at_a_time(transport: FakeTransport) -> None:
    dispatcher = BlockingDispatcher()
    tools = ControlTvTools(service(transport), dispatcher=dispatcher)

    async def scenario() -> None:
        async with anyio.create_task_group() as group:
            for _ in range(3):
                group.start_soon(tools.call, "get_status", {"deviceId": ID})
            await anyio.sleep(0.2)
            dispatcher.release.set()

    anyio.run(scenario)

    assert dispatcher.methods == ["get_status"] * 3
    assert dispatcher.max_running == 1


def test_a_call_cancelled_while_waiting_never_runs_and_a_running_one_runs_once(
    transport: FakeTransport,
) -> None:
    dispatcher = BlockingDispatcher()
    tools = ControlTvTools(service(transport), dispatcher=dispatcher)

    async def scenario() -> None:
        async with anyio.create_task_group() as running:
            running.start_soon(tools.call, "pause", {"deviceId": ID})
            await anyio.to_thread.run_sync(dispatcher.started.wait, 5)
            with anyio.move_on_after(0.2):  # cancelled while it waits for the first call
                await tools.call("stop", {"deviceId": ID})
            running.cancel_scope.cancel()  # cancel the running call: it completes anyway
            dispatcher.release.set()

    anyio.run(scenario)

    assert dispatcher.methods == ["pause"]
    assert dispatcher.max_running == 1
    # The lock was released once the cancelled call finished: the next call runs.
    dispatcher.release.set()
    assert structured(call(tools, "get_status", {"deviceId": ID})) == {"status": None}
    assert dispatcher.methods == ["pause", "get_status"]


# --- Through the MCP protocol -------------------------------------------------------------------


def test_an_mcp_client_lists_the_tools_and_reads_through_the_shared_layer(
    transport: FakeTransport,
) -> None:
    control = service(transport)

    async def scenario() -> tuple[list[str], types.CallToolResult, types.CallToolResult]:
        async with create_connected_server_and_client_session(build_server(control)) as client:
            listed = await client.list_tools()
            unknown = await client.call_tool("get_status", {"deviceId": "nope"})
            discovered = await client.call_tool("discover_devices", {})
            return [tool.name for tool in listed.tools], unknown, discovered

    names, unknown, discovered = anyio.run(scenario)

    assert names == [tool.name for tool in TOOLS]
    assert unknown.isError is True
    assert structured(unknown)["error"]["code"] == "device_not_found"
    assert structured(discovered)["devices"][0]["id"] == ID
    assert transport.attempted() == []


def test_the_real_stdio_server_starts_lists_tools_and_never_reaches_the_network() -> None:
    environment = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    parameters = StdioServerParameters(
        command=sys.executable, args=["-m", "control_tv.mcp_server"], env=environment
    )

    async def scenario() -> tuple[types.InitializeResult, list[str], types.CallToolResult]:
        with open(os.devnull, "w") as errlog:
            async with (
                stdio_client(parameters, errlog=errlog) as (read, write),
                ClientSession(read, write) as client,
            ):
                init = await client.initialize()
                listed = await client.list_tools()
                # Not discovered in this process: refused before any network access.
                unknown = await client.call_tool("get_status", {"deviceId": "never-seen"})
                return init, [tool.name for tool in listed.tools], unknown

    init, names, unknown = anyio.run(scenario)

    assert init.serverInfo.name == "control-tv"
    assert init.serverInfo.version == control_tv.__version__
    assert names == [tool.name for tool in TOOLS]
    assert structured(unknown)["error"]["code"] == "device_not_found"


# --- Dependency isolation ------------------------------------------------------------------------


def test_the_desktop_bridge_and_the_android_entry_point_never_import_the_mcp_sdk() -> None:
    script = (
        "import sys\n"
        "import control_tv.bridge, control_tv.embedded\n"
        "print(sorted(m for m in sys.modules if m == 'mcp' or m.startswith('mcp.')))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )

    assert completed.stdout.strip() == "[]"


def test_the_mcp_module_is_the_only_one_importing_the_sdk() -> None:
    importers = [
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "src" / "control_tv").rglob("*.py")
        if any(
            line.startswith(("import mcp", "from mcp"))
            for line in path.read_text(encoding="utf-8").splitlines()
        )
    ]

    assert importers == ["src/control_tv/mcp_server.py"]
    assert mcp_server.SERVER_NAME == "control-tv"
