use crate::{EngineError, MediaFrame, SessionDescription};
use async_trait::async_trait;
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::watch;
use webrtc::api::interceptor_registry::register_default_interceptors;
use webrtc::api::media_engine::{MediaEngine, MIME_TYPE_H264, MIME_TYPE_OPUS};
use webrtc::api::APIBuilder;
use webrtc::ice_transport::ice_server::RTCIceServer;
use webrtc::interceptor::registry::Registry;
use webrtc::media::Sample;
use webrtc::peer_connection::configuration::RTCConfiguration;
use webrtc::peer_connection::peer_connection_state::RTCPeerConnectionState;
use webrtc::peer_connection::sdp::session_description::RTCSessionDescription;
use webrtc::peer_connection::RTCPeerConnection;
use webrtc::rtp_transceiver::rtp_codec::RTCRtpCodecCapability;
use webrtc::track::track_local::track_local_static_sample::TrackLocalStaticSample;
use webrtc::track::track_local::TrackLocal;

#[async_trait]
pub trait MediaTransport: Send + Sync {
    async fn create_offer(&self) -> Result<SessionDescription, EngineError>;
    async fn accept_answer(&self, answer: SessionDescription) -> Result<(), EngineError>;
    async fn send_video(&self, frame: MediaFrame) -> Result<(), EngineError>;
    async fn send_audio(&self, frame: MediaFrame) -> Result<(), EngineError>;
    async fn close(&self) -> Result<(), EngineError>;
    async fn wait_for_disconnect(&self) -> Result<(), EngineError> {
        std::future::pending().await
    }
}

pub struct WebRtcTransport {
    peer: Arc<RTCPeerConnection>,
    video: Arc<TrackLocalStaticSample>,
    audio: Arc<TrackLocalStaticSample>,
    connection_state: watch::Receiver<RTCPeerConnectionState>,
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
        let (connection_state_tx, connection_state) = watch::channel(peer.connection_state());
        peer.on_peer_connection_state_change(Box::new(move |state| {
            let connection_state_tx = connection_state_tx.clone();
            Box::pin(async move {
                let _ = connection_state_tx.send(state);
            })
        }));
        Ok(Self {
            peer,
            video,
            audio,
            connection_state,
        })
    }
}

fn drain_rtcp(sender: Arc<webrtc::rtp_transceiver::rtp_sender::RTCRtpSender>) {
    tokio::spawn(async move { while sender.read_rtcp().await.is_ok() {} });
}

fn transport_error(error: impl std::fmt::Display) -> EngineError {
    EngineError::Transport(error.to_string())
}

fn sample(frame: MediaFrame) -> Sample {
    Sample {
        data: frame.data,
        duration: frame.duration,
        prev_dropped_packets: frame.dropped_before,
        ..Default::default()
    }
}

async fn wait_for_connection_loss(
    mut states: watch::Receiver<RTCPeerConnectionState>,
    disconnect_grace: Duration,
) -> Result<(), EngineError> {
    loop {
        let state = { *states.borrow_and_update() };
        match state {
            RTCPeerConnectionState::Failed | RTCPeerConnectionState::Closed => {
                return Err(EngineError::Transport("WebRTC connection was lost".into()));
            }
            RTCPeerConnectionState::Disconnected => {
                match tokio::time::timeout(disconnect_grace, states.changed()).await {
                    Ok(Ok(())) => continue,
                    Ok(Err(_)) | Err(_) => {
                        return Err(EngineError::Transport(
                            "WebRTC connection remained disconnected".into(),
                        ));
                    }
                }
            }
            _ => {}
        }
        states.changed().await.map_err(|_| {
            EngineError::Transport("WebRTC connection state monitor stopped".into())
        })?;
    }
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

    async fn send_video(&self, frame: MediaFrame) -> Result<(), EngineError> {
        self.video
            .write_sample(&sample(frame))
            .await
            .map_err(transport_error)
    }

    async fn send_audio(&self, frame: MediaFrame) -> Result<(), EngineError> {
        self.audio
            .write_sample(&sample(frame))
            .await
            .map_err(transport_error)
    }

    async fn close(&self) -> Result<(), EngineError> {
        self.peer.close().await.map_err(transport_error)
    }

    async fn wait_for_disconnect(&self) -> Result<(), EngineError> {
        wait_for_connection_loss(self.connection_state.clone(), Duration::from_secs(3)).await
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use bytes::Bytes;
    use rtp::codecs::h264::H264Packet;
    use rtp::codecs::opus::OpusPacket;
    use std::io::Write;
    use std::process::{Command, Stdio};
    use tokio::sync::mpsc;
    use tokio::time::timeout;
    use webrtc::peer_connection::peer_connection_state::RTCPeerConnectionState;
    use webrtc_media::io::sample_builder::SampleBuilder;

    fn frame(data: Bytes, duration: Duration) -> MediaFrame {
        MediaFrame {
            data,
            duration,
            keyframe: false,
            dropped_before: 0,
        }
    }

    #[test]
    fn units_discarded_before_a_sample_skip_its_rtp_time_and_sequence_numbers() {
        let mut late = frame(Bytes::from_static(b"next"), Duration::from_millis(20));
        late.dropped_before = 3;
        let converted = sample(late);
        assert_eq!(converted.prev_dropped_packets, 3);
        assert_eq!(converted.duration, Duration::from_millis(20));
        assert_eq!(converted.data, Bytes::from_static(b"next"));
    }

    #[tokio::test]
    async fn a_transient_disconnect_that_recovers_does_not_restart_the_session() {
        let (state_tx, state_rx) = watch::channel(RTCPeerConnectionState::Connected);
        let monitor = tokio::spawn(wait_for_connection_loss(
            state_rx,
            Duration::from_millis(100),
        ));
        state_tx
            .send(RTCPeerConnectionState::Disconnected)
            .expect("disconnect");
        tokio::time::sleep(Duration::from_millis(10)).await;
        state_tx
            .send(RTCPeerConnectionState::Connected)
            .expect("reconnect");
        tokio::time::sleep(Duration::from_millis(120)).await;
        assert!(
            !monitor.is_finished(),
            "a recovered connection stays active"
        );

        state_tx
            .send(RTCPeerConnectionState::Failed)
            .expect("terminal failure");
        assert!(monitor.await.expect("monitor task").is_err());
    }

    #[tokio::test]
    async fn a_disconnect_beyond_the_grace_period_is_terminal() {
        let (state_tx, state_rx) = watch::channel(RTCPeerConnectionState::Connected);
        let monitor = tokio::spawn(wait_for_connection_loss(
            state_rx,
            Duration::from_millis(10),
        ));
        state_tx
            .send(RTCPeerConnectionState::Disconnected)
            .expect("disconnect");
        let error = monitor.await.expect("monitor task").expect_err("lost peer");
        assert!(error.to_string().contains("remained disconnected"));
    }

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

    fn encoded_fixture(arguments: &[&str]) -> Bytes {
        let output = Command::new("ffmpeg")
            .args(["-hide_banner", "-loglevel", "error"])
            .args(arguments)
            .output()
            .expect("run ffmpeg fixture encoder");
        assert!(output.status.success(), "fixture encoder failed");
        Bytes::from(output.stdout)
    }

    fn assert_ffmpeg_decodes(format: &str, encoded: &[u8]) {
        let mut decoder = Command::new("ffmpeg")
            .args([
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                format,
                "-i",
                "pipe:0",
                "-f",
                "null",
                "-",
            ])
            .stdin(Stdio::piped())
            .stdout(Stdio::null())
            .stderr(Stdio::piped())
            .spawn()
            .expect("start ffmpeg decoder");
        decoder
            .stdin
            .take()
            .expect("decoder stdin")
            .write_all(encoded)
            .expect("write encoded media");
        let output = decoder.wait_with_output().expect("wait for decoder");
        assert!(
            output.status.success(),
            "{format} decoder rejected received stream: {}",
            String::from_utf8_lossy(&output.stderr)
        );
    }

    fn ogg_packets(stream: &Bytes) -> Vec<Bytes> {
        let mut packets = Vec::new();
        let mut packet = Vec::new();
        let mut offset = 0;
        while offset < stream.len() {
            assert_eq!(&stream[offset..offset + 4], b"OggS");
            let segment_count = usize::from(stream[offset + 26]);
            let lacing = &stream[offset + 27..offset + 27 + segment_count];
            let body_size: usize = lacing.iter().map(|size| usize::from(*size)).sum();
            let page_size = 27 + segment_count + body_size;
            let mut body_offset = offset + 27 + segment_count;
            for size in lacing {
                let end = body_offset + usize::from(*size);
                packet.extend_from_slice(&stream[body_offset..end]);
                body_offset = end;
                if *size < 255 {
                    packets.push(Bytes::from(std::mem::take(&mut packet)));
                }
            }
            offset += page_size;
        }
        assert!(packet.is_empty(), "fixture ends on an Opus packet boundary");
        packets
    }

    fn ogg_crc(page: &[u8]) -> u32 {
        page.iter().fold(0_u32, |mut crc, byte| {
            crc ^= u32::from(*byte) << 24;
            for _ in 0..8 {
                crc = if crc & 0x8000_0000 != 0 {
                    (crc << 1) ^ 0x04c1_1db7
                } else {
                    crc << 1
                };
            }
            crc
        })
    }

    fn ogg_page(packet: &[u8], sequence: u32, granule: u64, header_type: u8) -> Vec<u8> {
        let mut lacing = vec![255_u8; packet.len() / 255];
        lacing.push(u8::try_from(packet.len() % 255).expect("lacing remainder"));
        let mut page = b"OggS\0".to_vec();
        page.push(header_type);
        page.extend_from_slice(&granule.to_le_bytes());
        page.extend_from_slice(&0x4354_5631_u32.to_le_bytes());
        page.extend_from_slice(&sequence.to_le_bytes());
        page.extend_from_slice(&[0; 4]);
        page.push(u8::try_from(lacing.len()).expect("lacing count"));
        page.extend_from_slice(&lacing);
        page.extend_from_slice(packet);
        let checksum = ogg_crc(&page).to_le_bytes();
        page[22..26].copy_from_slice(&checksum);
        page
    }

    fn mux_received_opus(headers: &[Bytes], packets: &[Bytes]) -> Vec<u8> {
        assert_eq!(headers.len(), 2);
        let mut stream = Vec::new();
        stream.extend(ogg_page(&headers[0], 0, 0, 2));
        stream.extend(ogg_page(&headers[1], 1, 0, 0));
        for (index, packet) in packets.iter().enumerate() {
            let last = index + 1 == packets.len();
            stream.extend(ogg_page(
                packet,
                u32::try_from(index + 2).expect("page sequence"),
                u64::try_from(index + 1).expect("granule") * 960,
                if last { 4 } else { 0 },
            ));
        }
        stream
    }

    #[tokio::test]
    async fn real_peer_receives_and_decodes_continuous_h264_and_opus() {
        assert!(
            Command::new("ffmpeg")
                .arg("-version")
                .output()
                .is_ok_and(|output| output.status.success()),
            "this decoding test requires ffmpeg"
        );
        let video_fixture = encoded_fixture(&[
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=64x64:rate=10",
            "-t",
            "1",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-tune",
            "zerolatency",
            "-profile:v",
            "baseline",
            "-pix_fmt",
            "yuv420p",
            "-f",
            "h264",
            "pipe:1",
        ]);
        let audio_fixture = encoded_fixture(&[
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000",
            "-t",
            "1",
            "-c:a",
            "libopus",
            "-page_duration",
            "20000",
            "-f",
            "opus",
            "pipe:1",
        ]);
        let mut encoded_opus = ogg_packets(&audio_fixture);
        assert!(encoded_opus.len() > 2, "headers plus audio packets");
        let opus_headers: Vec<Bytes> = encoded_opus.drain(..2).collect();
        let sender = WebRtcTransport::new().await.expect("sender");
        let receiver = receiving_peer().await;
        let (video_tx, mut video_rx) = mpsc::channel(4);
        let (audio_tx, mut audio_rx) = mpsc::channel(4);
        receiver.on_track(Box::new(move |track, _, _| {
            let video_tx = video_tx.clone();
            let audio_tx = audio_tx.clone();
            Box::pin(async move {
                let media = track.codec().capability.mime_type;
                tokio::spawn(async move {
                    if media == MIME_TYPE_H264 {
                        let mut builder = SampleBuilder::new(64, H264Packet::default(), 90_000);
                        while let Ok((packet, _)) = track.read_rtp().await {
                            builder.push(packet);
                            while let Some(sample) = builder.pop() {
                                if video_tx.send(sample.data).await.is_err() {
                                    return;
                                }
                            }
                        }
                    } else if media == MIME_TYPE_OPUS {
                        let mut builder = SampleBuilder::new(64, OpusPacket, 48_000);
                        while let Ok((packet, _)) = track.read_rtp().await {
                            builder.push(packet);
                            while let Some(sample) = builder.pop() {
                                if audio_tx.send(sample.data).await.is_err() {
                                    return;
                                }
                            }
                        }
                    };
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
            for _ in 0..3 {
                sender
                    .send_video(frame(video_fixture.clone(), Duration::from_secs(1)))
                    .await
                    .expect("send video");
            }
        };
        let audio = async {
            for packet in encoded_opus.iter().chain(encoded_opus.first()) {
                sender
                    .send_audio(frame(packet.clone(), Duration::from_millis(20)))
                    .await
                    .expect("send audio");
            }
        };
        tokio::join!(video, audio);
        let received_video = timeout(Duration::from_secs(5), async {
            let mut stream = Vec::new();
            for _ in 0..2 {
                stream.extend_from_slice(&video_rx.recv().await.expect("video sample"));
            }
            stream
        })
        .await
        .expect("video receive timeout");
        let received_opus = timeout(Duration::from_secs(5), async {
            let mut packets = Vec::new();
            for _ in 0..encoded_opus.len() {
                packets.push(audio_rx.recv().await.expect("audio sample"));
            }
            packets
        })
        .await
        .expect("audio receive timeout");
        assert_eq!(
            received_opus, encoded_opus,
            "Opus payloads survive SRTP/RTP"
        );
        let received_audio = mux_received_opus(&opus_headers, &received_opus);
        assert_ffmpeg_decodes("h264", &received_video);
        assert_ffmpeg_decodes("ogg", &received_audio);
        sender.close().await.expect("close sender");
        receiver.close().await.expect("close receiver");
    }
}
