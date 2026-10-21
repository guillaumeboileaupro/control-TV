use crate::{
    Diagnostics, EngineError, EngineState, MediaTransport, SessionConfig, SessionDescription,
    SessionId, SessionToken,
};
use async_trait::async_trait;
use bytes::Bytes;
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::sync::{mpsc, oneshot, Mutex};
use tokio::task::JoinHandle;

#[async_trait]
pub trait MediaSource: Send {
    async fn start(&mut self) -> Result<MediaStreams, EngineError>;
    async fn stop(&mut self) -> Result<(), EngineError>;
}

#[derive(Debug)]
pub struct MediaFrame {
    pub data: Bytes,
    pub duration: Duration,
    pub keyframe: bool,
}

pub struct MediaStreams {
    pub video: mpsc::Receiver<Result<MediaFrame, EngineError>>,
    pub audio: Option<mpsc::Receiver<Result<MediaFrame, EngineError>>>,
}

pub trait Clock: Send + Sync {
    fn now(&self) -> Instant;
}

pub struct SystemClock;
impl Clock for SystemClock {
    fn now(&self) -> Instant {
        Instant::now()
    }
}

pub struct SessionController {
    state: EngineState,
    id: Option<SessionId>,
    token: Option<SessionToken>,
    deadline: Option<Instant>,
    diagnostics: Arc<Mutex<Diagnostics>>,
    media_task: Option<JoinHandle<Result<(), EngineError>>>,
    media_stop: Option<oneshot::Sender<()>>,
}

impl Default for SessionController {
    fn default() -> Self {
        Self {
            state: EngineState::Idle,
            id: None,
            token: None,
            deadline: None,
            diagnostics: Arc::new(Mutex::new(Diagnostics::default())),
            media_task: None,
            media_stop: None,
        }
    }
}

impl SessionController {
    pub fn state(&self) -> EngineState {
        self.state
    }
    pub fn session_id(&self) -> Option<&SessionId> {
        self.id.as_ref()
    }
    pub fn pairing_token(&self) -> Option<&SessionToken> {
        self.token.as_ref()
    }

    pub async fn prepare(
        &mut self,
        config: &SessionConfig,
        clock: &dyn Clock,
    ) -> Result<(), EngineError> {
        config.validate()?;
        if self.state != EngineState::Idle {
            return Err(EngineError::SessionAlreadyActive);
        }
        self.id = Some(SessionId::generate());
        self.token = Some(SessionToken::generate());
        self.deadline = Some(clock.now() + config.maximum_duration);
        *self.diagnostics.lock().await = Diagnostics::default();
        self.state = EngineState::AwaitingReceiver;
        Ok(())
    }

    pub async fn negotiate(
        &mut self,
        candidate_token: &str,
        answer: SessionDescription,
        transport: &dyn MediaTransport,
        timeout: Duration,
    ) -> Result<(), EngineError> {
        let token = self.token.as_ref().ok_or(EngineError::NoActiveSession)?;
        if !token.matches(candidate_token) {
            return Err(EngineError::AuthenticationFailed);
        }
        self.state = EngineState::Negotiating;
        match tokio::time::timeout(timeout, transport.accept_answer(answer)).await {
            Ok(Ok(())) => {
                self.state = EngineState::Streaming;
                Ok(())
            }
            Ok(Err(error)) => {
                self.state = EngineState::Failed;
                Err(error)
            }
            Err(_) => {
                self.state = EngineState::Failed;
                Err(EngineError::NegotiationTimeout)
            }
        }
    }

    pub fn start_media(
        &mut self,
        mut source: Box<dyn MediaSource>,
        transport: Arc<dyn MediaTransport>,
    ) -> Result<(), EngineError> {
        if self.state != EngineState::Streaming || self.media_task.is_some() {
            return Err(EngineError::NoActiveSession);
        }
        let diagnostics = Arc::clone(&self.diagnostics);
        let deadline = self.deadline.ok_or(EngineError::NoActiveSession)?;
        let (stop_tx, stop_rx) = oneshot::channel();
        self.media_stop = Some(stop_tx);
        self.media_task = Some(tokio::spawn(async move {
            let streams = source.start().await?;
            let result = tokio::select! {
                result = pump_media(streams, transport.as_ref(), diagnostics, deadline) => result,
                _ = stop_rx => Ok(()),
            };
            let cleanup = source.stop().await;
            result.and(cleanup)
        }));
        Ok(())
    }

    pub async fn diagnostics(&self) -> Diagnostics {
        self.diagnostics.lock().await.clone()
    }

    pub async fn stop(&mut self, transport: &dyn MediaTransport) -> Result<(), EngineError> {
        if self.state == EngineState::Idle {
            return Ok(());
        }
        self.state = EngineState::Stopping;
        if let Some(stop) = self.media_stop.take() {
            let _ = stop.send(());
        }
        let mut media_result = Ok(());
        if let Some(task) = self.media_task.take() {
            media_result = match task.await {
                Ok(result) => result,
                Err(error) => Err(EngineError::Cleanup(error.to_string())),
            };
        }
        let transport_result = transport.close().await;
        let result = media_result.and(transport_result);
        self.id = None;
        self.token = None;
        self.deadline = None;
        self.state = if result.is_ok() {
            EngineState::Idle
        } else {
            EngineState::Failed
        };
        result
    }
}

async fn pump_media(
    streams: MediaStreams,
    transport: &dyn MediaTransport,
    diagnostics: Arc<Mutex<Diagnostics>>,
    deadline: Instant,
) -> Result<(), EngineError> {
    let video = pump_video(streams.video, transport, Arc::clone(&diagnostics), deadline);
    let audio = pump_audio(streams.audio, transport, diagnostics, deadline);
    tokio::try_join!(video, audio).map(|_| ())
}

async fn pump_video(
    mut frames: mpsc::Receiver<Result<MediaFrame, EngineError>>,
    transport: &dyn MediaTransport,
    diagnostics: Arc<Mutex<Diagnostics>>,
    deadline: Instant,
) -> Result<(), EngineError> {
    while Instant::now() < deadline {
        let Some(frame) = frames.recv().await else {
            break;
        };
        let frame = frame?;
        let bytes = frame.data.len() as u64;
        transport.send_video(frame.data, frame.duration).await?;
        let mut values = diagnostics.lock().await;
        values.captured_frames += 1;
        values.encoded_frames += 1;
        values.sent_video_bytes += bytes;
    }
    Ok(())
}

async fn pump_audio(
    frames: Option<mpsc::Receiver<Result<MediaFrame, EngineError>>>,
    transport: &dyn MediaTransport,
    diagnostics: Arc<Mutex<Diagnostics>>,
    deadline: Instant,
) -> Result<(), EngineError> {
    let Some(mut frames) = frames else {
        return Ok(());
    };
    while Instant::now() < deadline {
        let Some(frame) = frames.recv().await else {
            break;
        };
        let frame = frame?;
        let bytes = frame.data.len() as u64;
        transport.send_audio(frame.data, frame.duration).await?;
        diagnostics.lock().await.sent_audio_bytes += bytes;
    }
    Ok(())
}
