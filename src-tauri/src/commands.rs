//! The Tauri commands the window calls: each builds one bridge request and forwards it with
//! its own outer time bound. No retry, no state of their own, no Cast/control logic.

use std::time::Duration;

use serde_json::{json, Value};

use crate::bridge::{call_bridge, BridgeFailure, SharedBridgeState};

/// Upper bound on `ping`: this call never has an operator-supplied timeout, so it gets
/// a fixed, generous one - long enough for a healthy bridge under load, short enough
/// that a genuinely stuck bridge is reported quickly.
pub(crate) const PING_TIMEOUT: Duration = Duration::from_secs(5);
/// Safety margin added on top of a caller-supplied discovery timeout, so a slow-but-
/// working discovery (bounded by `ControlService` itself) is never cut off by this
/// outer guard first; only a bridge that is truly stuck exceeds it.
pub(crate) const DISCOVERY_TIMEOUT_MARGIN: Duration = Duration::from_secs(5);
/// Outer bound on one `get_status` round trip: twice the control layer's own 5s status
/// budget (`DEFAULT_STATUS_TIMEOUT` in `control_tv.service`), so only a genuinely stuck
/// bridge exceeds it. The bridge is single-flight and this timer starts before a request
/// gets its turn, so a request must never wait behind another: the UI keeps at most one
/// request in flight (it offers no selection, refresh or discovery while one is running).
pub(crate) const STATUS_TIMEOUT: Duration = Duration::from_secs(10);
/// Outer bound on one playback command. The control layer runs a command inside its own
/// windows - an identity snapshot and the confirmation share one 5s window, and delivering
/// the command may first wait for a connection with one bounded recovery, about 35s in the
/// worst case - so this is deliberately generous: only a genuinely stuck bridge exceeds it.
/// The UI keeps one request in flight and never resends on a timeout (delivery is then
/// ambiguous), so a long bound only ever delays reporting a stuck bridge.
pub(crate) const COMMAND_TIMEOUT: Duration = Duration::from_secs(60);
/// Outer bound on one request on the TV's own remote channel (status, pairing or one key). The
/// control layer bounds each such operation to 8s (`DEFAULT_TIMEOUT` in
/// `control_tv.adapters.tv_remote`); this is generous on top of it, so only a stuck bridge
/// exceeds it. A key is never resent on a timeout: its delivery is then ambiguous.
pub(crate) const REMOTE_TIMEOUT: Duration = Duration::from_secs(20);
/// Outer bound on listing or opening the TV's apps over its network debugging. A first,
/// slow authorization in the pinned adb-shell runs in stages: up to 5s to open the socket, up
/// to 5s for each of its two first handshake answers, then up to 30s for the owner to allow
/// control-TV on the TV; then 10s for the list, or one 15s budget shared by a launch and its
/// checks of the app in front (`control_tv.adapters.tv_apps`). That is 70s at most before
/// bridge overhead; this bound leaves margin above it.
pub(crate) const APPS_TIMEOUT: Duration = Duration::from_secs(90);
/// Outer bound on one screen stream request. Starting and stopping only hand the session to the
/// bridge's own background thread and return; listing the screens reads `xrandr` (5s at most in
/// `control_tv.screen_streaming.x11_capture`). Only a stuck bridge exceeds this bound.
pub(crate) const SCREEN_TIMEOUT: Duration = Duration::from_secs(15);

/// Desktop has no home-screen widget; accepting the selection keeps one frontend contract.
#[cfg(not(target_os = "android"))]
#[tauri::command]
pub(crate) async fn remember_selection(device_id: String, name: String) -> Result<(), String> {
    let _ = (device_id, name);
    Ok(())
}

#[tauri::command]
pub(crate) async fn bridge_ping(
    state: tauri::State<'_, SharedBridgeState>,
) -> Result<Value, BridgeFailure> {
    call_bridge(state.inner().clone(), "ping", json!({}), PING_TIMEOUT).await
}

/// Pure request-building logic for `bridge_discover_devices`, factored out so it is
/// testable without a running `tauri::App` to construct a `State` from.
pub(crate) fn discovery_request(timeout_seconds: Option<f64>) -> (Value, Duration) {
    let mut params = serde_json::Map::new();
    let mut timeout = PING_TIMEOUT + DISCOVERY_TIMEOUT_MARGIN;
    if let Some(timeout_seconds) = timeout_seconds {
        params.insert("timeoutSeconds".to_string(), json!(timeout_seconds));
        timeout = Duration::from_secs_f64(timeout_seconds.max(0.0)) + DISCOVERY_TIMEOUT_MARGIN;
    }
    (Value::Object(params), timeout)
}

#[tauri::command]
pub(crate) async fn bridge_discover_devices(
    state: tauri::State<'_, SharedBridgeState>,
    timeout_seconds: Option<f64>,
) -> Result<Value, BridgeFailure> {
    let (params, timeout) = discovery_request(timeout_seconds);
    call_bridge(state.inner().clone(), "discover_devices", params, timeout).await
}

/// The request for one status read: only the stable device id, exactly as discovery
/// reported it. Whether that id is valid, known or reachable is decided by the shared
/// control layer, never here.
pub(crate) fn status_request(device_id: &str) -> Value {
    json!({ "deviceId": device_id })
}

pub(crate) async fn read_status(
    state: SharedBridgeState,
    device_id: &str,
) -> Result<Value, BridgeFailure> {
    call_bridge(
        state,
        "get_status",
        status_request(device_id),
        STATUS_TIMEOUT,
    )
    .await
}

/// Read-only: asks the shared control layer for the selected device's observed status.
/// Sends no playback, volume or mute command.
#[tauri::command]
pub(crate) async fn bridge_get_status(
    state: tauri::State<'_, SharedBridgeState>,
    device_id: String,
) -> Result<Value, BridgeFailure> {
    read_status(state.inner().clone(), &device_id).await
}

/// The request for a seek: the stable device id and the requested position in seconds. Whether
/// the position is valid, or the media can be seeked at all, is decided by the control layer.
pub(crate) fn seek_request(device_id: &str, position_seconds: f64) -> Value {
    json!({ "deviceId": device_id, "positionSeconds": position_seconds })
}

/// Forwards `play`, `pause` or `stop` for one device. A plain forward: no retry (a timeout
/// leaves delivery ambiguous, so resending is the operator's decision) and no state of its
/// own. The answer is data: `ok` means the command was sent, `confirmation` says whether the
/// TV then showed the result.
pub(crate) async fn send_transport(
    state: SharedBridgeState,
    method: &'static str,
    device_id: &str,
) -> Result<Value, BridgeFailure> {
    call_bridge(state, method, status_request(device_id), COMMAND_TIMEOUT).await
}

pub(crate) async fn send_seek(
    state: SharedBridgeState,
    device_id: &str,
    position_seconds: f64,
) -> Result<Value, BridgeFailure> {
    call_bridge(
        state,
        "seek",
        seek_request(device_id, position_seconds),
        COMMAND_TIMEOUT,
    )
    .await
}

#[tauri::command]
pub(crate) async fn bridge_play(
    state: tauri::State<'_, SharedBridgeState>,
    device_id: String,
) -> Result<Value, BridgeFailure> {
    send_transport(state.inner().clone(), "play", &device_id).await
}

#[tauri::command]
pub(crate) async fn bridge_pause(
    state: tauri::State<'_, SharedBridgeState>,
    device_id: String,
) -> Result<Value, BridgeFailure> {
    send_transport(state.inner().clone(), "pause", &device_id).await
}

#[tauri::command]
pub(crate) async fn bridge_stop(
    state: tauri::State<'_, SharedBridgeState>,
    device_id: String,
) -> Result<Value, BridgeFailure> {
    send_transport(state.inner().clone(), "stop", &device_id).await
}

#[tauri::command]
pub(crate) async fn bridge_seek(
    state: tauri::State<'_, SharedBridgeState>,
    device_id: String,
    position_seconds: f64,
) -> Result<Value, BridgeFailure> {
    send_seek(state.inner().clone(), &device_id, position_seconds).await
}

/// The request for a volume change: the stable device id and the requested level from 0.0 to
/// 1.0. Whether the level is valid is decided by the control layer, never here.
pub(crate) fn volume_request(device_id: &str, level: f64) -> Value {
    json!({ "deviceId": device_id, "level": level })
}

/// The request for a mute change. `muted` is the state to reach (`true` mutes, `false` unmutes),
/// never a toggle: the same request sent twice ends in the same state.
pub(crate) fn muted_request(device_id: &str, muted: bool) -> Value {
    json!({ "deviceId": device_id, "muted": muted })
}

/// Forwards a volume change. A plain forward like the playback commands: no retry, no state of
/// its own, and the answer is data (`ok` means sent, `confirmation` says whether the TV showed it).
pub(crate) async fn send_set_volume(
    state: SharedBridgeState,
    device_id: &str,
    level: f64,
) -> Result<Value, BridgeFailure> {
    call_bridge(
        state,
        "set_volume",
        volume_request(device_id, level),
        COMMAND_TIMEOUT,
    )
    .await
}

pub(crate) async fn send_set_muted(
    state: SharedBridgeState,
    device_id: &str,
    muted: bool,
) -> Result<Value, BridgeFailure> {
    call_bridge(
        state,
        "set_muted",
        muted_request(device_id, muted),
        COMMAND_TIMEOUT,
    )
    .await
}

#[tauri::command]
pub(crate) async fn bridge_set_volume(
    state: tauri::State<'_, SharedBridgeState>,
    device_id: String,
    level: f64,
) -> Result<Value, BridgeFailure> {
    send_set_volume(state.inner().clone(), &device_id, level).await
}

#[tauri::command]
pub(crate) async fn bridge_set_muted(
    state: tauri::State<'_, SharedBridgeState>,
    device_id: String,
    muted: bool,
) -> Result<Value, BridgeFailure> {
    send_set_muted(state.inner().clone(), &device_id, muted).await
}

/// The request for one key on the TV's own remote channel: the stable device id and the key's
/// name (`volume_up`, `mute`, `power`, `ok`, ...). Which names exist is decided by the control
/// layer, which refuses every other name before anything is sent.
pub(crate) fn remote_key_request(device_id: &str, key: &str) -> Value {
    json!({ "deviceId": device_id, "key": key })
}

/// The request that finishes pairing: the code the TV shows, passed as typed. Its format is
/// checked by the control layer.
pub(crate) fn pairing_code_request(device_id: &str, code: &str) -> Value {
    json!({ "deviceId": device_id, "code": code })
}

/// The request that types text into the TV's active field. The text is forwarded as typed and
/// never logged: it may be a password.
pub(crate) fn remote_text_request(device_id: &str, text: &str) -> Value {
    json!({ "deviceId": device_id, "text": text })
}

/// The request that opens an installed app on the TV: the stable device id and the app's
/// package name, checked by the control layer before anything is sent.
pub(crate) fn app_request(device_id: &str, package: &str) -> Value {
    json!({ "deviceId": device_id, "package": package })
}

pub(crate) fn app_pairing_request(device_id: &str, pairing_endpoint: &str, code: &str) -> Value {
    json!({ "deviceId": device_id, "pairingEndpoint": pairing_endpoint, "code": code })
}

/// Forwards one request on the TV's own remote channel. A plain forward like the Cast commands:
/// no retry, no state of its own, and the answer is data.
pub(crate) async fn send_remote(
    state: SharedBridgeState,
    method: &'static str,
    params: Value,
) -> Result<Value, BridgeFailure> {
    call_bridge(state, method, params, REMOTE_TIMEOUT).await
}

/// Read-only: what the TV reports on its own remote channel (power, audible volume and mute).
#[tauri::command]
pub(crate) async fn bridge_get_remote_status(
    state: tauri::State<'_, SharedBridgeState>,
    device_id: String,
) -> Result<Value, BridgeFailure> {
    send_remote(
        state.inner().clone(),
        "get_remote_status",
        status_request(&device_id),
    )
    .await
}

/// Asks the TV to show a pairing code.
#[tauri::command]
pub(crate) async fn bridge_start_remote_pairing(
    state: tauri::State<'_, SharedBridgeState>,
    device_id: String,
) -> Result<Value, BridgeFailure> {
    send_remote(
        state.inner().clone(),
        "start_remote_pairing",
        status_request(&device_id),
    )
    .await
}

#[tauri::command]
pub(crate) async fn bridge_finish_remote_pairing(
    state: tauri::State<'_, SharedBridgeState>,
    device_id: String,
    code: String,
) -> Result<Value, BridgeFailure> {
    send_remote(
        state.inner().clone(),
        "finish_remote_pairing",
        pairing_code_request(&device_id, &code),
    )
    .await
}

#[tauri::command]
pub(crate) async fn bridge_press_remote_key(
    state: tauri::State<'_, SharedBridgeState>,
    device_id: String,
    key: String,
) -> Result<Value, BridgeFailure> {
    send_remote(
        state.inner().clone(),
        "press_remote_key",
        remote_key_request(&device_id, &key),
    )
    .await
}

#[tauri::command]
pub(crate) async fn bridge_type_remote_text(
    state: tauri::State<'_, SharedBridgeState>,
    device_id: String,
    text: String,
) -> Result<Value, BridgeFailure> {
    send_remote(
        state.inner().clone(),
        "type_remote_text",
        remote_text_request(&device_id, &text),
    )
    .await
}

/// Read-only: the apps installed on the TV, listed over its network debugging.
#[tauri::command]
pub(crate) async fn bridge_list_tv_apps(
    state: tauri::State<'_, SharedBridgeState>,
    device_id: String,
) -> Result<Value, BridgeFailure> {
    call_bridge(
        state.inner().clone(),
        "list_tv_apps",
        status_request(&device_id),
        APPS_TIMEOUT,
    )
    .await
}

#[tauri::command]
pub(crate) async fn bridge_launch_tv_app(
    state: tauri::State<'_, SharedBridgeState>,
    device_id: String,
    package: String,
) -> Result<Value, BridgeFailure> {
    // Over the TV's debugging, like the list: the owner may first have to allow control-TV.
    call_bridge(
        state.inner().clone(),
        "launch_tv_app",
        app_request(&device_id, &package),
        APPS_TIMEOUT,
    )
    .await
}

#[tauri::command]
pub(crate) async fn bridge_pair_tv_apps(
    state: tauri::State<'_, SharedBridgeState>,
    device_id: String,
    pairing_endpoint: String,
    code: String,
) -> Result<Value, BridgeFailure> {
    call_bridge(
        state.inner().clone(),
        "pair_tv_apps",
        app_pairing_request(&device_id, &pairing_endpoint, &code),
        APPS_TIMEOUT,
    )
    .await
}

/// Where and how "extend" places the TV's screen: its refresh rate and its side of the primary
/// screen ("left", "right", "above" or "below"), as the window's extension menu chose them.
pub(crate) struct Placement<'a> {
    pub(crate) refresh: u32,
    pub(crate) position: &'a str,
}

/// The request that starts streaming this computer's screen to the TV: "duplicate" one screen or
/// "extend" onto a second screen, at one of the offered sizes and rates. `consent` is the
/// owner's explicit agreement given in the window; the bridge refuses without it.
pub(crate) fn screen_stream_request(
    device_id: &str,
    mode: &str,
    screen: Option<&str>,
    size: (u32, u32),
    fps: u32,
    consent: bool,
    placement: &Placement<'_>,
) -> Value {
    json!({
        "deviceId": device_id,
        "mode": mode,
        "screen": screen,
        "width": size.0,
        "height": size.1,
        "fps": fps,
        "consent": consent,
        "refresh": placement.refresh,
        "position": placement.position,
    })
}

/// Forwards one screen stream request: no retry, no state of its own; the session lives in the
/// bridge, which stops it when it exits.
pub(crate) async fn send_screen(
    state: SharedBridgeState,
    method: &'static str,
    params: Value,
) -> Result<Value, BridgeFailure> {
    call_bridge(state, method, params, SCREEN_TIMEOUT).await
}

/// Read-only: the screens that can be duplicated (an X11 session only).
#[tauri::command]
pub(crate) async fn bridge_list_screens(
    state: tauri::State<'_, SharedBridgeState>,
) -> Result<Value, BridgeFailure> {
    send_screen(state.inner().clone(), "list_screens", json!({})).await
}

#[tauri::command]
#[allow(clippy::too_many_arguments)]
pub(crate) async fn bridge_start_screen_stream(
    state: tauri::State<'_, SharedBridgeState>,
    device_id: String,
    mode: String,
    screen: Option<String>,
    width: u32,
    height: u32,
    fps: u32,
    consent: bool,
    refresh: Option<u32>,
    position: Option<String>,
) -> Result<Value, BridgeFailure> {
    let placement = Placement {
        refresh: refresh.unwrap_or(60),
        position: position.as_deref().unwrap_or("right"),
    };
    let params = screen_stream_request(
        &device_id,
        &mode,
        screen.as_deref(),
        (width, height),
        fps,
        consent,
        &placement,
    );
    send_screen(state.inner().clone(), "start_screen_stream", params).await
}

#[tauri::command]
pub(crate) async fn bridge_stop_screen_stream(
    state: tauri::State<'_, SharedBridgeState>,
) -> Result<Value, BridgeFailure> {
    send_screen(state.inner().clone(), "stop_screen_stream", json!({})).await
}

/// Read-only: the desktop's screens, their modes and how each is set.
#[tauri::command]
pub(crate) async fn bridge_get_display_layout(
    state: tauri::State<'_, SharedBridgeState>,
) -> Result<Value, BridgeFailure> {
    send_screen(state.inner().clone(), "get_display_layout", json!({})).await
}

/// The request that sets every screen: the window's display manager sends the whole layout.
pub(crate) fn display_layout_request(displays: Value) -> Value {
    json!({ "displays": displays })
}

/// Sets every screen; the bridge restores the previous configuration if this fails, and reverts
/// it by itself unless it is kept (`bridge_confirm_display_layout`) in time.
#[tauri::command]
pub(crate) async fn bridge_apply_display_layout(
    state: tauri::State<'_, SharedBridgeState>,
    displays: Value,
) -> Result<Value, BridgeFailure> {
    let params = display_layout_request(displays);
    send_screen(state.inner().clone(), "apply_display_layout", params).await
}

#[tauri::command]
pub(crate) async fn bridge_confirm_display_layout(
    state: tauri::State<'_, SharedBridgeState>,
) -> Result<Value, BridgeFailure> {
    send_screen(state.inner().clone(), "confirm_display_layout", json!({})).await
}

#[tauri::command]
pub(crate) async fn bridge_revert_display_layout(
    state: tauri::State<'_, SharedBridgeState>,
) -> Result<Value, BridgeFailure> {
    send_screen(state.inner().clone(), "revert_display_layout", json!({})).await
}

/// Read-only: the screen stream's state (idle, starting, running, stopping or failed).
#[tauri::command]
pub(crate) async fn bridge_get_screen_stream(
    state: tauri::State<'_, SharedBridgeState>,
) -> Result<Value, BridgeFailure> {
    send_screen(state.inner().clone(), "get_screen_stream", json!({})).await
}

/// Brings the window to the front: the window calls it when a tray action cannot run (no TV
/// selected, another request in flight), so the person sees why. Nothing on a phone.
#[tauri::command]
pub(crate) fn show_main_window(app: tauri::AppHandle) {
    #[cfg(desktop)]
    crate::tray::show_main_window(&app);
    #[cfg(not(desktop))]
    let _ = app;
}
