//! Low-latency WebRTC sender used by the Ubuntu control-TV application.
//!
//! The crate deliberately has no Tauri or display-management dependency. The host selects the
//! existing D1/D2 source, obtains explicit consent, and owns the session deadline. This crate
//! owns only bounded media production, WebRTC negotiation and deterministic cleanup.

mod ffmpeg;
mod model;
mod session;
mod signaling;
mod transport;

pub use ffmpeg::{FfmpegMediaSource, ProcessRunner};
pub use model::{
    AudioConfig, CaptureConfig, Diagnostics, EngineError, EngineState, SessionConfig,
    SessionDescription, SessionId, SessionToken, TransportMode, VideoConfig, VideoEncoder,
};
pub use session::{
    Clock, EncoderReport, MediaFrame, MediaSource, MediaStreams, SessionController, SystemClock,
};
pub use signaling::{PairingEndpoint, PairingServer};
pub use transport::{MediaTransport, WebRtcTransport};
