"""Line-delimited JSON bridge exposed over stdio for the Tauri application shell.

This module is the *only* process boundary between the Tauri/Rust shell and the shared
control layer. It constructs one `ControlService` over one `PyChromecastTransport` and
dispatches requests to it; no Cast/control logic is duplicated in Rust or TypeScript.

Protocol (intentionally minimal - a starting point, expected to grow as more of
`TvControl` is exposed): the parent process writes one JSON object per line to this
process's stdin and reads one JSON object per line from its stdout, matched by "id".

Request:  {"id": <any>, "method": <str>, "params": {...}}
Response: {"id": <same as request, or null if the request itself could not be read>,
           "ok": true, "result": {...}}
       or {"id": ..., "ok": false, "error": {"code": <str>, "message": <str>}}

`id` is opaque to this module: it is only ever echoed back, never interpreted.

Methods (every one is read-only with respect to the TV):
- `ping`             -> {"status", "controlTvVersion"}
- `discover_devices` -> {"devices": [...]}; optional param `timeoutSeconds`
- `get_status`       -> {"status": {...}}; required param `deviceId` (the stable id from
                        discovery, never a display name). Fields the TV did not report are
                        `null`, never a default: `null` means unknown, not "off" or "zero".

An error `code` is either a `ControlError` code (`invalid_argument`, `device_not_found`,
`device_unavailable`, `timeout`, ...) or `internal_error` for an unexpected exception,
which is reported as an ordinary error response so a single bad request cannot kill this
long-lived process.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any, TextIO

import control_tv
from control_tv.domain import (
    ControlError,
    Device,
    DeviceId,
    DeviceStatus,
    ErrorCode,
    InvalidArgumentError,
)
from control_tv.service import DEFAULT_DISCOVERY_TIMEOUT, ControlService

Handler = Callable[[ControlService, dict[str, Any]], Any]
INTERNAL_ERROR_CODE = "internal_error"


def _device_to_json(device: Device) -> dict[str, Any]:
    return {
        "id": str(device.id),
        "friendlyName": device.friendly_name,
        "host": device.host,
        "port": device.port,
        "kind": device.kind.value,
        "modelName": device.model_name,
    }


def _status_to_json(status: DeviceStatus) -> dict[str, Any]:
    receiver = status.receiver
    media = status.media
    return {
        "deviceId": str(status.device_id),
        "connection": status.connection.value,
        "observedAt": status.observed_at.isoformat(),
        "receiver": None
        if receiver is None
        else {
            "appId": receiver.app_id,
            "appName": receiver.app_name,
            "volumeLevel": receiver.volume_level,
            "muted": receiver.muted,
            "standby": receiver.standby,
        },
        "media": None
        if media is None
        else {
            "playbackState": media.playback_state.value,
            "contentId": media.content_id,
            "contentType": media.content_type,
            "title": media.title,
            "positionSeconds": media.position_seconds,
            "durationSeconds": media.duration_seconds,
            "supportsSeek": media.supports_seek,
        },
    }


def _handle_ping(control: ControlService, params: dict[str, Any]) -> Any:
    del control, params
    return {"status": "ready", "controlTvVersion": control_tv.__version__}


def _handle_discover_devices(control: ControlService, params: dict[str, Any]) -> Any:
    timeout = params.get("timeoutSeconds", DEFAULT_DISCOVERY_TIMEOUT)
    if not isinstance(timeout, int | float):
        raise InvalidArgumentError(f"timeoutSeconds must be a number: {timeout!r}")
    devices = control.discover_devices(timeout=float(timeout))
    return {"devices": [_device_to_json(device) for device in devices]}


def _handle_get_status(control: ControlService, params: dict[str, Any]) -> Any:
    device_id = params.get("deviceId")
    if not isinstance(device_id, str):
        raise InvalidArgumentError(f"deviceId must be a string: {device_id!r}")
    return {"status": _status_to_json(control.get_status(DeviceId(device_id)))}


_HANDLERS: dict[str, Handler] = {
    "ping": _handle_ping,
    "discover_devices": _handle_discover_devices,
    "get_status": _handle_get_status,
}


def _error_response(request_id: Any, code: str, message: str) -> dict[str, Any]:
    return {"id": request_id, "ok": False, "error": {"code": code, "message": message}}


def dispatch(control: ControlService, request: dict[str, Any]) -> dict[str, Any]:
    """Run one already-parsed request object against `control` and build its response."""
    request_id = request.get("id")
    try:
        method = request.get("method")
        if not isinstance(method, str) or method not in _HANDLERS:
            raise InvalidArgumentError(f"unknown method: {method!r}")
        params = request.get("params", {})
        if not isinstance(params, dict):
            raise InvalidArgumentError("params must be an object")
        result = _HANDLERS[method](control, params)
    except ControlError as error:
        return _error_response(request_id, error.code.value, error.message)
    except Exception as error:
        # A process boundary: an unexpected exception must not kill this long-lived process
        # (every later request from the app would then fail), and must never look like success.
        return _error_response(request_id, INTERNAL_ERROR_CODE, f"{type(error).__name__}: {error}")
    return {"id": request_id, "ok": True, "result": result}


def handle_line(control: ControlService, line: str) -> dict[str, Any]:
    """Parse one request line and run it. Never raises: parse failures are error responses."""
    invalid = ErrorCode.INVALID_ARGUMENT.value
    try:
        request = json.loads(line)
    except json.JSONDecodeError as error:
        return _error_response(None, invalid, f"malformed JSON request: {error}")
    if not isinstance(request, dict):
        return _error_response(None, invalid, "request must be a JSON object")
    return dispatch(control, request)


def run(control: ControlService, *, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> None:
    """Serve requests until `stdin` is closed. Exactly one response line per request line."""
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        response = handle_line(control, line)
        stdout.write(json.dumps(response) + "\n")
        stdout.flush()


def main() -> None:
    from control_tv.adapters import PyChromecastTransport

    run(ControlService(PyChromecastTransport()))


if __name__ == "__main__":
    main()
