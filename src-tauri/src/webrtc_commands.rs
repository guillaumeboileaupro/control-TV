use control_tv_webrtc::{
    AudioConfig, CaptureConfig, EngineState, FfmpegMediaSource, MediaTransport, PairingServer,
    ProcessRunner, SessionConfig, SessionController, SystemClock, VideoConfig, WebRtcTransport,
};
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::net::IpAddr;
use std::sync::{Arc, Mutex as StdMutex};
use std::time::Duration;
use tokio::sync::Mutex;

use crate::bridge::SharedBridgeState;
use crate::commands::{screen_stream_request, send_screen, Placement};

const MAXIMUM_SESSION: Duration = Duration::from_secs(60 * 60);
const NEGOTIATION_TIMEOUT: Duration = Duration::from_secs(30);

#[derive(Clone, Default)]
pub(crate) struct WebRtcState {
    active: Arc<Mutex<ActiveState>>,
    negotiation: Arc<StdMutex<Option<tauri::async_runtime::JoinHandle<()>>>>,
    operation: Arc<Mutex<()>>,
}

#[derive(Default)]
struct ActiveState {
    controller: SessionController,
    transport: Option<Arc<WebRtcTransport>>,
    last_error: Option<String>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct CaptureLease {
    bind_address: String,
    display: String,
    offset_x: u32,
    offset_y: u32,
    source_width: u32,
    source_height: u32,
}

#[derive(Deserialize)]
struct PrepareReply {
    capture: CaptureLease,
}

#[derive(Deserialize)]
struct ReleaseStatus {
    error: Option<String>,
}

#[derive(Deserialize)]
struct ReleaseReply {
    stream: ReleaseStatus,
}

fn capture_config(
    lease: CaptureLease,
    width: u32,
    height: u32,
    fps: u32,
    audio_device: Option<String>,
) -> CaptureConfig {
    CaptureConfig {
        display: lease.display,
        offset_x: lease.offset_x,
        offset_y: lease.offset_y,
        source_width: lease.source_width,
        source_height: lease.source_height,
        video: VideoConfig {
            width,
            height,
            fps,
            bitrate_kbps: 4_000,
        },
        audio: AudioConfig {
            enabled: audio_device.is_some(),
            device: audio_device,
            bitrate_kbps: 128,
        },
    }
}

async fn release_capture(bridge: SharedBridgeState, error: Option<String>) -> Option<String> {
    match send_screen(bridge, "release_webrtc_capture", json!({ "error": error })).await {
        Ok(value) => serde_json::from_value::<ReleaseReply>(value).map_or_else(
            |parse| Some(format!("invalid capture cleanup response: {parse}")),
            |reply| reply.stream.error,
        ),
        Err(failure) => Some(failure.message),
    }
}

async fn release_after_failure(state: &WebRtcState, bridge: SharedBridgeState, detail: String) {
    if let Some(cleanup) = release_capture(bridge, Some(detail)).await {
        state.active.lock().await.last_error = Some(cleanup);
    }
}

async fn release_start_failure(bridge: SharedBridgeState, detail: String) -> String {
    release_capture(bridge, Some(detail.clone()))
        .await
        .unwrap_or(detail)
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct StartReply {
    session_id: String,
    pairing_address: String,
    pairing_token: String,
    pairing_certificate_sha256: String,
    state: EngineState,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct StatusReply {
    state: EngineState,
    diagnostics: control_tv_webrtc::Diagnostics,
    error: Option<String>,
}

#[tauri::command]
#[allow(clippy::too_many_arguments)]
pub(crate) async fn start_webrtc_stream(
    state: tauri::State<'_, WebRtcState>,
    bridge: tauri::State<'_, SharedBridgeState>,
    device_id: String,
    mode: String,
    screen: Option<String>,
    width: u32,
    height: u32,
    fps: u32,
    audio_device: Option<String>,
    consent: bool,
    refresh: Option<u32>,
    position: Option<String>,
) -> Result<StartReply, String> {
    if !consent {
        return Err("explicit screen-sharing consent is required".into());
    }
    let _operation = state.operation.lock().await;
    let placement = Placement {
        refresh: refresh.unwrap_or(60),
        position: position.as_deref().unwrap_or("right"),
    };
    let request = screen_stream_request(
        &device_id,
        &mode,
        screen.as_deref(),
        (width, height),
        fps,
        consent,
        &placement,
    );
    let prepared = send_screen(bridge.inner().clone(), "prepare_webrtc_capture", request)
        .await
        .map_err(|failure| failure.message)?;
    let prepared: PrepareReply = match serde_json::from_value(prepared) {
        Ok(prepared) => prepared,
        Err(error) => {
            let detail = format!("the capture service returned an invalid WebRTC lease: {error}");
            return Err(release_start_failure(bridge.inner().clone(), detail).await);
        }
    };
    let lease = prepared.capture;
    let bind_address: IpAddr = match lease.bind_address.parse() {
        Ok(address) => address,
        Err(_) => {
            let detail = "the capture service returned an invalid bind address".to_owned();
            return Err(release_start_failure(bridge.inner().clone(), detail).await);
        }
    };
    let capture = capture_config(lease, width, height, fps, audio_device);
    let config = SessionConfig {
        bind_address,
        capture: capture.clone(),
        maximum_duration: MAXIMUM_SESSION,
        negotiation_timeout: NEGOTIATION_TIMEOUT,
    };
    let transport = match WebRtcTransport::new().await {
        Ok(transport) => Arc::new(transport),
        Err(error) => {
            let detail = error.to_string();
            return Err(release_start_failure(bridge.inner().clone(), detail).await);
        }
    };
    let offer = match transport.create_offer().await {
        Ok(offer) => offer,
        Err(error) => {
            let detail = error.to_string();
            return Err(release_start_failure(bridge.inner().clone(), detail).await);
        }
    };
    let (server, endpoint) = match PairingServer::bind(bind_address).await {
        Ok(pairing) => pairing,
        Err(error) => {
            let detail = error.to_string();
            return Err(release_start_failure(bridge.inner().clone(), detail).await);
        }
    };
    let mut active = state.active.lock().await;
    if let Err(error) = active.controller.prepare(&config, &SystemClock).await {
        let detail = error.to_string();
        drop(active);
        return Err(release_start_failure(bridge.inner().clone(), detail).await);
    }
    active.last_error = None;
    let session_id = active
        .controller
        .session_id()
        .expect("prepared session id")
        .to_string();
    let token = active
        .controller
        .pairing_token()
        .expect("prepared pairing token")
        .clone();
    let pairing_token = token.expose_for_pairing().to_owned();
    active.transport = Some(Arc::clone(&transport));
    drop(active);

    let state_handle = state.inner().clone();
    let bridge_handle = bridge.inner().clone();
    let task = tauri::async_runtime::spawn(async move {
        let answer = server
            .accept_once(&token, &offer, NEGOTIATION_TIMEOUT)
            .await;
        let mut active = state_handle.active.lock().await;
        match answer {
            Ok(answer) => {
                if let Err(error) = active
                    .controller
                    .negotiate(
                        token.expose_for_pairing(),
                        answer,
                        transport.as_ref(),
                        NEGOTIATION_TIMEOUT,
                    )
                    .await
                {
                    let detail = error.to_string();
                    active.last_error = Some(detail.clone());
                    let _ = active.controller.stop(transport.as_ref()).await;
                    active.transport = None;
                    drop(active);
                    release_after_failure(&state_handle, bridge_handle, detail).await;
                    return;
                }
                let source = Box::new(FfmpegMediaSource::new(ProcessRunner::default(), capture));
                let media_transport: Arc<dyn MediaTransport> =
                    Arc::clone(&transport) as Arc<dyn MediaTransport>;
                if let Err(error) = active.controller.start_media(source, media_transport) {
                    let detail = error.to_string();
                    active.last_error = Some(detail.clone());
                    let _ = active.controller.stop(transport.as_ref()).await;
                    active.transport = None;
                    drop(active);
                    release_after_failure(&state_handle, bridge_handle, detail).await;
                } else {
                    drop(active);
                    if let Err(failure) =
                        send_screen(bridge_handle.clone(), "activate_webrtc_capture", json!({}))
                            .await
                    {
                        let mut active = state_handle.active.lock().await;
                        active.last_error = Some(failure.message.clone());
                        if let Some(transport) = active.transport.take() {
                            let _ = active.controller.stop(transport.as_ref()).await;
                        }
                        drop(active);
                        release_after_failure(&state_handle, bridge_handle, failure.message).await;
                    }
                }
            }
            Err(error) => {
                let detail = error.to_string();
                active.last_error = Some(detail.clone());
                let _ = active.controller.stop(transport.as_ref()).await;
                active.transport = None;
                drop(active);
                release_after_failure(&state_handle, bridge_handle, detail).await;
            }
        }
    });
    *state
        .negotiation
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner) = Some(task);

    Ok(StartReply {
        session_id,
        pairing_address: endpoint.address.to_string(),
        pairing_token,
        pairing_certificate_sha256: endpoint.certificate_sha256,
        state: EngineState::AwaitingReceiver,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn capture_uses_the_leased_source_geometry_and_requested_encoding_size() {
        let capture = capture_config(
            CaptureLease {
                bind_address: "127.0.0.1".into(),
                display: ":7".into(),
                offset_x: 1920,
                offset_y: 24,
                source_width: 2560,
                source_height: 1440,
            },
            1920,
            1080,
            30,
            Some("pulse.monitor".into()),
        );

        assert_eq!(capture.display, ":7");
        assert_eq!((capture.offset_x, capture.offset_y), (1920, 24));
        assert_eq!((capture.source_width, capture.source_height), (2560, 1440));
        assert_eq!((capture.video.width, capture.video.height), (1920, 1080));
        assert!(capture.audio.enabled);
    }

    #[tokio::test]
    async fn lifecycle_operations_are_serialized_across_start_stop_and_status() {
        let state = WebRtcState::default();
        let first = state.operation.lock().await;
        assert!(state.operation.try_lock().is_err());
        drop(first);
        assert!(state.operation.try_lock().is_ok());
    }
}

#[tauri::command]
pub(crate) async fn stop_webrtc_stream(
    state: tauri::State<'_, WebRtcState>,
    bridge: tauri::State<'_, SharedBridgeState>,
) -> Result<StatusReply, String> {
    let _operation = state.operation.lock().await;
    if let Some(task) = state
        .negotiation
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
        .take()
    {
        task.abort();
    }
    let mut active = state.active.lock().await;
    if let Some(transport) = active.transport.take() {
        let result = active.controller.stop(transport.as_ref()).await;
        if let Err(error) = result {
            active.last_error = Some(error.to_string());
        }
    }
    let engine_error = active.last_error.clone();
    drop(active);
    let cleanup_error = release_capture(bridge.inner().clone(), engine_error.clone()).await;
    let active = state.active.lock().await;
    let error = cleanup_error.or(engine_error);
    Ok(StatusReply {
        state: active.controller.state(),
        diagnostics: active.controller.diagnostics().await,
        error,
    })
}

#[tauri::command]
pub(crate) async fn get_webrtc_stream(
    state: tauri::State<'_, WebRtcState>,
    bridge: tauri::State<'_, SharedBridgeState>,
) -> Result<StatusReply, String> {
    let _operation = state.operation.lock().await;
    let mut active = state.active.lock().await;
    if let Some(result) = active.controller.poll_media().await {
        if let Err(error) = result {
            active.last_error = Some(error.to_string());
        }
        if let Some(transport) = active.transport.take() {
            if let Err(error) = transport.close().await {
                active.last_error = Some(error.to_string());
            }
        }
        let error = active.last_error.clone();
        drop(active);
        let cleanup_error = release_capture(bridge.inner().clone(), error).await;
        active = state.active.lock().await;
        active.last_error = cleanup_error.or_else(|| active.last_error.clone());
    }
    Ok(StatusReply {
        state: active.controller.state(),
        diagnostics: active.controller.diagnostics().await,
        error: active.last_error.clone(),
    })
}
