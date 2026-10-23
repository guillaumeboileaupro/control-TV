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
pub trait MediaSource: Send + Sync {
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
    media_task: Option<JoinHandle<MediaTaskResult>>,
    media_stop: Option<oneshot::Sender<()>>,
    unreleased_source: Option<Box<dyn MediaSource>>,
}

type MediaTaskResult = (
    Result<(), EngineError>,
    Result<(), EngineError>,
    Box<dyn MediaSource>,
);

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
            unreleased_source: None,
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
            let result = match source.start().await {
                Ok(streams) => tokio::select! {
                    result = pump_media(streams, transport.as_ref(), diagnostics, deadline) => result,
                    _ = stop_rx => Ok(()),
                },
                Err(error) => Err(error),
            };
            let cleanup = source.stop().await;
            (result, cleanup, source)
        }));
        Ok(())
    }

    pub async fn diagnostics(&self) -> Diagnostics {
        self.diagnostics.lock().await.clone()
    }

    pub async fn poll_media(&mut self) -> Option<Result<(), EngineError>> {
        if !self
            .media_task
            .as_ref()
            .is_some_and(JoinHandle::is_finished)
        {
            return None;
        }
        self.media_stop.take();
        let result = match self.media_task.take().expect("finished media task").await {
            Ok((media, cleanup, source)) => {
                if cleanup.is_err() {
                    self.unreleased_source = Some(source);
                }
                combine_media_and_cleanup(media, cleanup)
            }
            Err(error) => Err(EngineError::Cleanup(error.to_string())),
        };
        let result = match result {
            Ok(())
                if self
                    .deadline
                    .is_some_and(|deadline| Instant::now() >= deadline) =>
            {
                Ok(())
            }
            Ok(()) => Err(EngineError::MediaSource(
                "media pipeline ended unexpectedly".into(),
            )),
            Err(error) => Err(error),
        };
        self.state = if result.is_ok() {
            EngineState::Idle
        } else {
            EngineState::Failed
        };
        self.id = None;
        self.token = None;
        self.deadline = None;
        Some(result)
    }

    pub async fn stop(&mut self, transport: &dyn MediaTransport) -> Result<(), EngineError> {
        if self.state == EngineState::Idle {
            return Ok(());
        }
        self.state = EngineState::Stopping;
        let pending_source_result = if let Some(source) = self.unreleased_source.as_mut() {
            let result = source.stop().await;
            if result.is_ok() {
                self.unreleased_source = None;
            }
            result
        } else {
            Ok(())
        };
        if let Some(stop) = self.media_stop.take() {
            let _ = stop.send(());
        }
        let mut media_result = pending_source_result;
        if let Some(task) = self.media_task.take() {
            let task_result = match task.await {
                Ok((media, cleanup, source)) => {
                    if cleanup.is_err() {
                        self.unreleased_source = Some(source);
                    }
                    combine_media_and_cleanup(media, cleanup)
                }
                Err(error) => Err(EngineError::Cleanup(error.to_string())),
            };
            media_result = combine_media_and_cleanup(media_result, task_result);
        }
        let transport_result = transport.close().await;
        let result = combine_media_and_cleanup(media_result, transport_result);
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

fn combine_media_and_cleanup(
    media: Result<(), EngineError>,
    cleanup: Result<(), EngineError>,
) -> Result<(), EngineError> {
    match (media, cleanup) {
        (Ok(()), cleanup) => cleanup,
        (Err(media), Ok(())) => Err(media),
        (Err(media), Err(cleanup)) => Err(EngineError::Cleanup(format!(
            "{media}; additional cleanup failure: {cleanup}"
        ))),
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
    loop {
        let frame = tokio::select! {
            frame = frames.recv() => frame,
            () = tokio::time::sleep_until(tokio::time::Instant::from_std(deadline)) => None,
        };
        let Some(frame) = frame else {
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
    loop {
        let frame = tokio::select! {
            frame = frames.recv() => frame,
            () = tokio::time::sleep_until(tokio::time::Instant::from_std(deadline)) => None,
        };
        let Some(frame) = frame else {
            break;
        };
        let frame = frame?;
        let bytes = frame.data.len() as u64;
        transport.send_audio(frame.data, frame.duration).await?;
        diagnostics.lock().await.sent_audio_bytes += bytes;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

    struct FakeSource {
        keep_open: bool,
        fail_start: bool,
        fail_stop: bool,
        stopped: Arc<AtomicBool>,
        sender: Option<mpsc::Sender<Result<MediaFrame, EngineError>>>,
    }

    #[async_trait]
    impl MediaSource for FakeSource {
        async fn start(&mut self) -> Result<MediaStreams, EngineError> {
            let (sender, receiver) = mpsc::channel(1);
            if self.fail_start {
                self.sender = Some(sender);
                return Err(EngineError::MediaSource("synthetic start failure".into()));
            }
            if self.keep_open {
                self.sender = Some(sender);
            }
            Ok(MediaStreams {
                video: receiver,
                audio: None,
            })
        }

        async fn stop(&mut self) -> Result<(), EngineError> {
            self.stopped.store(true, Ordering::SeqCst);
            if self.fail_stop {
                Err(EngineError::Cleanup("synthetic stop failure".into()))
            } else {
                Ok(())
            }
        }
    }

    struct RetryCleanupSource {
        stop_calls: Arc<AtomicUsize>,
    }

    #[async_trait]
    impl MediaSource for RetryCleanupSource {
        async fn start(&mut self) -> Result<MediaStreams, EngineError> {
            let (_sender, receiver) = mpsc::channel(1);
            Ok(MediaStreams {
                video: receiver,
                audio: None,
            })
        }

        async fn stop(&mut self) -> Result<(), EngineError> {
            let attempt = self.stop_calls.fetch_add(1, Ordering::SeqCst);
            if attempt == 0 {
                Err(EngineError::Cleanup("first reap failed".into()))
            } else {
                Ok(())
            }
        }
    }

    struct FakeTransport {
        closed: AtomicBool,
        fail_close: bool,
    }

    impl Default for FakeTransport {
        fn default() -> Self {
            Self {
                closed: AtomicBool::new(false),
                fail_close: false,
            }
        }
    }

    #[async_trait]
    impl MediaTransport for FakeTransport {
        async fn create_offer(&self) -> Result<SessionDescription, EngineError> {
            unreachable!()
        }
        async fn accept_answer(&self, _answer: SessionDescription) -> Result<(), EngineError> {
            Ok(())
        }
        async fn send_video(&self, _data: Bytes, _duration: Duration) -> Result<(), EngineError> {
            Ok(())
        }
        async fn send_audio(&self, _data: Bytes, _duration: Duration) -> Result<(), EngineError> {
            Ok(())
        }
        async fn close(&self) -> Result<(), EngineError> {
            self.closed.store(true, Ordering::SeqCst);
            if self.fail_close {
                Err(EngineError::Cleanup("synthetic transport failure".into()))
            } else {
                Ok(())
            }
        }
    }

    fn streaming_controller() -> SessionController {
        SessionController {
            state: EngineState::Streaming,
            id: Some(SessionId::generate()),
            token: Some(SessionToken::generate()),
            deadline: Some(Instant::now() + Duration::from_secs(60)),
            ..SessionController::default()
        }
    }

    #[tokio::test]
    async fn an_unexpected_media_eof_marks_the_session_failed() {
        let stopped = Arc::new(AtomicBool::new(false));
        let transport: Arc<dyn MediaTransport> = Arc::new(FakeTransport::default());
        let mut controller = streaming_controller();
        controller
            .start_media(
                Box::new(FakeSource {
                    keep_open: false,
                    fail_start: false,
                    fail_stop: false,
                    stopped: Arc::clone(&stopped),
                    sender: None,
                }),
                transport,
            )
            .expect("start media");
        let result = tokio::time::timeout(Duration::from_secs(1), async {
            loop {
                if let Some(result) = controller.poll_media().await {
                    return result;
                }
                tokio::task::yield_now().await;
            }
        })
        .await
        .expect("pipeline completion");
        assert!(matches!(result, Err(EngineError::MediaSource(_))));
        assert_eq!(controller.state(), EngineState::Failed);
        assert!(controller.session_id().is_none());
        assert!(stopped.load(Ordering::SeqCst));
    }

    #[tokio::test]
    async fn stop_cancels_media_cooperatively_and_closes_transport() {
        let stopped = Arc::new(AtomicBool::new(false));
        let transport = Arc::new(FakeTransport::default());
        let media_transport: Arc<dyn MediaTransport> = transport.clone();
        let mut controller = streaming_controller();
        controller
            .start_media(
                Box::new(FakeSource {
                    keep_open: true,
                    fail_start: false,
                    fail_stop: false,
                    stopped: Arc::clone(&stopped),
                    sender: None,
                }),
                media_transport,
            )
            .expect("start media");
        controller
            .stop(transport.as_ref())
            .await
            .expect("stop session");
        assert_eq!(controller.state(), EngineState::Idle);
        assert!(stopped.load(Ordering::SeqCst));
        assert!(transport.closed.load(Ordering::SeqCst));
    }

    #[tokio::test]
    async fn stop_preserves_media_and_transport_cleanup_failures() {
        let stopped = Arc::new(AtomicBool::new(false));
        let transport = Arc::new(FakeTransport {
            closed: AtomicBool::new(false),
            fail_close: true,
        });
        let media_transport: Arc<dyn MediaTransport> = transport.clone();
        let mut controller = streaming_controller();
        controller
            .start_media(
                Box::new(FakeSource {
                    keep_open: true,
                    fail_start: false,
                    fail_stop: true,
                    stopped: Arc::clone(&stopped),
                    sender: None,
                }),
                media_transport,
            )
            .expect("start media");

        let error = controller
            .stop(transport.as_ref())
            .await
            .expect_err("both cleanup failures must be reported")
            .to_string();

        assert!(error.contains("synthetic stop failure"));
        assert!(error.contains("synthetic transport failure"));
        assert!(stopped.load(Ordering::SeqCst));
        assert!(transport.closed.load(Ordering::SeqCst));
    }

    #[tokio::test]
    async fn a_partial_start_failure_still_cleans_the_source() {
        let stopped = Arc::new(AtomicBool::new(false));
        let transport = Arc::new(FakeTransport::default());
        let media_transport: Arc<dyn MediaTransport> = transport.clone();
        let mut controller = streaming_controller();
        controller
            .start_media(
                Box::new(FakeSource {
                    keep_open: false,
                    fail_start: true,
                    fail_stop: false,
                    stopped: Arc::clone(&stopped),
                    sender: None,
                }),
                media_transport,
            )
            .expect("start task");
        let result = tokio::time::timeout(Duration::from_secs(1), async {
            loop {
                if let Some(result) = controller.poll_media().await {
                    return result;
                }
                tokio::task::yield_now().await;
            }
        })
        .await
        .expect("start failure reported");
        assert!(matches!(result, Err(EngineError::MediaSource(_))));
        assert!(stopped.load(Ordering::SeqCst));
    }

    #[tokio::test]
    async fn a_start_failure_does_not_mask_a_cleanup_failure() {
        let stopped = Arc::new(AtomicBool::new(false));
        let transport: Arc<dyn MediaTransport> = Arc::new(FakeTransport::default());
        let mut controller = streaming_controller();
        controller
            .start_media(
                Box::new(FakeSource {
                    keep_open: false,
                    fail_start: true,
                    fail_stop: true,
                    stopped: Arc::clone(&stopped),
                    sender: None,
                }),
                transport,
            )
            .expect("start task");
        let result = tokio::time::timeout(Duration::from_secs(1), async {
            loop {
                if let Some(result) = controller.poll_media().await {
                    return result;
                }
                tokio::task::yield_now().await;
            }
        })
        .await
        .expect("combined failure reported")
        .expect_err("start and cleanup must fail");
        assert!(matches!(result, EngineError::Cleanup(_)));
        let message = result.to_string();
        assert!(message.contains("synthetic start failure"));
        assert!(message.contains("synthetic stop failure"));
        assert!(stopped.load(Ordering::SeqCst));
    }

    #[tokio::test]
    async fn a_failed_source_cleanup_remains_tracked_until_stop_retries_it() {
        let stop_calls = Arc::new(AtomicUsize::new(0));
        let transport = Arc::new(FakeTransport::default());
        let media_transport: Arc<dyn MediaTransport> = transport.clone();
        let mut controller = streaming_controller();
        controller
            .start_media(
                Box::new(RetryCleanupSource {
                    stop_calls: Arc::clone(&stop_calls),
                }),
                media_transport,
            )
            .expect("start media");

        let first = tokio::time::timeout(Duration::from_secs(1), async {
            loop {
                if let Some(result) = controller.poll_media().await {
                    return result;
                }
                tokio::task::yield_now().await;
            }
        })
        .await
        .expect("pipeline completion");
        assert!(first.is_err());
        assert_eq!(stop_calls.load(Ordering::SeqCst), 1);

        controller
            .stop(transport.as_ref())
            .await
            .expect("retry cleanup");
        assert_eq!(stop_calls.load(Ordering::SeqCst), 2);
        assert_eq!(controller.state(), EngineState::Idle);
    }

    #[tokio::test]
    async fn a_silent_open_track_stops_at_the_session_deadline() {
        let (_sender, receiver) = mpsc::channel(1);
        let transport = FakeTransport::default();
        let result = pump_video(
            receiver,
            &transport,
            Arc::new(Mutex::new(Diagnostics::default())),
            Instant::now() + Duration::from_millis(10),
        );
        tokio::time::timeout(Duration::from_secs(1), result)
            .await
            .expect("deadline")
            .expect("silent track stops cleanly");
    }
}
