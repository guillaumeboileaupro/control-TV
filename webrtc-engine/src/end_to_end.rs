//! The Ubuntu side of D1 end to end: session controller, authenticated pairing with a receiver,
//! WebRTC negotiation and the production FFmpeg source, down to RTP samples on the receiving peer.
//! Only the live inputs are synthetic: a stand-in `ffmpeg` swaps `x11grab` and `pulse` for real-time
//! `lavfi` sources and runs the real FFmpeg with every other production argument.

use crate::transport::tests::receiving_peer;
use crate::{
    AudioConfig, CaptureConfig, EngineState, FfmpegMediaSource, MediaTransport, PairingEndpoint,
    PairingServer, ProcessRunner, SessionConfig, SessionController, SessionDescription,
    SystemClock, VideoConfig, VideoEncoder, WebRtcTransport,
};
use rtp::codecs::h264::H264Packet;
use rtp::codecs::opus::OpusPacket;
use std::net::{IpAddr, Ipv4Addr};
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::Arc;
use std::time::Duration;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::TcpStream;
use tokio::sync::mpsc;
use tokio_rustls::TlsConnector;
use webrtc::api::media_engine::MIME_TYPE_H264;
use webrtc::peer_connection::sdp::session_description::RTCSessionDescription;
use webrtc::peer_connection::RTCPeerConnection;
use webrtc_media::io::sample_builder::SampleBuilder;

const WRAPPER: &str = r#"#!/usr/bin/env python3
import os, sys
args = sys.argv[1:]
with open(PIDS, "a") as pids:
    pids.write(f"{os.getpid()}\n")
for live, source in (
    ("x11grab", "testsrc2=size={size}:rate={rate}"),
    ("pulse", "sine=frequency=440:sample_rate=48000"),
):
    if live in args:
        start = args.index(live) - 1
        end = args.index("-i", start) + 2
        size = args[args.index("-video_size") + 1] if "-video_size" in args else ""
        rate = args[args.index("-framerate") + 1] if "-framerate" in args else ""
        args[start:end] = ["-re", "-f", "lavfi", "-i", source.format(size=size, rate=rate)]
os.execvp("ffmpeg", ["ffmpeg", *args])
"#;

/// The stand-in `ffmpeg` and the file where it records every process it became.
struct SyntheticFfmpeg {
    script: PathBuf,
    pids: PathBuf,
}

impl SyntheticFfmpeg {
    fn new() -> Self {
        let base = std::env::temp_dir().join(format!("control-tv-e2e-{}", std::process::id()));
        let (script, pids) = (base.with_extension("py"), base.with_extension("pids"));
        let source = WRAPPER.replacen("PIDS", &format!("{:?}", pids.display().to_string()), 1);
        // Written by another process: a write handle held here would leak into a concurrent
        // test's fork and make `exec` fail with ETXTBSY.
        let mut writer = std::process::Command::new("sh")
            .args(["-c", "cat > \"$1\" && chmod 755 \"$1\"", "sh"])
            .arg(&script)
            .stdin(Stdio::piped())
            .spawn()
            .expect("script writer");
        std::io::Write::write_all(&mut writer.stdin.take().expect("stdin"), source.as_bytes())
            .expect("write the stand-in ffmpeg");
        assert!(writer.wait().expect("writer").success());
        Self { script, pids }
    }

    fn spawned(&self) -> Vec<u32> {
        std::fs::read_to_string(&self.pids)
            .unwrap_or_default()
            .lines()
            .map(|pid| pid.parse().expect("pid"))
            .collect()
    }
}

impl Drop for SyntheticFfmpeg {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.script);
        let _ = std::fs::remove_file(&self.pids);
    }
}

fn running(pid: u32) -> bool {
    std::fs::read_to_string(format!("/proc/{pid}/stat"))
        .ok()
        .and_then(|stat| stat.rsplit(") ").next().map(str::to_owned))
        .is_some_and(|fields| !fields.starts_with('Z'))
}

/// What the Android receiver does: TLS pinned to the session certificate, token, offer, then an
/// answer after ICE gathering completed.
async fn pair_like_the_receiver(
    endpoint: PairingEndpoint,
    token: String,
    peer: Arc<RTCPeerConnection>,
) {
    let stream = TcpStream::connect(endpoint.address).await.expect("connect");
    let mut roots = rustls::RootCertStore::empty();
    roots
        .add(endpoint.certificate_for_tests())
        .expect("trusted session certificate");
    let config = rustls::ClientConfig::builder()
        .with_root_certificates(roots)
        .with_no_client_auth();
    let name = rustls::pki_types::ServerName::try_from("control-tv.local").expect("name");
    let stream = TlsConnector::from(Arc::new(config))
        .connect(name, stream)
        .await
        .expect("TLS connect");
    let (reader, mut writer) = tokio::io::split(stream);
    let hello = serde_json::json!({ "token": token }).to_string();
    writer
        .write_all(format!("{hello}\n").as_bytes())
        .await
        .expect("hello");
    let mut line = String::new();
    BufReader::new(reader)
        .read_line(&mut line)
        .await
        .expect("offer");
    let offer: serde_json::Value = serde_json::from_str(&line).expect("offer JSON");
    let sdp = offer["offer"].as_str().expect("offer SDP").to_owned();
    peer.set_remote_description(RTCSessionDescription::offer(sdp).expect("offer"))
        .await
        .expect("set offer");
    let answer = peer.create_answer(None).await.expect("answer");
    let mut gathering = peer.gathering_complete_promise().await;
    peer.set_local_description(answer)
        .await
        .expect("set answer");
    let _ = gathering.recv().await;
    let answer = peer.local_description().await.expect("local answer").sdp;
    let reply = serde_json::json!({ "accepted": true, "answer": answer }).to_string();
    writer
        .write_all(format!("{reply}\n").as_bytes())
        .await
        .expect("answer");
}

/// Forwards each received sample as `(is_video, payload)`.
fn collect_samples(peer: &RTCPeerConnection) -> mpsc::Receiver<(bool, bytes::Bytes)> {
    let (sender, receiver) = mpsc::channel(512);
    peer.on_track(Box::new(move |track, _, _| {
        let sender = sender.clone();
        Box::pin(async move {
            tokio::spawn(async move {
                if track.codec().capability.mime_type == MIME_TYPE_H264 {
                    let mut builder = SampleBuilder::new(64, H264Packet::default(), 90_000);
                    while let Ok((packet, _)) = track.read_rtp().await {
                        builder.push(packet);
                        while let Some(sample) = builder.pop() {
                            if sender.send((true, sample.data)).await.is_err() {
                                return;
                            }
                        }
                    }
                } else {
                    let mut builder = SampleBuilder::new(64, OpusPacket, 48_000);
                    while let Ok((packet, _)) = track.read_rtp().await {
                        builder.push(packet);
                        while let Some(sample) = builder.pop() {
                            if sender.send((false, sample.data)).await.is_err() {
                                return;
                            }
                        }
                    }
                }
            });
        })
    }));
    receiver
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn a_paired_receiver_gets_h264_and_opus_from_the_production_media_source() {
    let ffmpeg = SyntheticFfmpeg::new();
    let localhost = IpAddr::V4(Ipv4Addr::LOCALHOST);
    let config = SessionConfig {
        bind_address: localhost,
        capture: CaptureConfig {
            display: ":0".into(),
            offset_x: 0,
            offset_y: 0,
            source_width: 320,
            source_height: 240,
            video: VideoConfig {
                width: 320,
                height: 240,
                fps: 30,
                bitrate_kbps: 500,
                encoder: VideoEncoder::Software,
            },
            audio: AudioConfig {
                enabled: true,
                device: Some("synthetic.monitor".into()),
                bitrate_kbps: 64,
            },
        },
        maximum_duration: Duration::from_secs(60),
        negotiation_timeout: Duration::from_secs(10),
    };
    let transport = Arc::new(WebRtcTransport::new().await.expect("transport"));
    let offer = transport.create_offer().await.expect("offer");
    let (server, endpoint) = PairingServer::bind(localhost).await.expect("signaling");
    let mut controller = SessionController::default();
    controller
        .prepare(&config, &SystemClock)
        .await
        .expect("prepare");
    let token = controller.pairing_token().expect("token").clone();

    let receiver = receiving_peer().await;
    let mut samples = collect_samples(&receiver);
    let client = tokio::spawn(pair_like_the_receiver(
        endpoint,
        token.expose_for_pairing().to_owned(),
        Arc::clone(&receiver),
    ));
    let answer: SessionDescription = server
        .accept_once(&token, &offer, Duration::from_secs(10))
        .await
        .expect("pairing");
    client.await.expect("receiver pairing");
    controller
        .negotiate(
            token.expose_for_pairing(),
            answer,
            transport.as_ref(),
            Duration::from_secs(10),
        )
        .await
        .expect("negotiate");
    let source = FfmpegMediaSource::new(
        ProcessRunner::new(ffmpeg.script.display().to_string()),
        config.capture.clone(),
    );
    controller
        .start_media(
            Box::new(source),
            Arc::clone(&transport) as Arc<dyn MediaTransport>,
        )
        .expect("start media");

    let (mut video, mut audio, mut idr) = (0, 0, false);
    tokio::time::timeout(Duration::from_secs(20), async {
        while video < 15 || audio < 25 || !idr {
            let (is_video, data) = samples.recv().await.expect("receiving tracks");
            if is_video {
                video += 1;
                // Depacketized samples are Annex-B: an IDR slice is NAL unit type 5.
                idr |= data
                    .windows(4)
                    .any(|w| w[..3] == [0, 0, 1] && w[3] & 0x1f == 5);
            } else {
                assert!(!data.is_empty(), "an empty Opus payload");
                audio += 1;
            }
        }
    })
    .await
    .unwrap_or_else(|_| panic!("only {video} H.264 and {audio} Opus samples (IDR: {idr})"));

    let diagnostics = controller.diagnostics().await;
    assert_eq!(diagnostics.video_encoder.as_deref(), Some("libx264"));
    assert_eq!(diagnostics.encoder_fallback, None);
    assert!(diagnostics.sent_video_frames >= 15 && diagnostics.sent_audio_bytes > 0);
    let pids = ffmpeg.spawned();
    assert_eq!(pids.len(), 2, "one video and one audio FFmpeg");

    controller.stop(transport.as_ref()).await.expect("stop");
    assert_eq!(controller.state(), EngineState::Idle);
    assert!(controller.pairing_token().is_none());
    for pid in pids {
        assert!(!running(pid), "FFmpeg {pid} survived Stop");
    }
    let _ = receiver.close().await;
}
