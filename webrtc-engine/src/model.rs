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

/// How the engine encodes H.264. `Software` (`libx264`) is the default. `Vaapi` uses the GPU with
/// constant QP, the only rate control that worked on the measured Intel GPU (D0, #79), so
/// `bitrate_kbps` does not apply to it; when it delivers no picture the source falls back to
/// `libx264` and reports why in the diagnostics.
#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case", tag = "kind")]
pub enum VideoEncoder {
    #[default]
    Software,
    Vaapi {
        render_node: String,
        qp: u8,
    },
}

/// The constant QP D0 measured on the owner's Intel GPU (#79): 1920x1080 at 30 fps, 4.1x real time.
pub const DEFAULT_VAAPI_QP: u8 = 24;

impl VideoEncoder {
    /// VA-API on the first DRM render node under `dri` (normally `/dev/dri`), else `libx264`.
    /// Only the directory is listed: whether the GPU really encodes is learned when the session
    /// starts, which falls back to `libx264` and says why.
    pub fn preferred(dri: &std::path::Path) -> Self {
        let mut nodes: Vec<String> = std::fs::read_dir(dri)
            .into_iter()
            .flatten()
            .filter_map(|entry| entry.ok()?.file_name().into_string().ok())
            .filter(|name| render_node_number(name).is_some())
            .collect();
        nodes.sort();
        match nodes.first() {
            Some(name) => Self::Vaapi {
                render_node: format!("{}/{name}", dri.display()),
                qp: DEFAULT_VAAPI_QP,
            },
            None => Self::Software,
        }
    }

    fn is_valid(&self) -> bool {
        match self {
            Self::Software => true,
            Self::Vaapi { render_node, qp } => {
                (1..=51).contains(qp)
                    && render_node
                        .strip_prefix("/dev/dri/")
                        .and_then(render_node_number)
                        .is_some()
            }
        }
    }
}

/// `renderD128` -> `128`: a DRM render node name, three digits and nothing else.
fn render_node_number(name: &str) -> Option<&str> {
    name.strip_prefix("renderD")
        .filter(|number| number.len() == 3 && number.bytes().all(|byte| byte.is_ascii_digit()))
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct VideoConfig {
    pub width: u32,
    pub height: u32,
    pub fps: u32,
    pub bitrate_kbps: u32,
    #[serde(default)]
    pub encoder: VideoEncoder,
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
    pub source_width: u32,
    pub source_height: u32,
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
        if self.capture.source_width == 0
            || self.capture.source_height == 0
            || self.capture.video.width == 0
            || self.capture.video.height == 0
            || !(1..=120).contains(&self.capture.video.fps)
            || !self.capture.video.encoder.is_valid()
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
    /// Video frames produced by the capture/encoder, whether sent or discarded.
    pub captured_frames: u64,
    pub encoded_frames: u64,
    pub sent_video_frames: u64,
    pub sent_video_bytes: u64,
    pub sent_audio_bytes: u64,
    /// Discarded because the transport was late, counted when the next unit of the track is sent.
    pub dropped_frames: u64,
    pub dropped_audio_packets: u64,
    pub reconnect_attempts: u32,
    /// The H.264 encoder in use, and why it differs from the requested one (VA-API fallback).
    pub video_encoder: Option<String>,
    pub encoder_fallback: Option<String>,
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
                source_width: 1920,
                source_height: 1080,
                video: VideoConfig {
                    width: 1280,
                    height: 720,
                    fps: 30,
                    bitrate_kbps: 4_000,
                    encoder: VideoEncoder::Software,
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
    fn vaapi_is_preferred_only_when_a_render_node_exists() {
        let dri = std::env::temp_dir().join(format!("control-tv-dri-{}", std::process::id()));
        std::fs::create_dir_all(&dri).expect("dri directory");
        assert_eq!(VideoEncoder::preferred(&dri), VideoEncoder::Software);
        for name in [
            "card0",
            "renderD12",
            "renderD1280",
            "renderD129",
            "renderD128",
        ] {
            std::fs::write(dri.join(name), b"").expect("node");
        }
        assert_eq!(
            VideoEncoder::preferred(&dri),
            VideoEncoder::Vaapi {
                render_node: format!("{}/renderD128", dri.display()),
                qp: DEFAULT_VAAPI_QP,
            }
        );
        std::fs::remove_dir_all(&dri).expect("cleanup");
        assert_eq!(VideoEncoder::preferred(&dri), VideoEncoder::Software);
        assert!(VideoEncoder::preferred(std::path::Path::new("/dev/dri")).is_valid());
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
        for (render_node, qp) in [
            ("/dev/dri/card0", 24),
            ("/dev/dri/renderD12", 24),
            ("/dev/dri/renderD128;", 24),
            ("/dev/dri/renderD128", 0),
            ("/dev/dri/renderD128", 52),
        ] {
            invalid.capture.video.encoder = VideoEncoder::Vaapi {
                render_node: render_node.into(),
                qp,
            };
            assert!(matches!(
                invalid.validate(),
                Err(EngineError::InvalidConfiguration)
            ));
        }
        invalid.capture.video.encoder = VideoEncoder::Vaapi {
            render_node: "/dev/dri/renderD128".into(),
            qp: 24,
        };
        assert!(invalid.validate().is_ok());
        invalid.negotiation_timeout = invalid.maximum_duration;
        assert!(matches!(
            invalid.validate(),
            Err(EngineError::InvalidConfiguration)
        ));
    }
}
