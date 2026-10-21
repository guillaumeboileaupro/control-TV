use rand::distr::{Alphanumeric, SampleString};
use serde::{Deserialize, Serialize};
use std::fmt::{Display, Formatter};
use std::net::IpAddr;
use std::time::Duration;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum TransportMode {
    Auto,
    WebRtc,
    Hls,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SessionId(String);

impl SessionId {
    pub fn generate() -> Self {
        Self(Alphanumeric.sample_string(&mut rand::rng(), 20))
    }
}

impl Display for SessionId {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.0)
    }
}

#[derive(Clone, Eq, PartialEq)]
pub struct SessionToken(String);

impl std::fmt::Debug for SessionToken {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("SessionToken([redacted])")
    }
}

impl SessionToken {
    pub fn generate() -> Self {
        Self(Alphanumeric.sample_string(&mut rand::rng(), 48))
    }

    pub fn expose_for_pairing(&self) -> &str {
        &self.0
    }

    pub fn matches(&self, candidate: &str) -> bool {
        let left = self.0.as_bytes();
        let right = candidate.as_bytes();
        let mut difference = left.len() ^ right.len();
        for index in 0..left.len().max(right.len()) {
            difference |= usize::from(
                left.get(index).copied().unwrap_or_default()
                    ^ right.get(index).copied().unwrap_or_default(),
            );
        }
        difference == 0
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct VideoConfig {
    pub width: u32,
    pub height: u32,
    pub fps: u32,
    pub bitrate_kbps: u32,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct AudioConfig {
    pub enabled: bool,
    pub device: Option<String>,
    pub bitrate_kbps: u32,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CaptureConfig {
    pub display: String,
    pub offset_x: u32,
    pub offset_y: u32,
    pub video: VideoConfig,
    pub audio: AudioConfig,
}

#[derive(Clone, Debug)]
pub struct SessionConfig {
    pub bind_address: IpAddr,
    pub capture: CaptureConfig,
    pub maximum_duration: Duration,
    pub negotiation_timeout: Duration,
}

impl SessionConfig {
    pub fn validate(&self) -> Result<(), EngineError> {
        if self.bind_address.is_unspecified() {
            return Err(EngineError::UnsafeBindAddress);
        }
        if self.capture.video.width == 0
            || self.capture.video.height == 0
            || !(1..=120).contains(&self.capture.video.fps)
            || self.maximum_duration.is_zero()
            || self.negotiation_timeout.is_zero()
            || self.negotiation_timeout >= self.maximum_duration
        {
            return Err(EngineError::InvalidConfiguration);
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SessionDescription {
    pub sdp: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum EngineState {
    Idle,
    AwaitingReceiver,
    Negotiating,
    Streaming,
    Stopping,
    Failed,
}

#[derive(Clone, Debug, Default, PartialEq, Serialize)]
pub struct Diagnostics {
    pub captured_frames: u64,
    pub encoded_frames: u64,
    pub sent_video_bytes: u64,
    pub sent_audio_bytes: u64,
    pub dropped_frames: u64,
    pub reconnect_attempts: u32,
}

#[derive(Debug, thiserror::Error)]
pub enum EngineError {
    #[error("a streaming session is already active")]
    SessionAlreadyActive,
    #[error("no streaming session is active")]
    NoActiveSession,
    #[error("the session configuration is invalid")]
    InvalidConfiguration,
    #[error("refusing to listen on an unspecified network interface")]
    UnsafeBindAddress,
    #[error("receiver authentication failed")]
    AuthenticationFailed,
    #[error("receiver negotiation timed out")]
    NegotiationTimeout,
    #[error("media source failed: {0}")]
    MediaSource(String),
    #[error("WebRTC transport failed: {0}")]
    Transport(String),
    #[error("cleanup failed: {0}")]
    Cleanup(String),
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::{IpAddr, Ipv4Addr};

    fn config(address: IpAddr) -> SessionConfig {
        SessionConfig {
            bind_address: address,
            capture: CaptureConfig {
                display: ":0.0".into(),
                offset_x: 0,
                offset_y: 0,
                video: VideoConfig {
                    width: 1280,
                    height: 720,
                    fps: 30,
                    bitrate_kbps: 4_000,
                },
                audio: AudioConfig {
                    enabled: true,
                    device: Some("monitor".into()),
                    bitrate_kbps: 128,
                },
            },
            maximum_duration: Duration::from_secs(60),
            negotiation_timeout: Duration::from_secs(5),
        }
    }

    #[test]
    fn token_is_long_unique_and_matchable_without_debug_exposure() {
        let first = SessionToken::generate();
        let second = SessionToken::generate();
        assert_eq!(first.expose_for_pairing().len(), 48);
        assert_ne!(first, second);
        assert!(first.matches(first.expose_for_pairing()));
        assert!(!first.matches("wrong"));
        assert!(!format!("{first:?}").contains(first.expose_for_pairing()));
    }

    #[test]
    fn config_refuses_wildcard_and_invalid_bounds() {
        assert!(matches!(
            config(IpAddr::V4(Ipv4Addr::UNSPECIFIED)).validate(),
            Err(EngineError::UnsafeBindAddress)
        ));
        let mut invalid = config(IpAddr::V4(Ipv4Addr::LOCALHOST));
        invalid.capture.video.fps = 0;
        assert!(matches!(
            invalid.validate(),
            Err(EngineError::InvalidConfiguration)
        ));
        invalid.capture.video.fps = 30;
        invalid.negotiation_timeout = invalid.maximum_duration;
        assert!(matches!(
            invalid.validate(),
            Err(EngineError::InvalidConfiguration)
        ));
    }
}
