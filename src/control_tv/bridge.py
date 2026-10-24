"""Line-delimited JSON bridge exposed over stdio for the Tauri application shell.

This module is the *only* process boundary between the Tauri/Rust shell and the shared
control layer. It dispatches requests to the `TvControl` port; `default_service` builds the
one `ControlService` every host runs. No Cast/control logic is duplicated in Rust or
TypeScript.

Protocol (intentionally minimal - a starting point, expected to grow as more of
`TvControl` is exposed): the parent process writes one JSON object per line to this
process's stdin and reads one JSON object per line from its stdout, matched by "id".

Request:  {"id": <a JSON value>, "method": <str>, "params": {...}, "requestId": <str>?}
Response: {"id": <same as request, or null if the request itself could not be read>,
           "ok": true, "result": {...}}
       or {"id": ..., "ok": false, "error": {"code": <str>, "message": <str>}}

`id` is opaque to this module: it is only ever echoed back, never interpreted. `requestId`
(optional; letters, digits and dashes, 1-64) names the application request: a host that issues
the same request again (a retry, a new invocation) reuses it, so the acceptance trace counts
every invocation of that request (C7). Without it, each message is its own request.

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
- `get_screen_receiver_capabilities` -> the selected TV's reachability, HLS availability and
                             exact ControlTV Receiver installation/access state. A failed app
                             inventory is returned as an explicit capability state and never
                             guessed as "not installed". Read-only.

An error `code` is either a `ControlError` code (`invalid_argument`, `device_not_found`,
`device_unavailable`, `timeout`, ...) or `internal_error` for an unexpected exception,
which is reported as an ordinary error response so a single bad request cannot kill this
long-lived process.
"""

from __future__ import annotations

import json
import sys
import uuid
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, TextIO

import control_tv
from control_tv.command_trace import application_request, is_application_request_id
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
    UnsupportedOperationError,
)
from control_tv.json_types import JsonObject, JsonValue
from control_tv.ports import TvControl
from control_tv.service import DEFAULT_DISCOVERY_TIMEOUT, ControlService
from control_tv.trace_files import AcceptanceTracing

Handler = Callable[[TvControl, JsonObject], JsonObject]

if TYPE_CHECKING:
    from control_tv.screen_streaming.controller import (
        ScreenRequest,
        ScreenStatus,
        ScreenStreamController,
    )
    from control_tv.screen_streaming.display_layout import Display, DisplayManager, OutputSetting
    from control_tv.screen_streaming.virtual_display import Position
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


def _handle_get_screen_receiver_capabilities(control: TvControl, params: JsonObject) -> JsonObject:
    """Probe only the selected device through existing control-layer reads.

    A successful Cast status proves that the selected equipment is reachable and can use the
    existing HLS path. The ADB inventory separately proves whether the exact receiver package is
    installed and accessible; inability to read that inventory stays distinct from absence. A
    WebRTC confirmation additionally requires the already-paired Android Remote channel used for
    the one-time secret handoff.
    """
    device_id = _device_id_param(params)
    try:
        control.get_status(device_id)
    except ControlError as error:
        return {
            "capabilities": {
                "deviceAvailable": False,
                "hlsAvailable": False,
                "receiverAppInstalled": None,
                "receiverAccessible": False,
                "remoteAccessible": False,
                "webrtcConfirmed": False,
                "reason": error.code.value,
            }
        }
    try:
        apps = control.list_tv_apps(device_id)
    except ControlError as error:
        return {
            "capabilities": {
                "deviceAvailable": True,
                "hlsAvailable": True,
                "receiverAppInstalled": None,
                "receiverAccessible": False,
                "remoteAccessible": False,
                "webrtcConfirmed": False,
                "reason": error.code.value,
            }
        }
    from control_tv.screen_streaming.controller import WEBRTC_RECEIVER_PACKAGE

    installed = any(app.package == WEBRTC_RECEIVER_PACKAGE for app in apps)
    if not installed:
        return {
            "capabilities": {
                "deviceAvailable": True,
                "hlsAvailable": True,
                "receiverAppInstalled": False,
                "receiverAccessible": False,
                "remoteAccessible": False,
                "webrtcConfirmed": False,
                "reason": "receiver_not_installed",
            }
        }
    try:
        control.get_remote_status(device_id)
    except ControlError as error:
        return {
            "capabilities": {
                "deviceAvailable": True,
                "hlsAvailable": True,
                "receiverAppInstalled": True,
                "receiverAccessible": True,
                "remoteAccessible": False,
                "webrtcConfirmed": False,
                "reason": error.code.value,
            }
        }
    return {
        "capabilities": {
            "deviceAvailable": True,
            "hlsAvailable": True,
            "receiverAppInstalled": True,
            "receiverAccessible": True,
            "remoteAccessible": True,
            "webrtcConfirmed": True,
            "reason": None,
        }
    }


def _handle_launch_tv_app(control: TvControl, params: JsonObject) -> JsonObject:
    device_id = _device_id_param(params)
    package = params.get("package")
    if not isinstance(package, str):
        raise InvalidArgumentError("package must be a string")
    return {"result": _app_launch_to_json(control.launch_tv_app(device_id, package))}


def _handle_pair_tv_apps(control: TvControl, params: JsonObject) -> JsonObject:
    device_id = _device_id_param(params)
    pairing_endpoint = params.get("pairingEndpoint")
    code = params.get("code")
    if not isinstance(pairing_endpoint, str) or not isinstance(code, str):
        raise InvalidArgumentError("pairingEndpoint and code must be strings")
    control.pair_tv_apps(device_id, pairing_endpoint, code)
    return {"pairing": "paired"}


# The screen stream (D3/D4): one controller per bridge process, created on first use (never on
# Android or without X11), closed when the bridge exits so quitting the app stops the stream.
_screen_controller: ScreenStreamController | None = None


def _screen() -> ScreenStreamController:
    global _screen_controller
    if _screen_controller is None:
        from control_tv.screen_streaming.controller import ScreenStreamController

        _screen_controller = ScreenStreamController()
    return _screen_controller


def close_screen_stream() -> None:
    """Stop the screen stream, if one was ever started, and revert a screen configuration that
    was not kept (the bridge is exiting). Both are attempted."""
    try:
        if _screen_controller is not None:
            _screen_controller.close()
    finally:
        if _display_manager is not None:
            _display_manager.close()


_display_manager: DisplayManager | None = None


def _displays() -> DisplayManager:
    global _display_manager
    if _display_manager is None:
        from control_tv.screen_streaming.display_layout import DisplayManager

        _display_manager = DisplayManager()
    return _display_manager


def _layout_to_json(manager: DisplayManager, displays: Sequence[Display]) -> JsonObject:
    shown: list[JsonValue] = []
    for display in displays:
        setting = display.setting
        modes: list[JsonValue] = [
            {"id": mode.id, "width": mode.width, "height": mode.height, "rate": mode.rate}
            for mode in display.modes
        ]
        shown.append(
            {
                "name": display.name,
                "connected": display.connected,
                "enabled": setting.enabled,
                "mode": setting.mode,
                "x": setting.x,
                "y": setting.y,
                "rotation": setting.rotation.value,
                "scale": setting.scale,
                "primary": setting.primary,
                "modes": modes,
            }
        )
    return {
        "displays": shown,
        "awaitingConfirmation": manager.awaiting_confirmation,
        "revertedByItself": manager.take_reverted_notice(),
    }


def _setting_from_json(value: JsonValue) -> OutputSetting:
    from control_tv.screen_streaming.display_layout import OutputSetting, Rotation

    if not isinstance(value, dict):
        raise InvalidArgumentError("each display must be an object")
    name, enabled, mode = value.get("name"), value.get("enabled"), value.get("mode")
    rotation, scale, primary = value.get("rotation"), value.get("scale"), value.get("primary")
    if not isinstance(name, str) or not isinstance(enabled, bool):
        raise InvalidArgumentError("a display needs a name and enabled true or false")
    if not enabled:
        return OutputSetting(name, False)
    if not isinstance(mode, str) or not isinstance(primary, bool):
        raise InvalidArgumentError("a display that is on needs a mode and primary true or false")
    if rotation not in ("normal", "left", "right", "inverted"):
        raise InvalidArgumentError("rotation must be normal, left, right or inverted")
    if isinstance(scale, bool) or not isinstance(scale, int | float):
        raise InvalidArgumentError("scale must be a number")
    return OutputSetting(
        name,
        True,
        mode,
        _strict_int(value, "x"),
        _strict_int(value, "y"),
        Rotation(rotation),
        float(scale),
        primary,
    )


def _screen_call(call: Callable[[], JsonObject]) -> JsonObject:
    from control_tv.screen_streaming.model import InvalidStreamConfiguration, ScreenStreamError

    try:
        return call()
    except InvalidStreamConfiguration as error:
        raise InvalidArgumentError(error.message) from None
    except ScreenStreamError as error:
        raise UnsupportedOperationError(error.message) from None


def _screen_status_to_json(status: ScreenStatus) -> JsonObject:
    return {
        "state": status.state.value,
        "mode": None if status.mode is None else status.mode.value,
        "screen": status.screen,
        "width": status.width,
        "height": status.height,
        "fps": status.fps,
        "error": status.error,
    }


def _strict_int(params: JsonObject, name: str) -> int:
    value = params.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidArgumentError(f"{name} must be an integer: {value!r}")
    return value


def _handle_list_screens(control: TvControl, params: JsonObject) -> JsonObject:
    del control, params

    from control_tv.screen_streaming.virtual_display import REFRESH_RATES, SIZES

    def listed() -> JsonObject:
        controller = _screen()
        screens: list[JsonValue] = [
            {
                "name": output.name,
                "width": output.width,
                "height": output.height,
                "x": output.x,
                "y": output.y,
                "primary": output.primary,
            }
            for output in controller.screens()
        ]
        # What "Étendre" can offer here, as measured on the desktop; no output means the
        # extension is not available until the owner enables one.
        extension: JsonObject = {
            "output": controller.extension_output(),
            "sizes": [[width, height] for width, height in SIZES],
            "refreshRates": list(REFRESH_RATES),
            "positions": list(_positions()),
        }
        return {"screens": screens, "extension": extension}

    return _screen_call(listed)


def _positions() -> dict[str, Position]:
    from control_tv.screen_streaming.virtual_display import Position

    return {
        "left": Position.LEFT,
        "right": Position.RIGHT,
        "above": Position.ABOVE,
        "below": Position.BELOW,
    }


def _screen_request(params: JsonObject) -> ScreenRequest:
    from control_tv.screen_streaming.controller import ScreenRequest
    from control_tv.screen_streaming.d1 import Mode

    mode, screen, consent = params.get("mode"), params.get("screen"), params.get("consent")
    if mode not in ("duplicate", "extend"):
        raise InvalidArgumentError(f"mode must be duplicate or extend: {mode!r}")
    if screen is not None and not isinstance(screen, str):
        raise InvalidArgumentError("screen must be a string")
    if not isinstance(consent, bool):
        raise InvalidArgumentError("consent must be true or false")
    position = params.get("position", "right")
    if not isinstance(position, str) or position not in _positions():
        raise InvalidArgumentError("position must be left, right, above or below")
    refresh = _strict_int(params, "refresh") if "refresh" in params else 60
    return ScreenRequest(
        str(_device_id_param(params)),
        Mode(mode),
        screen,
        _strict_int(params, "width"),
        _strict_int(params, "height"),
        _strict_int(params, "fps"),
        consent,
        refresh,
        _positions()[position],
        _strict_int(params, "x") if "x" in params else None,
        _strict_int(params, "y") if "y" in params else None,
    )


def _handle_start_screen_stream(control: TvControl, params: JsonObject) -> JsonObject:
    del control
    request = _screen_request(params)
    return _screen_call(lambda: {"stream": _screen_status_to_json(_screen().start(request))})


def _handle_prepare_webrtc_capture(control: TvControl, params: JsonObject) -> JsonObject:
    del control

    def prepare() -> JsonObject:
        lease = _screen().prepare_webrtc_capture(_screen_request(params))
        return {
            "capture": {
                "bindAddress": lease.bind_address,
                "display": lease.display,
                "offsetX": lease.offset_x,
                "offsetY": lease.offset_y,
                "sourceWidth": lease.source_width,
                "sourceHeight": lease.source_height,
            }
        }

    return _screen_call(prepare)


def _handle_activate_webrtc_capture(control: TvControl, params: JsonObject) -> JsonObject:
    del control, params
    return _screen_call(
        lambda: {"stream": _screen_status_to_json(_screen().activate_webrtc_capture())}
    )


def _handle_release_webrtc_capture(control: TvControl, params: JsonObject) -> JsonObject:
    del control
    error = params.get("error")
    if error is not None and not isinstance(error, str):
        raise InvalidArgumentError("error must be a string or null")
    return _screen_call(
        lambda: {"stream": _screen_status_to_json(_screen().release_webrtc_capture(error))}
    )


def _handle_stop_screen_stream(control: TvControl, params: JsonObject) -> JsonObject:
    del control, params
    return {"stream": _screen_status_to_json(_screen().stop())}


def _handle_get_display_layout(control: TvControl, params: JsonObject) -> JsonObject:
    del control, params
    return _screen_call(lambda: _layout_to_json(_displays(), _displays().layout()))


def _handle_apply_display_layout(control: TvControl, params: JsonObject) -> JsonObject:
    del control
    displays = params.get("displays")
    if not isinstance(displays, list):
        raise InvalidArgumentError("displays must be a list")
    settings = [_setting_from_json(value) for value in displays]
    return _screen_call(lambda: _layout_to_json(_displays(), _displays().apply(settings)))


def _handle_confirm_display_layout(control: TvControl, params: JsonObject) -> JsonObject:
    del control, params
    _displays().confirm()
    return _screen_call(lambda: _layout_to_json(_displays(), _displays().layout()))


def _handle_revert_display_layout(control: TvControl, params: JsonObject) -> JsonObject:
    del control, params
    return _screen_call(lambda: _layout_to_json(_displays(), _displays().revert()))


def _handle_get_screen_stream(control: TvControl, params: JsonObject) -> JsonObject:
    del control, params
    return {"stream": _screen_status_to_json(_screen().status())}


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
    "get_screen_receiver_capabilities": _handle_get_screen_receiver_capabilities,
    "launch_tv_app": _handle_launch_tv_app,
    "pair_tv_apps": _handle_pair_tv_apps,
    "list_screens": _handle_list_screens,
    "start_screen_stream": _handle_start_screen_stream,
    "prepare_webrtc_capture": _handle_prepare_webrtc_capture,
    "activate_webrtc_capture": _handle_activate_webrtc_capture,
    "release_webrtc_capture": _handle_release_webrtc_capture,
    "stop_screen_stream": _handle_stop_screen_stream,
    "get_screen_stream": _handle_get_screen_stream,
    "get_display_layout": _handle_get_display_layout,
    "apply_display_layout": _handle_apply_display_layout,
    "confirm_display_layout": _handle_confirm_display_layout,
    "revert_display_layout": _handle_revert_display_layout,
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
        given = request.get("requestId", uuid.uuid4().hex)
        if not isinstance(given, str) or not is_application_request_id(given):
            raise InvalidArgumentError(
                f"requestId must be 1 to 64 letters, digits or dashes: {given!r}"
            )
        with application_request(given):
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
    """Serve requests until `stdin` is closed. Exactly one response line per request line. The
    screen stream, if any, is stopped when it ends (the application quits)."""
    try:
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            response = handle_line(control, line)
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()
    finally:
        close_screen_stream()


def default_service(tracing: AcceptanceTracing | None = None) -> ControlService:
    """The service every host runs: Cast, plus the TV's own remote channel and app list.

    Acceptance tracing is off unless configured: by the environment on a desktop host
    (`docs/COMMAND_TRACE.md`), or by the Android debug build through `tracing`. A configuration
    that names no valid role is refused rather than guessed."""
    from control_tv.adapters import PyChromecastTransport
    from control_tv.adapters.tv_apps import AdbAppInventory
    from control_tv.adapters.tv_remote import TvRemoteChannel, default_credentials_dir
    from control_tv.trace_files import tracing_from_environment

    active = tracing_from_environment() if tracing is None else tracing
    credentials = default_credentials_dir()
    return ControlService(
        PyChromecastTransport(),
        remote=TvRemoteChannel(credentials),
        apps=AdbAppInventory(credentials),
        trace=active.recorder,
        gate=active.gate,
    )


def main() -> None:
    run(default_service())


if __name__ == "__main__":
    main()
