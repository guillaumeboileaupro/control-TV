use crate::{EngineError, SessionDescription};
use async_trait::async_trait;
use bytes::Bytes;
use std::sync::Arc;
use std::time::Duration;
use webrtc::api::interceptor_registry::register_default_interceptors;
use webrtc::api::media_engine::{MediaEngine, MIME_TYPE_H264, MIME_TYPE_OPUS};
use webrtc::api::APIBuilder;
use webrtc::ice_transport::ice_server::RTCIceServer;
use webrtc::interceptor::registry::Registry;
use webrtc::media::Sample;
use webrtc::peer_connection::configuration::RTCConfiguration;
use webrtc::peer_connection::sdp::session_description::RTCSessionDescription;
use webrtc::peer_connection::RTCPeerConnection;
use webrtc::rtp_transceiver::rtp_codec::RTCRtpCodecCapability;
use webrtc::track::track_local::track_local_static_sample::TrackLocalStaticSample;
use webrtc::track::track_local::TrackLocal;

#[async_trait]
pub trait MediaTransport: Send + Sync {
    async fn create_offer(&self) -> Result<SessionDescription, EngineError>;
    async fn accept_answer(&self, answer: SessionDescription) -> Result<(), EngineError>;
    async fn send_video(&self, data: Bytes, duration: Duration) -> Result<(), EngineError>;
    async fn send_audio(&self, data: Bytes, duration: Duration) -> Result<(), EngineError>;
    async fn close(&self) -> Result<(), EngineError>;
}

pub struct WebRtcTransport {
    peer: Arc<RTCPeerConnection>,
    video: Arc<TrackLocalStaticSample>,
    audio: Arc<TrackLocalStaticSample>,
}

impl WebRtcTransport {
    pub async fn new() -> Result<Self, EngineError> {
        let mut media = MediaEngine::default();
        media.register_default_codecs().map_err(transport_error)?;
        let mut registry = Registry::new();
        registry = register_default_interceptors(registry, &mut media).map_err(transport_error)?;
        let api = APIBuilder::new()
            .with_media_engine(media)
            .with_interceptor_registry(registry)
            .build();
        let peer = Arc::new(
            api.new_peer_connection(RTCConfiguration {
                ice_servers: Vec::<RTCIceServer>::new(),
                ..Default::default()
            })
            .await
            .map_err(transport_error)?,
        );
        let video = Arc::new(TrackLocalStaticSample::new(
            RTCRtpCodecCapability {
                mime_type: MIME_TYPE_H264.to_owned(),
                clock_rate: 90_000,
                ..Default::default()
            },
            "video".into(),
            "control-tv".into(),
        ));
        let audio = Arc::new(TrackLocalStaticSample::new(
            RTCRtpCodecCapability {
                mime_type: MIME_TYPE_OPUS.to_owned(),
                clock_rate: 48_000,
                channels: 2,
                ..Default::default()
            },
            "audio".into(),
            "control-tv".into(),
        ));
        drain_rtcp(
            peer.add_track(Arc::clone(&video) as Arc<dyn TrackLocal + Send + Sync>)
                .await
                .map_err(transport_error)?,
        );
        drain_rtcp(
            peer.add_track(Arc::clone(&audio) as Arc<dyn TrackLocal + Send + Sync>)
                .await
                .map_err(transport_error)?,
        );
        Ok(Self { peer, video, audio })
    }
}

fn drain_rtcp(sender: Arc<webrtc::rtp_transceiver::rtp_sender::RTCRtpSender>) {
    tokio::spawn(async move { while sender.read_rtcp().await.is_ok() {} });
}

fn transport_error(error: impl std::fmt::Display) -> EngineError {
    EngineError::Transport(error.to_string())
}

#[async_trait]
impl MediaTransport for WebRtcTransport {
    async fn create_offer(&self) -> Result<SessionDescription, EngineError> {
        let offer = self
            .peer
            .create_offer(None)
            .await
            .map_err(transport_error)?;
        let mut gathering = self.peer.gathering_complete_promise().await;
        self.peer
            .set_local_description(offer)
            .await
            .map_err(transport_error)?;
        let _ = gathering.recv().await;
        let local = self
            .peer
            .local_description()
            .await
            .ok_or_else(|| EngineError::Transport("local SDP unavailable".into()))?;
        Ok(SessionDescription { sdp: local.sdp })
    }

    async fn accept_answer(&self, answer: SessionDescription) -> Result<(), EngineError> {
        let description = RTCSessionDescription::answer(answer.sdp).map_err(transport_error)?;
        self.peer
            .set_remote_description(description)
            .await
            .map_err(transport_error)
    }

    async fn send_video(&self, data: Bytes, duration: Duration) -> Result<(), EngineError> {
        self.video
            .write_sample(&Sample {
                data,
                duration,
                ..Default::default()
            })
            .await
            .map_err(transport_error)
    }

    async fn send_audio(&self, data: Bytes, duration: Duration) -> Result<(), EngineError> {
        self.audio
            .write_sample(&Sample {
                data,
                duration,
                ..Default::default()
            })
            .await
            .map_err(transport_error)
    }

    async fn close(&self) -> Result<(), EngineError> {
        self.peer.close().await.map_err(transport_error)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::sync::mpsc;
    use tokio::time::timeout;
    use webrtc::peer_connection::peer_connection_state::RTCPeerConnectionState;

    async fn receiving_peer() -> Arc<RTCPeerConnection> {
        let mut media = MediaEngine::default();
        media.register_default_codecs().expect("default codecs");
        let registry =
            register_default_interceptors(Registry::new(), &mut media).expect("interceptors");
        Arc::new(
            APIBuilder::new()
                .with_media_engine(media)
                .with_interceptor_registry(registry)
                .build()
                .new_peer_connection(RTCConfiguration::default())
                .await
                .expect("receiving peer"),
        )
    }

    #[tokio::test]
    async fn real_peer_receives_continuous_encrypted_h264_and_opus_rtp() {
        let sender = WebRtcTransport::new().await.expect("sender");
        let receiver = receiving_peer().await;
        let (packet_tx, mut packet_rx) = mpsc::channel(32);
        receiver.on_track(Box::new(move |track, _, _| {
            let packet_tx = packet_tx.clone();
            Box::pin(async move {
                let media = track.codec().capability.mime_type;
                tokio::spawn(async move {
                    for _ in 0..5 {
                        if let Ok((packet, _)) = track.read_rtp().await {
                            if !packet.payload.is_empty() {
                                let _ = packet_tx.send(media.clone()).await;
                            }
                        }
                    }
                });
            })
        }));

        let offer = sender.create_offer().await.expect("offer");
        receiver
            .set_remote_description(RTCSessionDescription::offer(offer.sdp).expect("offer SDP"))
            .await
            .expect("set offer");
        let answer = receiver.create_answer(None).await.expect("answer");
        let mut gathering = receiver.gathering_complete_promise().await;
        receiver
            .set_local_description(answer)
            .await
            .expect("set answer");
        let _ = gathering.recv().await;
        let answer = receiver.local_description().await.expect("local answer");
        sender
            .accept_answer(SessionDescription { sdp: answer.sdp })
            .await
            .expect("accept answer");

        timeout(Duration::from_secs(10), async {
            loop {
                if sender.peer.connection_state() == RTCPeerConnectionState::Connected
                    && receiver.connection_state() == RTCPeerConnectionState::Connected
                {
                    break;
                }
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .expect("peers connected");

        let video = async {
            for sequence in 0..10_u8 {
                let annex_b = Bytes::from(vec![0, 0, 0, 1, 0x65, 0x88, 0x84, sequence]);
                sender
                    .send_video(annex_b, Duration::from_millis(33))
                    .await
                    .expect("send video");
            }
        };
        let audio = async {
            for _ in 0..10 {
                sender
                    .send_audio(
                        Bytes::from_static(&[0xF8, 0xFF, 0xFE]),
                        Duration::from_millis(20),
                    )
                    .await
                    .expect("send audio");
            }
        };
        tokio::join!(video, audio);
        let mut video_packets = 0;
        let mut audio_packets = 0;
        timeout(Duration::from_secs(5), async {
            while video_packets < 5 || audio_packets < 5 {
                match packet_rx.recv().await.as_deref() {
                    Some(MIME_TYPE_H264) => video_packets += 1,
                    Some(MIME_TYPE_OPUS) => audio_packets += 1,
                    _ => {}
                }
            }
        })
        .await
        .expect("concurrent RTP timeout");
        assert_eq!((video_packets, audio_packets), (5, 5));
        sender.close().await.expect("close sender");
        receiver.close().await.expect("close receiver");
    }
}
