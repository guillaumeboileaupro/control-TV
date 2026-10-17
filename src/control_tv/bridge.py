"""Line-delimited JSON bridge exposed over stdio for the Tauri application shell.

This module is the *only* process boundary between the Tauri/Rust shell and the shared
control layer. It dispatches requests to the `TvControl` port; `default_service` builds the
one `ControlService` every host runs. No Cast/control logic is duplicated in Rust or
TypeScript.

Protocol (intentionally minimal - a starting point, expected to grow as more of
`TvControl` is exposed): the parent process writes one JSON object per line to this
process's stdin and reads one JSON object per line from its stdout, matched by "id".

Request:  {"id": <a JSON value>, "method": <str>, "params": {...}}
Response: {"id": <same as request, or null if the request itself could not be read>,
           "ok": true, "result": {...}}
       or {"id": ..., "ok": false, "error": {"code": <str>, "message": <str>}}

`id` is opaque to this module: it is only ever echoed back, never interpreted.

Read-only methods:
- `ping`             -> {"status", "controlTvVersion", "tvRemote", "tvApps"}: whether this
                        build embeds the TV's own remote channel and its app list
- `discover_devices` -> {"devices": [...]}; optional param `timeoutSeconds`. Each device
                        carries `androidTvRemote`: whether it advertises the Android TV
                        Remote service (`true`/`false`), or `null` when undetermined
- `get_status`       -> {"status": {...}}; required param `deviceId` (the stable id from
                        discovery, never a display name). Fields the TV did not report are
                        `null`, never a default: `null` means not reported, not "off" or "zero".
                        `media` carries `playbackState`, `contentId`, `contentType`, `title`,
                        `artist`, `streamType` ("buffered"/"live"), `metadataType` ("generic",
                        "movie", "tv_show", "music_track", "photo", "audiobook_chapter"),
                        `positionSeconds`, `durationSeconds`, `supportsSeek`, `supportsPause`.

Commands, each a plain forward to the same-named `TvControl` method (no Cast
logic, no retry, no replay), all taking the stable `deviceId`:
- `play`, `pause`, `stop`
- `seek`             -> also requires `positionSeconds` (a number, not a boolean)
- `set_volume`       -> also requires `level`, a number from 0.0 to 1.0 (not a boolean)
- `set_muted`        -> also requires `muted`, a JSON boolean: it is an absolute state, so
                        "mute" is `true` and "unmute" is `false`; there is no toggle
They answer {"result": {"command", "deviceId", "confirmation", "detail", "observed"}}.
`ok: true` on a command means the command was SENT - nothing more. Whether the TV then
showed the requested state is `result.confirmation`: `confirmed` (a status observed on the
TV shows it), `unconfirmed` (sent, but the TV did not show it in time; `detail` says what it
did report) or `not_checked` (sent, verification disabled). `unconfirmed` and `not_checked`
are not success: a caller must not present them as such. `observed` is the last status read
from the TV, in the same shape as `get_status`, or `null` if none could be read. A command
that could not be sent is an error response instead (`device_unavailable`,
`command_rejected` for a delivered command the receiver refused, `unsupported_operation`,
`timeout`, ...); a `timeout` on delivery is ambiguous, so a caller must not resend on its own.

The TV's own remote channel (Android TV Remote protocol), separate from Cast: a TV whose
Cast volume is fixed is heard at the volume the TV itself sets. All take `deviceId`, a TV
found by the last `discover_devices`; a Cast group or a TV whose `androidTvRemote` is
`false` answers `unsupported_operation`, a TV not paired yet answers `pairing_required`:
- `get_remote_status`     -> {"remote": {"isOn", "currentApp", "volume"}}: what the TV
                             reported; `volume` is {"level", "max", "muted"} or `null`,
                             `currentApp` the package name of the app in front or `null`,
                             `isOn` may be `null`
- `start_remote_pairing`  -> {"pairing": "code_shown"}: the TV shows a 6-character code
- `finish_remote_pairing` -> also requires `code`, the code shown; {"pairing": "paired"},
                             or `command_rejected` when the TV refuses the code
- `press_remote_key`      -> also requires `key`, one of `volume_up`, `volume_down`, `mute`
                             (the speaker), `power`, `up`, `down`, `left`, `right`, `ok`,
                             `back`, `home`, `delete` (Backspace in the TV's text field),
                             `enter` (submits it). The key is sent once, never resent. Answers
                             {"result": {"key", "deviceId", "confirmation", "detail", "before",
                             "after"}}: `confirmed` only when the TV reported the expected
                             volume or mute change (its report, not a person hearing it);
                             keys whose effect the TV does not report are `not_checked`.
- `type_remote_text`      -> also requires `text` (1 to 256 printable characters): sent once
                             to the TV's active text field, replacing what it holds. Answers
                             {"result": {"deviceId", "confirmation", "detail", "fieldReported",
                             "observed"}}, always `not_checked` (the TV does not echo text
                             back); `fieldReported` says whether the TV reported an active
                             field. The text is never echoed in an answer or an error: it may
                             be a password.
- `list_tv_apps`          -> {"apps": [{"package", "name"}]}: the apps installed on the TV
                             that its home screen can launch, as the TV lists them over its
                             network debugging (ADB), which the owner turns on; `name` is a
                             display name control-TV knows, or `null`. `debugging_off` when
                             the TV's debugging is off, `pairing_required` until the owner
                             allows control-TV on the TV, `unsupported_operation` for a Cast
                             group or a build without the app list. Read-only.
- `launch_tv_app`         -> also requires `package`: opens that app once, over the same
                             network debugging as the list (the app's home-screen activity).
                             Answers {"result": {"package", "deviceId", "confirmation",
                             "detail", "frontApp"}}: `confirmed` only when the TV then reports
                             that app in front, `not_checked` when it does not say.

An error `code` is either a `ControlError` code (`invalid_argument`, `device_not_found`,
`device_unavailable`, `timeout`, ...) or `internal_error` for an unexpected exception,
which is reported as an ordinary error response so a single bad request cannot kill this
long-lived process.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import TextIO

import control_tv
from control_tv.domain import (
    AppLaunchResult,
    CommandResult,
    ControlError,
    Device,
    DeviceId,
    DeviceStatus,
    ErrorCode,
    InvalidArgumentError,
    RemoteKey,
    RemoteKeyResult,
    RemoteObservation,
    RemoteTextResult,
    TvApp,
)
from control_tv.json_types import JsonObject, JsonValue
from control_tv.ports import TvControl
from control_tv.service import DEFAULT_DISCOVERY_TIMEOUT, ControlService

Handler = Callable[[TvControl, JsonObject], JsonObject]
INTERNAL_ERROR_CODE = "internal_error"


def _device_to_json(device: Device) -> JsonObject:
    return {
        "id": str(device.id),
        "friendlyName": device.friendly_name,
        "host": device.host,
        "port": device.port,
        "kind": device.kind.value,
        "modelName": device.model_name,
        "androidTvRemote": device.android_tv_remote,
    }


def _status_to_json(status: DeviceStatus) -> JsonObject:
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
            "volumeControlType": None
            if receiver.volume_control_type is None
            else receiver.volume_control_type.value,
        },
        "media": None
        if media is None
        else {
            "playbackState": media.playback_state.value,
            "contentId": media.content_id,
            "contentType": media.content_type,
            "title": media.title,
            "artist": media.artist,
            "streamType": None if media.stream_type is None else media.stream_type.value,
            "metadataType": None if media.metadata_type is None else media.metadata_type.value,
            "positionSeconds": media.position_seconds,
            "durationSeconds": media.duration_seconds,
            "supportsSeek": media.supports_seek,
            "supportsPause": media.supports_pause,
        },
    }


def _command_result_to_json(result: CommandResult) -> JsonObject:
    return {
        "command": result.command.value,
        "deviceId": str(result.device_id),
        "confirmation": result.confirmation.value,
        "detail": result.detail,
        "observed": None if result.observed is None else _status_to_json(result.observed),
    }


def _device_id_param(params: JsonObject) -> DeviceId:
    device_id = params.get("deviceId")
    if not isinstance(device_id, str):
        raise InvalidArgumentError(f"deviceId must be a string: {device_id!r}")
    return DeviceId(device_id)


def _handle_ping(control: TvControl, params: JsonObject) -> JsonObject:
    del control, params
    from control_tv.adapters.tv_apps import apps_installed
    from control_tv.adapters.tv_remote import channel_installed

    return {
        "status": "ready",
        "controlTvVersion": control_tv.__version__,
        "tvRemote": channel_installed(),
        "tvApps": apps_installed(),
    }


def _handle_discover_devices(control: TvControl, params: JsonObject) -> JsonObject:
    timeout = params.get("timeoutSeconds", DEFAULT_DISCOVERY_TIMEOUT)
    if not isinstance(timeout, int | float):
        raise InvalidArgumentError(f"timeoutSeconds must be a number: {timeout!r}")
    # The service validates the number itself (bool, non-finite, too large for a float).
    devices = control.discover_devices(timeout=timeout)
    return {"devices": [_device_to_json(device) for device in devices]}


def _handle_get_status(control: TvControl, params: JsonObject) -> JsonObject:
    return {"status": _status_to_json(control.get_status(_device_id_param(params)))}


def _handle_play(control: TvControl, params: JsonObject) -> JsonObject:
    return {"result": _command_result_to_json(control.play(_device_id_param(params)))}


def _handle_pause(control: TvControl, params: JsonObject) -> JsonObject:
    return {"result": _command_result_to_json(control.pause(_device_id_param(params)))}


def _handle_stop(control: TvControl, params: JsonObject) -> JsonObject:
    return {"result": _command_result_to_json(control.stop(_device_id_param(params)))}


def _handle_seek(control: TvControl, params: JsonObject) -> JsonObject:
    device_id = _device_id_param(params)
    position = params.get("positionSeconds")
    # A JSON boolean is an int in Python; it is never a position.
    if isinstance(position, bool) or not isinstance(position, int | float):
        raise InvalidArgumentError(f"positionSeconds must be a number: {position!r}")
    # The service validates the number itself (non-finite, negative, too large for a float).
    return {"result": _command_result_to_json(control.seek(device_id, position))}


def _handle_set_volume(control: TvControl, params: JsonObject) -> JsonObject:
    device_id = _device_id_param(params)
    level = params.get("level")
    # A JSON boolean is an int in Python; it is never a volume level.
    if isinstance(level, bool) or not isinstance(level, int | float):
        raise InvalidArgumentError(f"level must be a number: {level!r}")
    try:
        number = float(level)
    except OverflowError:
        raise InvalidArgumentError("level is too large to be a volume level") from None
    return {"result": _command_result_to_json(control.set_volume(device_id, number))}


def _handle_set_muted(control: TvControl, params: JsonObject) -> JsonObject:
    device_id = _device_id_param(params)
    muted = params.get("muted")
    # Strictly a boolean: 0, 1 and "true" are not accepted as a mute state.
    if not isinstance(muted, bool):
        raise InvalidArgumentError(f"muted must be a boolean: {muted!r}")
    return {"result": _command_result_to_json(control.set_muted(device_id, muted))}


REMOTE_KEYS: dict[str, RemoteKey] = {
    "volume_up": RemoteKey.VOLUME_UP,
    "volume_down": RemoteKey.VOLUME_DOWN,
    "mute": RemoteKey.VOLUME_MUTE,
    "power": RemoteKey.POWER,
    "up": RemoteKey.DPAD_UP,
    "down": RemoteKey.DPAD_DOWN,
    "left": RemoteKey.DPAD_LEFT,
    "right": RemoteKey.DPAD_RIGHT,
    "ok": RemoteKey.DPAD_CENTER,
    "back": RemoteKey.BACK,
    "home": RemoteKey.HOME,
    "delete": RemoteKey.DELETE,
    "enter": RemoteKey.ENTER,
}
_KEY_NAMES = {key: name for name, key in REMOTE_KEYS.items()}


def _remote_to_json(observation: RemoteObservation) -> JsonObject:
    volume = observation.volume
    return {
        "isOn": observation.is_on,
        "currentApp": observation.current_app,
        "volume": None
        if volume is None
        else {"level": volume.level, "max": volume.maximum, "muted": volume.muted},
    }


def _remote_key_result_to_json(result: RemoteKeyResult) -> JsonObject:
    return {
        "key": _KEY_NAMES[result.key],
        "deviceId": str(result.device_id),
        "confirmation": result.confirmation.value,
        "detail": result.detail,
        "before": _remote_to_json(result.before),
        "after": _remote_to_json(result.after),
    }


def _handle_get_remote_status(control: TvControl, params: JsonObject) -> JsonObject:
    return {"remote": _remote_to_json(control.get_remote_status(_device_id_param(params)))}


def _handle_start_remote_pairing(control: TvControl, params: JsonObject) -> JsonObject:
    control.start_remote_pairing(_device_id_param(params))
    return {"pairing": "code_shown"}


def _handle_finish_remote_pairing(control: TvControl, params: JsonObject) -> JsonObject:
    device_id = _device_id_param(params)
    code = params.get("code")
    if not isinstance(code, str):
        raise InvalidArgumentError("code must be a string")
    control.finish_remote_pairing(device_id, code)
    return {"pairing": "paired"}


def _handle_press_remote_key(control: TvControl, params: JsonObject) -> JsonObject:
    device_id = _device_id_param(params)
    name = params.get("key")
    if not isinstance(name, str) or name not in REMOTE_KEYS:
        raise InvalidArgumentError(f"key must be one of {', '.join(REMOTE_KEYS)}: {name!r}")
    result = control.press_remote_key(device_id, REMOTE_KEYS[name])
    return {"result": _remote_key_result_to_json(result)}


def _remote_text_result_to_json(result: RemoteTextResult) -> JsonObject:
    # Never the text itself: it may be a password.
    return {
        "deviceId": str(result.device_id),
        "confirmation": result.confirmation.value,
        "detail": result.detail,
        "fieldReported": result.field_reported,
        "observed": _remote_to_json(result.observed),
    }


def _handle_type_remote_text(control: TvControl, params: JsonObject) -> JsonObject:
    device_id = _device_id_param(params)
    text = params.get("text")
    if not isinstance(text, str):
        raise InvalidArgumentError("text must be a string")
    return {"result": _remote_text_result_to_json(control.type_remote_text(device_id, text))}


def _app_to_json(app: TvApp) -> JsonObject:
    return {"package": app.package, "name": app.name}


def _app_launch_to_json(result: AppLaunchResult) -> JsonObject:
    return {
        "package": result.package,
        "deviceId": str(result.device_id),
        "confirmation": result.confirmation.value,
        "detail": result.detail,
        "frontApp": result.front_app,
    }


def _handle_list_tv_apps(control: TvControl, params: JsonObject) -> JsonObject:
    apps = control.list_tv_apps(_device_id_param(params))
    return {"apps": [_app_to_json(app) for app in apps]}


def _handle_launch_tv_app(control: TvControl, params: JsonObject) -> JsonObject:
    device_id = _device_id_param(params)
    package = params.get("package")
    if not isinstance(package, str):
        raise InvalidArgumentError("package must be a string")
    return {"result": _app_launch_to_json(control.launch_tv_app(device_id, package))}


_HANDLERS: dict[str, Handler] = {
    "ping": _handle_ping,
    "discover_devices": _handle_discover_devices,
    "get_status": _handle_get_status,
    "play": _handle_play,
    "pause": _handle_pause,
    "stop": _handle_stop,
    "seek": _handle_seek,
    "set_volume": _handle_set_volume,
    "set_muted": _handle_set_muted,
    "get_remote_status": _handle_get_remote_status,
    "start_remote_pairing": _handle_start_remote_pairing,
    "finish_remote_pairing": _handle_finish_remote_pairing,
    "press_remote_key": _handle_press_remote_key,
    "type_remote_text": _handle_type_remote_text,
    "list_tv_apps": _handle_list_tv_apps,
    "launch_tv_app": _handle_launch_tv_app,
}


def _error_response(
    request_id: JsonValue, code: str, message: str, *, delivery: str | None = None
) -> JsonObject:
    response: JsonObject = {
        "id": request_id,
        "ok": False,
        "error": {"code": code, "message": message},
    }
    if delivery is not None:
        response["delivery"] = delivery
    return response


def dispatch(control: TvControl, request: JsonObject) -> JsonObject:
    """Run one already-parsed request object against `control` and build its response."""
    request_id = request.get("id")
    try:
        method = request.get("method")
        if not isinstance(method, str) or method not in _HANDLERS:
            raise InvalidArgumentError(f"not a bridge method: {method!r}")
        params = request.get("params", {})
        if not isinstance(params, dict):
            raise InvalidArgumentError("params must be an object")
        result = _HANDLERS[method](control, params)
    except ControlError as error:
        return _error_response(request_id, error.code.value, error.message, delivery=error.delivery)
    except Exception as error:
        # A process boundary: an unexpected exception must not kill this long-lived process
        # (every later request from the app would then fail), and must never look like success.
        return _error_response(request_id, INTERNAL_ERROR_CODE, f"{type(error).__name__}: {error}")
    return {"id": request_id, "ok": True, "result": result}


def handle_line(control: TvControl, line: str) -> JsonObject:
    """Parse one request line and run it. Never raises: parse failures are error responses."""
    invalid = ErrorCode.INVALID_ARGUMENT.value
    try:
        request = json.loads(line)
    except json.JSONDecodeError as error:
        return _error_response(None, invalid, f"malformed JSON request: {error}")
    if not isinstance(request, dict):
        return _error_response(None, invalid, "request must be a JSON object")
    return dispatch(control, request)


def run(control: TvControl, *, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> None:
    """Serve requests until `stdin` is closed. Exactly one response line per request line."""
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        response = handle_line(control, line)
        stdout.write(json.dumps(response) + "\n")
        stdout.flush()


def default_service() -> ControlService:
    """The service every host runs: Cast, plus the TV's own remote channel and app list."""
    from control_tv.adapters import PyChromecastTransport
    from control_tv.adapters.tv_apps import AdbAppInventory
    from control_tv.adapters.tv_remote import TvRemoteChannel, default_credentials_dir
    from control_tv.command_trace import recorder_from_environment

    credentials = default_credentials_dir()
    return ControlService(
        PyChromecastTransport(),
        remote=TvRemoteChannel(credentials),
        apps=AdbAppInventory(credentials),
        trace=recorder_from_environment(),
    )


def main() -> None:
    run(default_service())


if __name__ == "__main__":
    main()
