"""Local MCP server exposing the shared control layer to an MCP client (`control_tv[mcp]`).

Run it as a stdio server, started by the MCP client: `python -m control_tv.mcp_server`.
It owns one `ControlService(PyChromecastTransport())`, like the window's bridge process,
and every tool goes through the same `control_tv.bridge.dispatch` as the window: the same
parameter validation, the same `ControlService` calls and the same JSON shapes. No Cast,
validation, confirmation, discovery or media-identity logic lives here; this module only
adapts the bridge's answers for an assistant:

- privacy: device addresses (`host`, `port`) and media content ids (`contentId`, and any
  quoted value in a command's `detail`) are never returned; error messages are fixed per
  code (the bridge's own text can carry library exception text, addresses or ids), except
  `invalid_argument`, whose message is about the caller's own arguments;
- delivery: a command error says whether the command was not sent, was sent, or may have
  been sent (`timeout`, `internal_error`); a sent command keeps its `confirmation`, and only
  `confirmed` is worded as done. Nothing is ever resent: not here, not by the service;
- one tool call at a time (the shared layer is single-flight). A call cancelled while it
  waits is never run; a call cancelled while it runs completes on its worker thread and its
  answer is lost, so a cancelled command may have been sent (check with `get_status`).

Device names and ids, media titles and the receiver's application are visible to the MCP
client and its model. Logging (stderr only) names the tool and the outcome code, never a
device, address or media.
"""

from __future__ import annotations

import itertools
import json
import logging
import re
import sys
from collections.abc import Callable, Mapping
from typing import Any

import anyio
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

import control_tv
from control_tv.bridge import INTERNAL_ERROR_CODE, dispatch
from control_tv.service import ControlService
from mcp import types

LOGGER = logging.getLogger("control_tv.mcp")

SERVER_NAME = "control-tv"

READ_TOOLS = ("discover_devices", "get_status")
COMMAND_TOOLS = ("play", "pause", "stop", "seek", "set_volume", "set_muted")

NOT_SENT = "not_sent"
SENT = "sent"
UNKNOWN = "unknown"

# What a failed command means for delivery, per error code. Unlisted codes were not sent.
_COMMAND_DELIVERY = {
    "command_rejected": SENT,  # delivered; the receiver refused it
    "timeout": UNKNOWN,  # delivery timed out: it may or may not have arrived
    INTERNAL_ERROR_CODE: UNKNOWN,  # an unexpected failure may have happened after sending
}

_ERROR_MESSAGES = {
    "device_not_found": "This deviceId is not known to this server. Run discover_devices "
    "and use a deviceId it returns.",
    "ambiguous_target": "The device could not be identified unambiguously.",
    "device_unavailable": "The device could not be reached.",
    "discovery_failed": "Device discovery failed on the local network.",
    "unsupported_operation": "The device does not support this operation for its current "
    "media or volume control.",
    "unsupported_media": "The media is not supported.",
    "command_rejected": "The TV received the command and refused it.",
    "timeout": "The device did not answer in time.",
    INTERNAL_ERROR_CODE: "Unexpected internal error (details are only in the server's local log).",
}

_DELIVERY_NOTES = {
    NOT_SENT: "Nothing was sent to the TV.",
    SENT: "The command reached the TV.",
    UNKNOWN: "The command may or may not have reached the TV. Do not resend it "
    "automatically: call get_status to see what the TV reports.",
}

_DEVICE_ID = {
    "type": "string",
    "minLength": 1,
    "description": "The stable deviceId returned by discover_devices (never a display name).",
}

_COMMAND_NOTE = (
    " The answer separates sending from confirmation: only confirmation 'confirmed' means the "
    "TV reported the requested state. 'unconfirmed' means sent but not shown in time: do not "
    "resend automatically, call get_status. Nothing is ever retried."
)


def _tool(
    name: str, description: str, properties: dict[str, Any], required: list[str]
) -> types.Tool:
    read_only = name in READ_TOOLS
    return types.Tool(
        name=name,
        description=description,
        inputSchema={
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        annotations=types.ToolAnnotations(
            readOnlyHint=read_only,
            destructiveHint=False,
            # Commands are not advertised as idempotent so a client never retries them on
            # its own: a repeated command is a second send.
            idempotentHint=read_only,
            openWorldHint=False,
        ),
    )


TOOLS: list[types.Tool] = [
    _tool(
        "discover_devices",
        "Discover the Chromecast / Google TV devices on the local network. Returns each "
        "device's stable deviceId, name, kind and model. Read-only.",
        {
            "timeoutSeconds": {
                "type": "number",
                "exclusiveMinimum": 0,
                "description": "How long to listen for devices (default 5 seconds).",
            }
        },
        [],
    ),
    _tool(
        "get_status",
        "Read what a discovered device reports now: connection, receiver (application, "
        "volume, mute, standby) and media (state, title, artist, position, length, whether "
        "seek and pause are supported). A field the TV did not report is null, never a "
        "default. Read-only.",
        {"deviceId": _DEVICE_ID},
        ["deviceId"],
    ),
    _tool(
        "play", "Resume the current media." + _COMMAND_NOTE, {"deviceId": _DEVICE_ID}, ["deviceId"]
    ),
    _tool(
        "pause", "Pause the current media." + _COMMAND_NOTE, {"deviceId": _DEVICE_ID}, ["deviceId"]
    ),
    _tool(
        "stop",
        "Stop the current media session." + _COMMAND_NOTE,
        {"deviceId": _DEVICE_ID},
        ["deviceId"],
    ),
    _tool(
        "seek",
        "Move the current media to an absolute position, in seconds from its start, when the "
        "TV reports it seekable." + _COMMAND_NOTE,
        {
            "deviceId": _DEVICE_ID,
            "positionSeconds": {"type": "number", "minimum": 0},
        },
        ["deviceId", "positionSeconds"],
    ),
    _tool(
        "set_volume",
        "Set the receiver volume to an absolute level from 0.0 (silent) to 1.0 (maximum). "
        "Refused when the receiver reports a fixed volume." + _COMMAND_NOTE,
        {
            "deviceId": _DEVICE_ID,
            "level": {"type": "number", "minimum": 0, "maximum": 1},
        },
        ["deviceId", "level"],
    ),
    _tool(
        "set_muted",
        "Set the receiver's mute state: muted=true mutes, muted=false unmutes. An absolute "
        "state, never a toggle." + _COMMAND_NOTE,
        {"deviceId": _DEVICE_ID, "muted": {"type": "boolean"}},
        ["deviceId", "muted"],
    ),
]

_ALLOWED_ARGUMENTS = {tool.name: set(tool.inputSchema["properties"]) for tool in TOOLS}

_QUOTED = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")


# --- Privacy filtering -------------------------------------------------------------------


def public_device(device: Mapping[str, Any]) -> dict[str, Any]:
    """A discovered device without its network address."""
    return {key: value for key, value in device.items() if key not in {"host", "port"}}


def public_status(status: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """A device status without the media content id (often a private media URL)."""
    if status is None:
        return None
    public = dict(status)
    media = status.get("media")
    if isinstance(media, Mapping):
        public["media"] = {key: value for key, value in media.items() if key != "contentId"}
    return public


def redact_detail(detail: str | None) -> str | None:
    """A command detail with every quoted value (content ids, URLs) hidden."""
    return None if detail is None else _QUOTED.sub("<hidden>", detail)


# --- Presentation of bridge answers --------------------------------------------------------


def _text_result(text: str, structured: dict[str, Any], *, is_error: bool) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        structuredContent=structured,
        isError=is_error,
    )


def present_error(tool: str, code: str, message: str) -> types.CallToolResult:
    """An error answer: the code, a privacy-safe message and, for a command, its delivery."""
    public_message = (
        message
        if code == "invalid_argument"
        else _ERROR_MESSAGES.get(code, _ERROR_MESSAGES[INTERNAL_ERROR_CODE])
    )
    structured: dict[str, Any] = {"error": {"code": code, "message": public_message}}
    text = f"{tool} failed ({code}): {public_message}"
    if tool in COMMAND_TOOLS:
        delivery = _COMMAND_DELIVERY.get(code, NOT_SENT)
        structured["delivery"] = delivery
        text += " " + _DELIVERY_NOTES[delivery]
    return _text_result(text, structured, is_error=True)


def present_command(result: Mapping[str, Any]) -> types.CallToolResult:
    """A sent command: confirmed, unconfirmed (not a success) or not checked."""
    confirmation = result["confirmation"]
    detail = redact_detail(result.get("detail"))
    structured = {
        "command": result["command"],
        "deviceId": result["deviceId"],
        "delivery": SENT,
        "confirmation": confirmation,
        "detail": detail,
        "observed": public_status(result.get("observed")),
    }
    command = result["command"]
    if confirmation == "confirmed":
        text = f"{command} was sent and confirmed: the TV reports the requested state."
    elif confirmation == "unconfirmed":
        text = (
            f"{command} was sent but NOT confirmed: the TV did not show the requested state "
            f"in time ({detail}). Do not resend it automatically; call get_status to check."
        )
    else:
        text = f"{command} was sent; the result was not checked."
    return _text_result(text, structured, is_error=False)


def present(tool: str, response: Mapping[str, Any]) -> types.CallToolResult:
    """Turn one bridge response into the MCP tool answer."""
    if not response.get("ok"):
        error = response.get("error") or {}
        return present_error(
            tool, str(error.get("code", INTERNAL_ERROR_CODE)), str(error.get("message", ""))
        )
    result = response["result"]
    if tool == "discover_devices":
        devices = [public_device(device) for device in result["devices"]]
        names = ", ".join(f"{d['friendlyName']} (deviceId {d['id']})" for d in devices)
        text = f"Found {len(devices)} device(s)" + (f": {names}." if devices else ".")
        return _text_result(text, {"devices": devices}, is_error=False)
    if tool == "get_status":
        status = public_status(result["status"])
        return _text_result(json.dumps(status, indent=2), {"status": status}, is_error=False)
    # A command answers {"result": {command, deviceId, confirmation, detail, observed}}.
    return present_command(result["result"])


# --- Serialized execution ------------------------------------------------------------------


class ControlTvTools:
    """Runs MCP tool calls against one `ControlService`, one call at a time.

    `dispatch` is the window bridge's own request handler; it runs on a worker thread so a
    slow discovery or command does not block the MCP session, while the lock keeps the
    single-flight shared layer from ever running two calls at once.
    """

    def __init__(
        self,
        control: ControlService,
        dispatcher: Callable[[ControlService, dict[str, Any]], dict[str, Any]] = dispatch,
    ) -> None:
        self._control = control
        self._dispatch = dispatcher
        self._lock = anyio.Lock()
        self._ids = itertools.count(1)

    async def call(self, tool: str, arguments: Mapping[str, Any] | None) -> types.CallToolResult:
        """Never raises: every outcome, including an unexpected failure, is a tool result."""
        try:
            return await self._call(tool, dict(arguments or {}))
        except anyio.get_cancelled_exc_class():
            raise
        except Exception as error:  # an adapter bug must not leak its text to the client
            LOGGER.error("tool %s: unexpected %s", tool, type(error).__name__)
            return present_error(tool, INTERNAL_ERROR_CODE, "")

    async def _call(self, tool: str, arguments: dict[str, Any]) -> types.CallToolResult:
        if tool not in _ALLOWED_ARGUMENTS:
            return present_error(tool, "invalid_argument", f"unknown tool: {tool!r}")
        unexpected = sorted(set(arguments) - _ALLOWED_ARGUMENTS[tool])
        if unexpected:
            return present_error(
                tool, "invalid_argument", f"unexpected argument(s): {', '.join(unexpected)}"
            )
        request = {"id": next(self._ids), "method": tool, "params": arguments}
        async with self._lock:
            # Not abandoned on cancellation: the call runs to completion, so the lock is
            # held until the shared layer is free again and nothing runs concurrently.
            response = await anyio.to_thread.run_sync(self._dispatch, self._control, request)
        LOGGER.info("tool %s: %s", tool, _outcome(response))
        return present(tool, response)


def _outcome(response: Mapping[str, Any]) -> str:
    """What the log records about a call: the error code or the confirmation, nothing else."""
    if not response.get("ok"):
        return str((response.get("error") or {}).get("code", INTERNAL_ERROR_CODE))
    inner = (response.get("result") or {}).get("result")
    return str(inner.get("confirmation")) if isinstance(inner, Mapping) else "ok"


def build_server(control: ControlService) -> Server[Any, Any]:
    """The MCP server over `control`; the transport (stdio, in-memory) is chosen by the caller."""
    tools = ControlTvTools(control)
    server: Server[Any, Any] = Server(
        SERVER_NAME,
        version=control_tv.__version__,
        instructions=(
            "Controls Chromecast / Google TV devices on the local network. Call "
            "discover_devices first and address devices by the deviceId it returns. A command "
            "result separates 'sent' from 'confirmed'; never resend a command automatically."
        ),
    )

    @server.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]
    async def list_tools() -> list[types.Tool]:
        return TOOLS

    # Arguments are validated by the shared bridge and service (exact window semantics,
    # error codes included), not by the SDK's schema check.
    @server.call_tool(validate_input=False)  # type: ignore[untyped-decorator]
    async def call_tool(name: str, arguments: dict[str, Any] | None) -> types.CallToolResult:
        return await tools.call(name, arguments)

    return server


async def serve_stdio(control: ControlService) -> None:
    server = build_server(control)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    # stdout carries the MCP protocol: logs go to stderr only.
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(name)s: %(message)s")
    from control_tv.adapters import PyChromecastTransport

    anyio.run(serve_stdio, ControlService(PyChromecastTransport()))


if __name__ == "__main__":
    main()
