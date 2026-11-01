//! The Android app: Python is embedded in the app process (Chaquopy) and reached through
//! the `ControlBridgePlugin` Kotlin plugin, which runs `control_tv.embedded.handle`.

use super::*;
use serde::{Deserialize, Serialize};
use tauri::plugin::{mobile::PluginInvokeError, PluginHandle, TauriPlugin};

const PLUGIN_PACKAGE: &str = "io.github.guillaumeboileaupro.controltv";
const PLUGIN_CLASS: &str = "ControlBridgePlugin";

#[derive(Serialize)]
struct HandleArgs {
    line: String,
}

#[derive(Deserialize)]
struct HandleResponse {
    line: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct SelectionArgs {
    device_id: String,
    name: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct VoiceArgs {
    device_id: String,
}

/// The registered Kotlin plugin, shared by every launch of the bridge.
pub(super) struct EmbeddedBridge(pub(super) Arc<PluginHandle<tauri::Wry>>);

pub(super) fn plugin() -> TauriPlugin<tauri::Wry> {
    tauri::plugin::Builder::new("control-bridge")
        .setup(|app, api| {
            let handle = api.register_android_plugin(PLUGIN_PACKAGE, PLUGIN_CLASS)?;
            app.manage(EmbeddedBridge(Arc::new(handle)));
            Ok(())
        })
        .build()
}

/// One request line to the embedded bridge, on the calling (worker) thread; the Kotlin
/// side runs Python off the Android main thread and answers once.
pub(super) fn launcher(handle: Arc<PluginHandle<tauri::Wry>>) -> BridgeLauncher {
    Box::new(move || {
        let handle = Arc::clone(&handle);
        Ok(PythonBridge::in_process(Box::new(move |line| {
            handle
                .run_mobile_plugin::<HandleResponse>("handle", HandleArgs { line })
                .map(|response| response.line)
                .map_err(|error| match error {
                    PluginInvokeError::InvokeRejected(rejection) => {
                        embedded_rejection(rejection.code.as_deref(), rejection.message.as_deref())
                    }
                    other => BridgeFailure::transport(other.to_string()),
                })
        })))
    })
}

/// Stores the window's explicit TV choice for the home-screen widget. This is app-private
/// Android state only: it does not cross the Python bridge and sends nothing to a TV.
fn store_selection(
    handle: &PluginHandle<tauri::Wry>,
    device_id: String,
    name: String,
) -> Result<(), String> {
    handle
        .run_mobile_plugin::<serde_json::Value>(
            "rememberSelection",
            SelectionArgs { device_id, name },
        )
        .map(|_| ())
        .map_err(|error| error.to_string())
}

/// Remembers the window's explicit TV choice for the Android widget. The plugin call is
/// blocking, so it runs off the async/main thread. No bridge request or TV command is sent.
#[tauri::command]
pub(crate) async fn remember_selection(
    app: tauri::AppHandle,
    device_id: String,
    name: String,
) -> Result<(), String> {
    let handle = Arc::clone(&app.state::<EmbeddedBridge>().0);
    tauri::async_runtime::spawn_blocking(move || store_selection(&handle, device_id, name))
        .await
        .map_err(|error| error.to_string())?
}

async fn voice_call(
    app: tauri::AppHandle,
    command: &'static str,
    device_id: String,
) -> Result<(), BridgeFailure> {
    let handle = Arc::clone(&app.state::<EmbeddedBridge>().0);
    tauri::async_runtime::spawn_blocking(move || {
        handle
            .run_mobile_plugin::<serde_json::Value>(command, VoiceArgs { device_id })
            .map(|_| ())
            .map_err(|error| match error {
                PluginInvokeError::InvokeRejected(rejection) => BridgeFailure::new(
                    rejection.code.as_deref().unwrap_or("voice_failed"),
                    rejection
                        .message
                        .as_deref()
                        .unwrap_or("voice session failed"),
                ),
                other => BridgeFailure::transport(other.to_string()),
            })
    })
    .await
    .map_err(|error| BridgeFailure::transport(error.to_string()))?
}

pub(crate) async fn start_voice(
    app: tauri::AppHandle,
    device_id: String,
) -> Result<(), BridgeFailure> {
    voice_call(app, "startVoice", device_id).await
}

pub(crate) async fn stop_voice(
    app: tauri::AppHandle,
    device_id: String,
) -> Result<(), BridgeFailure> {
    voice_call(app, "stopVoice", device_id).await
}
