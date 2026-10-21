use control_tv_webrtc::{
    AudioConfig, CaptureConfig, EngineState, FfmpegMediaSource, MediaTransport, PairingServer,
    ProcessRunner, SessionConfig, SessionController, SystemClock, VideoConfig, WebRtcTransport,
};
use serde::Serialize;
use std::net::IpAddr;
use std::sync::{Arc, Mutex as StdMutex};
use std::time::Duration;
use tokio::sync::Mutex;

const MAXIMUM_SESSION: Duration = Duration::from_secs(60 * 60);
const NEGOTIATION_TIMEOUT: Duration = Duration::from_secs(30);

#[derive(Clone, Default)]
pub(crate) struct WebRtcState {
    active: Arc<Mutex<ActiveState>>,
    negotiation: Arc<StdMutex<Option<tauri::async_runtime::JoinHandle<()>>>>,
}

#[derive(Default)]
struct ActiveState {
    controller: SessionController,
    transport: Option<Arc<WebRtcTransport>>,
    last_error: Option<String>,
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
    bind_address: String,
    display: String,
    offset_x: u32,
    offset_y: u32,
    width: u32,
    height: u32,
    fps: u32,
    audio_device: Option<String>,
    consent: bool,
) -> Result<StartReply, String> {
    if !consent {
        return Err("explicit screen-sharing consent is required".into());
    }
    let bind_address: IpAddr = bind_address
        .parse()
        .map_err(|_| "invalid bind address".to_owned())?;
    let capture = CaptureConfig {
        display,
        offset_x,
        offset_y,
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
    };
    let config = SessionConfig {
        bind_address,
        capture: capture.clone(),
        maximum_duration: MAXIMUM_SESSION,
        negotiation_timeout: NEGOTIATION_TIMEOUT,
    };
    let transport = Arc::new(
        WebRtcTransport::new()
            .await
            .map_err(|error| error.to_string())?,
    );
    let offer = transport
        .create_offer()
        .await
        .map_err(|error| error.to_string())?;
    let (server, endpoint) = PairingServer::bind(bind_address)
        .await
        .map_err(|error| error.to_string())?;
    let mut active = state.active.lock().await;
    active
        .controller
        .prepare(&config, &SystemClock)
        .await
        .map_err(|error| error.to_string())?;
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
                    active.last_error = Some(error.to_string());
                    let _ = active.controller.stop(transport.as_ref()).await;
                    active.transport = None;
                    return;
                }
                let source = Box::new(FfmpegMediaSource::new(ProcessRunner::default(), capture));
                let media_transport: Arc<dyn MediaTransport> =
                    Arc::clone(&transport) as Arc<dyn MediaTransport>;
                if let Err(error) = active.controller.start_media(source, media_transport) {
                    active.last_error = Some(error.to_string());
                    let _ = active.controller.stop(transport.as_ref()).await;
                    active.transport = None;
                }
            }
            Err(error) => {
                active.last_error = Some(error.to_string());
                let _ = active.controller.stop(transport.as_ref()).await;
                active.transport = None;
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

#[tauri::command]
pub(crate) async fn stop_webrtc_stream(
    state: tauri::State<'_, WebRtcState>,
) -> Result<StatusReply, String> {
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
    Ok(StatusReply {
        state: active.controller.state(),
        diagnostics: active.controller.diagnostics().await,
        error: active.last_error.clone(),
    })
}

#[tauri::command]
pub(crate) async fn get_webrtc_stream(
    state: tauri::State<'_, WebRtcState>,
) -> Result<StatusReply, String> {
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
    }
    Ok(StatusReply {
        state: active.controller.state(),
        diagnostics: active.controller.diagnostics().await,
        error: active.last_error.clone(),
    })
}
