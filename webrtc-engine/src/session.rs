use crate::{
    Diagnostics, EngineError, EngineState, MediaTransport, SessionConfig, SessionDescription,
    SessionId, SessionToken,
};
use async_trait::async_trait;
use bytes::Bytes;
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::sync::Mutex;
use tokio::task::JoinHandle;

#[async_trait]
pub trait MediaSource: Send {
    async fn start(&mut self) -> Result<(), EngineError>;
    async fn next_video_chunk(&mut self) -> Result<Option<Bytes>, EngineError>;
    async fn next_audio_chunk(&mut self) -> Result<Option<Bytes>, EngineError>;
    async fn stop(&mut self) -> Result<(), EngineError>;
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
        self.media_task = Some(tokio::spawn(async move {
            source.start().await?;
            let result = pump_media(&mut *source, transport.as_ref(), diagnostics, deadline).await;
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
        if let Some(task) = self.media_task.take() {
            task.abort();
            let _ = task.await;
        }
        let result = transport.close().await;
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
    source: &mut dyn MediaSource,
    transport: &dyn MediaTransport,
    diagnostics: Arc<Mutex<Diagnostics>>,
    deadline: Instant,
) -> Result<(), EngineError> {
    while Instant::now() < deadline {
        match source.next_video_chunk().await? {
            Some(chunk) => {
                let bytes = chunk.len() as u64;
                transport
                    .send_video(chunk, Duration::from_millis(33))
                    .await?;
                let mut values = diagnostics.lock().await;
                values.captured_frames += 1;
                values.encoded_frames += 1;
                values.sent_video_bytes += bytes;
            }
            None => break,
        }
        if let Some(chunk) = source.next_audio_chunk().await? {
            let bytes = chunk.len() as u64;
            transport
                .send_audio(chunk, Duration::from_millis(20))
                .await?;
            diagnostics.lock().await.sent_audio_bytes += bytes;
        }
    }
    Ok(())
}
