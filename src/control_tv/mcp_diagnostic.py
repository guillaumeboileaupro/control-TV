"""Read-only diagnostic MCP server for the ChatGPT reach test (gate G1, item S0).

It answers one question: can a ChatGPT surface (text, then native Voice) call a custom
control-TV MCP tool? It publishes exactly one tool, `control_tv_diagnostic`, which takes no
argument, reads nothing from the network, needs no TV or discovery, never imports the Cast
layer, the bridge or the control service, and so cannot reach or command a TV. Its answer
holds only the server's readiness, version, call number, UTC time and fixed scope: no device,
media, network, personal or secret data. It is not the control server
(`control_tv.mcp_server`), whose tools are absent from this server's `tools/list`.

Run it as a stdio server: `python -m control_tv.mcp_diagnostic [--log-file PATH]`, for
example as the `--mcp-command` of OpenAI's Secure MCP Tunnel client, which gives the server
no network port (`docs/CHATGPT_VOICE_FEASIBILITY.md`). Each call is logged with the tool
name, the outcome, a per-process call number and the server's UTC time, so a call made from
ChatGPT can be matched with the moment of the request; the log holds nothing else.
`--self-check` lists and calls the tool once through an in-memory MCP client and prints the
result, without any network.
"""

from __future__ import annotations

import argparse
import itertools
import json
import logging
import os
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.shared.memory import create_connected_server_and_client_session

import control_tv

LOGGER = logging.getLogger("control_tv.mcp_diagnostic")

SERVER_NAME = "control-tv-diagnostic"
TOOL_NAME = "control_tv_diagnostic"
SCOPE = "diagnostic only: no TV contacted, no discovery, no command"

TOOL = types.Tool(
    name=TOOL_NAME,
    description=(
        "Checks that control-TV's tools can be reached from this conversation. Use it when the "
        "user asks to test or check the control-TV connection. Read-only: it contacts no TV, "
        "discovers nothing, sends no command and changes nothing."
    ),
    inputSchema={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    annotations=types.ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)


def _result(text: str, structured: dict[str, Any], *, is_error: bool) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        structuredContent=structured,
        isError=is_error,
    )


def _refuse(reason: str) -> types.CallToolResult:
    # The refused input is never echoed or logged: only the outcome code.
    LOGGER.info("%s refused: invalid_argument", TOOL_NAME)
    return _result(
        f"Not run: {reason}.",
        {"error": {"code": "invalid_argument", "message": reason}},
        is_error=True,
    )


class DiagnosticTool:
    """The tool's behavior; `now` is injectable for tests."""

    def __init__(self, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._now = now
        self._calls = itertools.count(1)

    def call(self, name: str, arguments: dict[str, Any] | None) -> types.CallToolResult:
        if name != TOOL_NAME:
            return _refuse("this server has only the control_tv_diagnostic tool")
        if arguments:
            return _refuse("control_tv_diagnostic takes no argument")
        number = next(self._calls)
        when = self._now().astimezone(UTC).isoformat(timespec="seconds")
        LOGGER.info("%s call #%d at %s: ok", TOOL_NAME, number, when)
        structured = {
            "server": SERVER_NAME,
            "version": control_tv.__version__,
            "ready": True,
            "call": number,
            "serverTimeUtc": when,
            "scope": SCOPE,
            "tvContacted": False,
        }
        text = (
            f"control-TV diagnostic reached (call #{number} at {when} UTC). "
            "No TV was contacted, nothing was discovered and nothing was changed."
        )
        return _result(text, structured, is_error=False)


def build_server(tool: DiagnosticTool | None = None) -> Server[Any, Any]:
    """The diagnostic MCP server; the transport (stdio, in-memory) is chosen by the caller."""
    diagnostic = tool or DiagnosticTool()
    server: Server[Any, Any] = Server(
        SERVER_NAME,
        version=control_tv.__version__,
        instructions=(
            "Diagnostic server for control-TV. It only checks that control-TV's tools can be "
            "called from this conversation; it cannot see or control any TV."
        ),
    )

    @server.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]
    async def list_tools() -> list[types.Tool]:
        return [TOOL]

    @server.call_tool(validate_input=False)  # type: ignore[untyped-decorator]
    async def call_tool(name: str, arguments: dict[str, Any] | None) -> types.CallToolResult:
        return diagnostic.call(name, arguments)

    return server


async def self_check() -> dict[str, Any]:
    """`tools/list` and one call through an in-memory MCP client: no network, no TV."""
    async with create_connected_server_and_client_session(build_server()) as client:
        listed = await client.list_tools()
        called = await client.call_tool(TOOL_NAME, {})
    tools = [tool.name for tool in listed.tools]
    read_only = [bool(tool.annotations and tool.annotations.readOnlyHint) for tool in listed.tools]
    return {
        "tools": tools,
        "readOnly": read_only,
        "result": called.structuredContent,
        "ok": tools == [TOOL_NAME] and read_only == [True] and called.isError is False,
    }


async def serve_stdio() -> None:
    server = build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def _log_file_handler(path: str) -> logging.Handler:
    # Private to the user: created 0600, appended to, never truncated.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    return logging.StreamHandler(os.fdopen(descriptor, "a", encoding="utf-8"))


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m control_tv.mcp_diagnostic")
    parser.add_argument("--log-file", help="also append the call log to this file (mode 0600)")
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="list and call the tool once in memory, print the result and exit",
    )
    options = parser.parse_args(argv)
    # stdout carries the MCP protocol: logs go to stderr (and the optional file) only.
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if options.log_file:
        handlers.append(_log_file_handler(options.log_file))
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s", handlers=handlers)
    if options.self_check:
        report = anyio.run(self_check)
        print(json.dumps(report, indent=2, sort_keys=True))
        raise SystemExit(0 if report["ok"] else 1)
    anyio.run(serve_stdio)


if __name__ == "__main__":
    main()
