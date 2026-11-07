use crate::{EngineError, MediaFrame, SessionDescription};
use async_trait::async_trait;
use rtc::interceptor::Registry;
use rtc::media::Sample;
use rtc::media_stream::MediaStreamTrack;
use rtc::peer_connection::configuration::interceptor_registry::register_default_interceptors;
use rtc::peer_connection::configuration::media_engine::{
    MediaEngine, MIME_TYPE_H264, MIME_TYPE_OPUS,
};
use rtc::peer_connection::configuration::RTCConfigurationBuilder;
use rtc::peer_connection::sdp::RTCSessionDescription;
use rtc::rtp_transceiver::rtp_sender::{
    RTCRtpCodec, RTCRtpCodingParameters, RTCRtpEncodingParameters, RtpCodecKind,
};
use rtc::rtp_transceiver::PayloadType;
use std::sync::{Arc, OnceLock};
use std::time::{Duration, Instant};
use tokio::sync::watch;
use webrtc::media_stream::track_local::static_sample::TrackLocalStaticSample;
use webrtc::media_stream::track_local::TrackLocal;
use webrtc::peer_connection::{
    PeerConnection, PeerConnectionBuilder, PeerConnectionEventHandler, RTCIceGatheringState,
    RTCPeerConnectionState,
};
use webrtc::rtp_transceiver::RtpSender;

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
    peer: Arc<dyn PeerConnection>,
    video: Outbound,
    audio: Outbound,
    connection_state: watch::Receiver<RTCPeerConnectionState>,
    gathered: watch::Receiver<bool>,
}

/// One sending track: its SSRC, and the payload type the receiver's answer chose for it.
struct Outbound {
    track: Arc<TrackLocalStaticSample>,
    sender: Arc<dyn RtpSender>,
    ssrc: u32,
    payload_type: OnceLock<PayloadType>,
}

/// Mirrors the peer's connection state and the end of ICE gathering (no trickle ICE).
struct PeerEvents {
    connection: watch::Sender<RTCPeerConnectionState>,
    gathered: watch::Sender<bool>,
}

#[async_trait]
impl PeerConnectionEventHandler for PeerEvents {
    async fn on_ice_gathering_state_change(&self, state: RTCIceGatheringState) {
        if state == RTCIceGatheringState::Complete {
            let _ = self.gathered.send(true);
        }
    }

    async fn on_connection_state_change(&self, state: RTCPeerConnectionState) {
        let _ = self.connection.send(state);
    }
}

impl WebRtcTransport {
    pub async fn new() -> Result<Self, EngineError> {
        let mut media = MediaEngine::default();
        media.register_default_codecs().map_err(transport_error)?;
        let registry =
            register_default_interceptors(Registry::new(), &mut media).map_err(transport_error)?;
        let (connection, connection_state) = watch::channel(RTCPeerConnectionState::New);
        let (gathered_tx, gathered) = watch::channel(false);
        let peer: Arc<dyn PeerConnection> = Arc::new(
            PeerConnectionBuilder::new()
                .with_configuration(RTCConfigurationBuilder::new().build())
                .with_media_engine(media)
                .with_interceptor_registry(registry)
                .with_handler(Arc::new(PeerEvents {
                    connection,
                    gathered: gathered_tx,
                }))
                // Every local interface, loopback and link-local excluded, as 0.14 gathered them.
                .with_udp_addrs(vec!["0.0.0.0:0"])
                .build()
                .await
                .map_err(transport_error)?,
        );
        let video = outbound(
            &peer,
            RtpCodecKind::Video,
            "video",
            RTCRtpCodec {
                mime_type: MIME_TYPE_H264.to_owned(),
                clock_rate: 90_000,
                sdp_fmtp_line: H264_CONSTRAINED_BASELINE.to_owned(),
                ..Default::default()
            },
        )
        .await?;
        let audio = outbound(
            &peer,
            RtpCodecKind::Audio,
            "audio",
            RTCRtpCodec {
                mime_type: MIME_TYPE_OPUS.to_owned(),
                clock_rate: 48_000,
                channels: 2,
                ..Default::default()
            },
        )
        .await?;
        Ok(Self {
            peer,
            video,
            audio,
            connection_state,
            gathered,
        })
    }
}

/// H.264 as both encoders produce it (libx264 baseline, VA-API constrained baseline). 0.21 offers
/// only the codec a track matches; without this line that was Baseline (42001f), which libwebrtc
/// does not treat as Constrained Baseline: the TV rejected the video and its answer then carried a
/// second set of ICE credentials, refused as "conflicting ice-ufrag values" (seen on 2026-11-07).
const H264_CONSTRAINED_BASELINE: &str =
    "level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=42e01f";

/// Adds one sending track, matched to the default codec of its type that fits it.
async fn outbound(
    peer: &Arc<dyn PeerConnection>,
    kind: RtpCodecKind,
    id: &str,
    codec: RTCRtpCodec,
) -> Result<Outbound, EngineError> {
    let ssrc = rand::random::<u32>();
    let track = Arc::new(
        TrackLocalStaticSample::new(
            Instant::now(),
            MediaStreamTrack::new(
                "control-tv".into(),
                id.into(),
                id.into(),
                kind,
                vec![RTCRtpEncodingParameters {
                    rtp_coding_parameters: RTCRtpCodingParameters {
                        ssrc: Some(ssrc),
                        ..Default::default()
                    },
                    active: true,
                    codec,
                    ..Default::default()
                }],
            ),
        )
        .map_err(transport_error)?,
    );
    let sender = peer
        .add_track(Arc::clone(&track) as Arc<dyn TrackLocal>)
        .await
        .map_err(transport_error)?;
    Ok(Outbound {
        track,
        sender,
        ssrc,
        payload_type: OnceLock::new(),
    })
}

/// The payload type the answer negotiated for this track; every packet carries it.
async fn negotiated(outbound: &Outbound, kind: &str) -> Result<(), EngineError> {
    let payload_type = outbound
        .sender
        .get_parameters()
        .await
        .map_err(transport_error)?
        .rtp_parameters
        .codecs
        .first()
        .map(|codec| codec.payload_type)
        .ok_or_else(|| EngineError::Transport(format!("the TV accepted no {kind} codec")))?;
    let _ = outbound.payload_type.set(payload_type);
    Ok(())
}

async fn send(outbound: &Outbound, frame: MediaFrame) -> Result<(), EngineError> {
    let payload_type = *outbound
        .payload_type
        .get()
        .ok_or_else(|| EngineError::Transport("media sent before the answer".into()))?;
    outbound
        .track
        .sample_writer(outbound.ssrc, payload_type)
        .write_sample(&sample(frame))
        .await
        .map_err(transport_error)
}

fn transport_error(error: impl std::fmt::Display) -> EngineError {
    EngineError::Transport(error.to_string())
}

fn sample(frame: MediaFrame) -> Sample {
    Sample {
        data: frame.data,
        duration: frame.duration,
        prev_dropped_packets: frame.dropped_before,
        ..Sample::new(Instant::now())
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
        self.peer
            .set_local_description(offer)
            .await
            .map_err(transport_error)?;
        self.gathered
            .clone()
            .wait_for(|done| *done)
            .await
            .map_err(|_| EngineError::Transport("ICE gathering monitor stopped".into()))?;
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
            .map_err(transport_error)?;
        negotiated(&self.video, "video").await?;
        negotiated(&self.audio, "audio").await
    }

    async fn send_video(&self, frame: MediaFrame) -> Result<(), EngineError> {
        send(&self.video, frame).await
    }

    async fn send_audio(&self, frame: MediaFrame) -> Result<(), EngineError> {
        send(&self.audio, frame).await
    }

    async fn close(&self) -> Result<(), EngineError> {
        self.peer.close().await.map_err(transport_error)
    }

    async fn wait_for_disconnect(&self) -> Result<(), EngineError> {
        wait_for_connection_loss(self.connection_state.clone(), Duration::from_secs(3)).await
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use bytes::Bytes;
    use rtc::media::io::sample_builder::SampleBuilder;
    use rtc::rtp::codec::h264::H264Packet;
    use rtc::rtp::codec::opus::OpusPacket;
    use std::io::Write;
    use std::process::{Command, Stdio};
    use tokio::sync::mpsc;
    use tokio::time::timeout;
    use webrtc::media_stream::track_remote::{TrackRemote, TrackRemoteEvent};

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

    /// A receiving peer standing in for the TV: it answers an offer and forwards each
    /// depacketized sample as `(is_video, payload)`.
    pub(crate) struct ReceivingPeer {
        peer: Arc<dyn PeerConnection>,
        connection: watch::Receiver<RTCPeerConnectionState>,
        gathered: watch::Receiver<bool>,
    }

    struct ReceiverEvents {
        events: PeerEvents,
        samples: mpsc::Sender<(bool, Bytes)>,
    }

    #[async_trait]
    impl PeerConnectionEventHandler for ReceiverEvents {
        async fn on_ice_gathering_state_change(&self, state: RTCIceGatheringState) {
            self.events.on_ice_gathering_state_change(state).await;
        }

        async fn on_connection_state_change(&self, state: RTCPeerConnectionState) {
            self.events.on_connection_state_change(state).await;
        }

        async fn on_track(&self, track: Arc<dyn TrackRemote>) {
            let samples = self.samples.clone();
            let video = track.kind().await == RtpCodecKind::Video;
            tokio::spawn(async move {
                if video {
                    let builder = SampleBuilder::new(64, H264Packet::default(), 90_000);
                    forward(track, builder, true, samples).await;
                } else {
                    forward(
                        track,
                        SampleBuilder::new(64, OpusPacket, 48_000),
                        false,
                        samples,
                    )
                    .await;
                }
            });
        }
    }

    async fn forward<T: rtc::rtp::packetizer::Depacketizer>(
        track: Arc<dyn TrackRemote>,
        mut builder: SampleBuilder<T>,
        video: bool,
        samples: mpsc::Sender<(bool, Bytes)>,
    ) {
        while let Some(event) = track.poll().await {
            if let TrackRemoteEvent::OnRtpPacket(packet) = event {
                builder.push(Instant::now(), packet);
                while let Some(sample) = builder.pop(Instant::now()) {
                    if samples.send((video, sample.data)).await.is_err() {
                        return;
                    }
                }
            }
        }
    }

    impl ReceivingPeer {
        /// Sets the offer and returns the complete answer SDP (all candidates gathered).
        pub(crate) async fn answer(&self, offer: String) -> String {
            self.peer
                .set_remote_description(RTCSessionDescription::offer(offer).expect("offer SDP"))
                .await
                .expect("set offer");
            let answer = self.peer.create_answer(None).await.expect("answer");
            self.peer
                .set_local_description(answer)
                .await
                .expect("set answer");
            self.gathered
                .clone()
                .wait_for(|done| *done)
                .await
                .expect("gathering");
            self.peer
                .local_description()
                .await
                .expect("local answer")
                .sdp
        }

        pub(crate) fn connected(&self) -> bool {
            *self.connection.borrow() == RTCPeerConnectionState::Connected
        }

        pub(crate) async fn close(&self) {
            let _ = self.peer.close().await;
        }
    }

    pub(crate) async fn receiving_peer() -> (ReceivingPeer, mpsc::Receiver<(bool, Bytes)>) {
        let mut media = MediaEngine::default();
        media.register_default_codecs().expect("default codecs");
        let registry =
            register_default_interceptors(Registry::new(), &mut media).expect("interceptors");
        let (connection_tx, connection) = watch::channel(RTCPeerConnectionState::New);
        let (gathered_tx, gathered) = watch::channel(false);
        let (samples, received) = mpsc::channel(512);
        let peer: Arc<dyn PeerConnection> = Arc::new(
            PeerConnectionBuilder::new()
                .with_configuration(RTCConfigurationBuilder::new().build())
                .with_media_engine(media)
                .with_interceptor_registry(registry)
                .with_handler(Arc::new(ReceiverEvents {
                    events: PeerEvents {
                        connection: connection_tx,
                        gathered: gathered_tx,
                    },
                    samples,
                }))
                .with_udp_addrs(vec!["0.0.0.0:0"])
                .build()
                .await
                .expect("receiving peer"),
        );
        (
            ReceivingPeer {
                peer,
                connection,
                gathered,
            },
            received,
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
    async fn the_offer_proposes_h264_constrained_baseline_as_the_tv_decodes_it() {
        let transport = WebRtcTransport::new().await.expect("transport");
        let offer = transport.create_offer().await.expect("offer");

        assert!(offer.sdp.contains("a=group:BUNDLE"));
        assert!(offer.sdp.lines().any(|line| line.starts_with("a=fmtp:")
            && line.contains("packetization-mode=1")
            && line.contains("profile-level-id=42e01f")));
        assert!(!offer.sdp.contains("profile-level-id=42001f"));
        transport.close().await.expect("close");
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
        let (receiver, mut samples) = receiving_peer().await;
        let offer = sender.create_offer().await.expect("offer");
        let answer = receiver.answer(offer.sdp).await;
        sender
            .accept_answer(SessionDescription { sdp: answer })
            .await
            .expect("accept answer");

        timeout(Duration::from_secs(10), async {
            loop {
                if *sender.connection_state.borrow() == RTCPeerConnectionState::Connected
                    && receiver.connected()
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
        let (received_video, received_opus) = timeout(Duration::from_secs(5), async {
            let (mut stream, mut packets, mut frames) = (Vec::new(), Vec::new(), 0);
            while frames < 2 || packets.len() < encoded_opus.len() {
                let (is_video, data) = samples.recv().await.expect("sample");
                if is_video {
                    stream.extend_from_slice(&data);
                    frames += 1;
                } else if packets.len() < encoded_opus.len() {
                    packets.push(data);
                }
            }
            (stream, packets)
        })
        .await
        .expect("receive timeout");
        assert_eq!(
            received_opus, encoded_opus,
            "Opus payloads survive SRTP/RTP"
        );
        let received_audio = mux_received_opus(&opus_headers, &received_opus);
        assert_ffmpeg_decodes("h264", &received_video);
        assert_ffmpeg_decodes("ogg", &received_audio);
        sender.close().await.expect("close sender");
        receiver.close().await;
    }
}
