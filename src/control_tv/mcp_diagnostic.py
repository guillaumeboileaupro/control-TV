"""Read-only diagnostic MCP server for the ChatGPT reach test (gate G1, item S0).

It answers one question: can a ChatGPT surface (text, then voice mode) call a custom
control-TV MCP tool? It has exactly one tool, `control_tv_diagnostic`, which reads nothing
from the network, never imports the Cast layer, the bridge or the control service, and so
cannot reach or command a TV. It is not the control server (`control_tv.mcp_server`).

Run it as a stdio server: `python -m control_tv.mcp_diagnostic [--log-file PATH]`, for
example as the `--mcp-command` of OpenAI's Secure MCP Tunnel client, which keeps the server
off any network port (`docs/CHATGPT_VOICE_FEASIBILITY.md`). Each call is logged with a
per-process call number and the server's UTC time, so a call made from ChatGPT can be
matched with the moment the request was made. The optional `note` (the words of the request)
is returned to the caller and never logged: the log keeps only its length.
"""

from __future__ import annotations

import argparse
import itertools
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

import control_tv

LOGGER = logging.getLogger("control_tv.mcp_diagnostic")

SERVER_NAME = "control-tv-diagnostic"
TOOL_NAME = "control_tv_diagnostic"
NOTE_MAX_LENGTH = 200

TOOL = types.Tool(
    name=TOOL_NAME,
    description=(
        "Checks that control-TV's tools can be reached from this conversation. Use it when the "
        "user asks to test or check the control-TV connection. Read-only: it contacts no TV, "
        "sends no command and changes nothing. Pass the user's words as `note` if useful."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "note": {
                "type": "string",
                "maxLength": NOTE_MAX_LENGTH,
                "description": "Optional: the words of the request, echoed back.",
            }
        },
        "required": [],
        "additionalProperties": False,
    },
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


def _refuse(message: str) -> types.CallToolResult:
    return _result(
        f"Not run: {message}",
        {"error": {"code": "invalid_argument", "message": message}},
        is_error=True,
    )


class DiagnosticTool:
    """The tool's behavior; `now` is injectable for tests."""

    def __init__(self, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._now = now
        self._calls = itertools.count(1)

    def call(self, name: str, arguments: dict[str, Any] | None) -> types.CallToolResult:
        if name != TOOL_NAME:
            return _refuse(f"unknown tool {name!r}")
        arguments = arguments or {}
        unexpected = sorted(set(arguments) - {"note"})
        if unexpected:
            return _refuse(f"unexpected arguments: {', '.join(unexpected)}")
        note = arguments.get("note")
        if note is not None and not isinstance(note, str):
            return _refuse("note must be a string")
        if note is not None and len(note) > NOTE_MAX_LENGTH:
            return _refuse(f"note must be at most {NOTE_MAX_LENGTH} characters")
        number = next(self._calls)
        when = self._now().astimezone(UTC).isoformat(timespec="seconds")
        LOGGER.info(
            "diagnostic call #%d at %s (note: %d characters)",
            number,
            when,
            0 if note is None else len(note),
        )
        structured = {
            "server": SERVER_NAME,
            "version": control_tv.__version__,
            "call": number,
            "serverTimeUtc": when,
            "note": note,
            "tvContacted": False,
        }
        text = (
            f"control-TV diagnostic reached (call #{number} at {when} UTC). "
            "No TV was contacted and nothing was changed."
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
    options = parser.parse_args(argv)
    # stdout carries the MCP protocol: logs go to stderr (and the optional file) only.
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if options.log_file:
        handlers.append(_log_file_handler(options.log_file))
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s", handlers=handlers)
    anyio.run(serve_stdio)


if __name__ == "__main__":
    main()
