use crate::{CaptureConfig, EngineError, MediaFrame, MediaSource, MediaStreams};
use async_trait::async_trait;
use bytes::Bytes;
use std::process::Stdio;
use std::time::Duration;
use tokio::io::AsyncReadExt;
use tokio::process::{Child, ChildStdout, Command};
use tokio::sync::mpsc;
use tokio::sync::mpsc::error::TrySendError;
use tokio::task::JoinHandle;

const READ_BUFFER: usize = 64 * 1024;
const VIDEO_QUEUE: usize = 1;
const AUDIO_QUEUE: usize = 4;
const MAX_H264_ACCESS_UNIT: usize = 8 * 1024 * 1024;
const MAX_OGG_BUFFER: usize = 1024 * 1024;

/// Starts a child process without involving a shell, so display and device names are never
/// interpreted as command syntax.
#[derive(Clone, Debug)]
pub struct ProcessRunner {
    executable: String,
}

impl ProcessRunner {
    pub fn new(executable: impl Into<String>) -> Self {
        Self {
            executable: executable.into(),
        }
    }

    fn video_arguments(config: &CaptureConfig) -> Vec<String> {
        vec![
            "-hide_banner".into(),
            "-loglevel".into(),
            "warning".into(),
            "-f".into(),
            "x11grab".into(),
            "-draw_mouse".into(),
            "1".into(),
            "-framerate".into(),
            config.video.fps.to_string(),
            "-video_size".into(),
            format!("{}x{}", config.source_width, config.source_height),
            "-i".into(),
            format!("{}+{},{}", config.display, config.offset_x, config.offset_y),
            "-an".into(),
            "-vf".into(),
            format!("scale={}:{}", config.video.width, config.video.height),
            "-c:v".into(),
            "libx264".into(),
            "-preset".into(),
            "ultrafast".into(),
            "-tune".into(),
            "zerolatency".into(),
            "-profile:v".into(),
            "baseline".into(),
            "-g".into(),
            config.video.fps.to_string(),
            "-bf".into(),
            "0".into(),
            "-b:v".into(),
            format!("{}k", config.video.bitrate_kbps),
            "-bsf:v".into(),
            "h264_metadata=aud=insert".into(),
            "-f".into(),
            "h264".into(),
            "pipe:1".into(),
        ]
    }

    fn audio_arguments(config: &CaptureConfig) -> Option<Vec<String>> {
        let device = config.audio.device.as_ref()?;
        Some(vec![
            "-hide_banner".into(),
            "-loglevel".into(),
            "warning".into(),
            "-f".into(),
            "pulse".into(),
            "-i".into(),
            device.clone(),
            "-vn".into(),
            "-c:a".into(),
            "libopus".into(),
            "-application".into(),
            "lowdelay".into(),
            "-frame_duration".into(),
            "20".into(),
            "-b:a".into(),
            format!("{}k", config.audio.bitrate_kbps),
            "-f".into(),
            "opus".into(),
            "pipe:1".into(),
        ])
    }

    fn spawn(&self, arguments: &[String]) -> Result<(Child, ChildStdout), EngineError> {
        let mut child = Command::new(&self.executable)
            .args(arguments)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .kill_on_drop(true)
            .spawn()
            .map_err(|error| EngineError::MediaSource(error.to_string()))?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| EngineError::MediaSource("encoder stdout unavailable".into()))?;
        Ok((child, stdout))
    }
}

impl Default for ProcessRunner {
    fn default() -> Self {
        Self::new("ffmpeg")
    }
}

pub struct FfmpegMediaSource {
    runner: ProcessRunner,
    config: CaptureConfig,
    video_child: Option<Child>,
    audio_child: Option<Child>,
    readers: Vec<JoinHandle<()>>,
}

impl FfmpegMediaSource {
    pub fn new(runner: ProcessRunner, config: CaptureConfig) -> Self {
        Self {
            runner,
            config,
            video_child: None,
            audio_child: None,
            readers: Vec::new(),
        }
    }

    async fn stop_child(child: &mut Option<Child>) -> Result<(), EngineError> {
        Self::stop_child_with_timeout(child, Duration::from_secs(5), true).await
    }

    async fn stop_child_with_timeout(
        child: &mut Option<Child>,
        timeout: Duration,
        terminate: bool,
    ) -> Result<(), EngineError> {
        let Some(process) = child.as_mut() else {
            return Ok(());
        };
        if process
            .try_wait()
            .map_err(|error| EngineError::Cleanup(error.to_string()))?
            .is_some()
        {
            child.take();
            return Ok(());
        }
        let kill = if terminate {
            process
                .kill()
                .await
                .map_err(|error| EngineError::Cleanup(error.to_string()))
        } else {
            Ok(())
        };
        let wait = tokio::time::timeout(timeout, process.wait())
            .await
            .map_err(|_| EngineError::Cleanup("timed out while reaping media process".into()))
            .and_then(|result| {
                result
                    .map(|_| ())
                    .map_err(|error| EngineError::Cleanup(error.to_string()))
            });
        if wait.is_ok() {
            child.take();
        }
        combine_cleanup_results(kill, wait)
    }
}

#[async_trait]
impl MediaSource for FfmpegMediaSource {
    async fn start(&mut self) -> Result<MediaStreams, EngineError> {
        if self.video_child.is_some() {
            return Err(EngineError::MediaSource("source already started".into()));
        }
        let (video_child, video) = self
            .runner
            .spawn(&ProcessRunner::video_arguments(&self.config))?;
        self.video_child = Some(video_child);
        let (video_tx, video_rx) = mpsc::channel(VIDEO_QUEUE);
        let frame_duration = Duration::from_secs_f64(1.0 / f64::from(self.config.video.fps));
        self.readers
            .push(tokio::spawn(read_h264(video, video_tx, frame_duration)));
        let mut audio_rx = None;
        if self.config.audio.enabled {
            let args = ProcessRunner::audio_arguments(&self.config)
                .ok_or_else(|| EngineError::MediaSource("audio device missing".into()))?;
            match self.runner.spawn(&args) {
                Ok((child, stdout)) => {
                    self.audio_child = Some(child);
                    let (audio_tx, receiver) = mpsc::channel(AUDIO_QUEUE);
                    self.readers
                        .push(tokio::spawn(read_ogg_opus(stdout, audio_tx)));
                    audio_rx = Some(receiver);
                }
                Err(error) => {
                    self.stop().await?;
                    return Err(error);
                }
            }
        }
        Ok(MediaStreams {
            video: video_rx,
            audio: audio_rx,
        })
    }

    async fn stop(&mut self) -> Result<(), EngineError> {
        for reader in self.readers.drain(..) {
            reader.abort();
            let _ = reader.await;
        }
        let video_result = Self::stop_child(&mut self.video_child).await;
        let audio_result = Self::stop_child(&mut self.audio_child).await;
        combine_cleanup_results(video_result, audio_result)
    }
}

fn combine_cleanup_results(
    first: Result<(), EngineError>,
    second: Result<(), EngineError>,
) -> Result<(), EngineError> {
    match (first, second) {
        (Ok(()), second) => second,
        (Err(first), Ok(())) => Err(first),
        (Err(first), Err(second)) => Err(EngineError::Cleanup(format!(
            "{first}; additional cleanup failure: {second}"
        ))),
    }
}

async fn read_h264(
    mut reader: ChildStdout,
    sender: mpsc::Sender<Result<MediaFrame, EngineError>>,
    duration: Duration,
) {
    let mut parser = AnnexBAccessUnits::default();
    let mut chunk = vec![0_u8; READ_BUFFER];
    loop {
        match reader.read(&mut chunk).await {
            Ok(0) => {
                if let Some(frame) = parser.finish(duration) {
                    if !send_without_lag(&sender, frame) {
                        return;
                    }
                }
                break;
            }
            Ok(count) => {
                let frames = match parser.push(&chunk[..count], duration) {
                    Ok(frames) => frames,
                    Err(error) => {
                        let _ = sender.send(Err(error)).await;
                        return;
                    }
                };
                for frame in frames {
                    // Interactive video prefers dropping an obsolete complete frame to building lag.
                    let _ = sender.try_send(Ok(frame));
                }
            }
            Err(error) => {
                let _ = sender
                    .send(Err(EngineError::MediaSource(error.to_string())))
                    .await;
                break;
            }
        }
    }
}

async fn read_ogg_opus(
    mut reader: ChildStdout,
    sender: mpsc::Sender<Result<MediaFrame, EngineError>>,
) {
    let mut parser = OggOpusPackets::default();
    let mut chunk = vec![0_u8; READ_BUFFER];
    loop {
        match reader.read(&mut chunk).await {
            Ok(0) => break,
            Ok(count) => match parser.push(&chunk[..count]) {
                Ok(packets) => {
                    for frame in packets {
                        // Keep draining FFmpeg when the transport is late. Backpressure here would
                        // accumulate old Opus packets in the pipe and turn a transient stall into
                        // seconds of audible lag.
                        if !send_without_lag(&sender, frame) {
                            return;
                        }
                    }
                }
                Err(error) => {
                    let _ = sender.send(Err(error)).await;
                    return;
                }
            },
            Err(error) => {
                let _ = sender
                    .send(Err(EngineError::MediaSource(error.to_string())))
                    .await;
                break;
            }
        }
    }
}

fn send_without_lag(
    sender: &mpsc::Sender<Result<MediaFrame, EngineError>>,
    frame: MediaFrame,
) -> bool {
    match sender.try_send(Ok(frame)) {
        Ok(()) | Err(TrySendError::Full(_)) => true,
        Err(TrySendError::Closed(_)) => false,
    }
}

#[derive(Default)]
struct AnnexBAccessUnits {
    buffer: Vec<u8>,
    started: bool,
}

impl AnnexBAccessUnits {
    fn push(&mut self, bytes: &[u8], duration: Duration) -> Result<Vec<MediaFrame>, EngineError> {
        self.buffer.extend_from_slice(bytes);
        if self.buffer.len() > MAX_H264_ACCESS_UNIT {
            return Err(EngineError::MediaSource(
                "H.264 access unit exceeds safety limit".into(),
            ));
        }
        let mut frames = Vec::new();
        loop {
            let starts = nal_starts(&self.buffer);
            let auds: Vec<usize> = starts
                .into_iter()
                .filter(|&offset| nal_type(&self.buffer, offset) == Some(9))
                .collect();
            if !self.started {
                let Some(&first) = auds.first() else { break };
                self.buffer.drain(..first);
                self.started = true;
                continue;
            }
            let Some(&next) = auds.iter().find(|&&offset| offset > 0) else {
                break;
            };
            let data: Vec<u8> = self.buffer.drain(..next).collect();
            frames.push(video_frame(data, duration));
        }
        Ok(frames)
    }

    fn finish(&mut self, duration: Duration) -> Option<MediaFrame> {
        if !self.started || self.buffer.is_empty() {
            return None;
        }
        Some(video_frame(std::mem::take(&mut self.buffer), duration))
    }
}

fn video_frame(data: Vec<u8>, duration: Duration) -> MediaFrame {
    let keyframe = nal_starts(&data)
        .into_iter()
        .any(|offset| nal_type(&data, offset) == Some(5));
    MediaFrame {
        data: Bytes::from(data),
        duration,
        keyframe,
    }
}

fn nal_starts(data: &[u8]) -> Vec<usize> {
    let mut starts = Vec::new();
    let mut index = 0;
    while index + 3 < data.len() {
        if data[index..].starts_with(&[0, 0, 1]) || data[index..].starts_with(&[0, 0, 0, 1]) {
            starts.push(index);
            index += if data[index..].starts_with(&[0, 0, 1]) {
                3
            } else {
                4
            };
        } else {
            index += 1;
        }
    }
    starts
}

fn nal_type(data: &[u8], start: usize) -> Option<u8> {
    let prefix = if data.get(start..)?.starts_with(&[0, 0, 1]) {
        3
    } else {
        4
    };
    data.get(start + prefix).map(|byte| byte & 0x1f)
}

#[derive(Default)]
struct OggOpusPackets {
    buffer: Vec<u8>,
    packet: Vec<u8>,
}

impl OggOpusPackets {
    fn push(&mut self, bytes: &[u8]) -> Result<Vec<MediaFrame>, EngineError> {
        self.buffer.extend_from_slice(bytes);
        if self.buffer.len() > MAX_OGG_BUFFER {
            return Err(EngineError::MediaSource(
                "Ogg page exceeds safety limit".into(),
            ));
        }
        let mut frames = Vec::new();
        loop {
            if self.buffer.len() < 27 {
                break;
            }
            if &self.buffer[..4] != b"OggS" {
                return Err(EngineError::MediaSource(
                    "invalid Ogg capture stream".into(),
                ));
            }
            let segments = usize::from(self.buffer[26]);
            if self.buffer.len() < 27 + segments {
                break;
            }
            let sizes = self.buffer[27..27 + segments].to_vec();
            let body_size: usize = sizes.iter().map(|value| usize::from(*value)).sum();
            let page_size = 27 + segments + body_size;
            if self.buffer.len() < page_size {
                break;
            }
            let page: Vec<u8> = self.buffer.drain(..page_size).collect();
            let mut offset = 27 + segments;
            for size in sizes {
                let end = offset + usize::from(size);
                self.packet.extend_from_slice(&page[offset..end]);
                offset = end;
                if size < 255 {
                    let packet = std::mem::take(&mut self.packet);
                    if !packet.starts_with(b"OpusHead") && !packet.starts_with(b"OpusTags") {
                        frames.push(MediaFrame {
                            duration: opus_duration(&packet)?,
                            data: Bytes::from(packet),
                            keyframe: false,
                        });
                    }
                }
            }
        }
        Ok(frames)
    }
}

fn opus_duration(packet: &[u8]) -> Result<Duration, EngineError> {
    let toc = *packet
        .first()
        .ok_or_else(|| EngineError::MediaSource("empty Opus packet".into()))?;
    let config = toc >> 3;
    let samples_per_frame = if config >= 16 {
        120_u32 << (config & 3)
    } else if config >= 12 {
        480_u32 << (config & 1)
    } else if config & 3 == 3 {
        2_880
    } else {
        480_u32 << (config & 3)
    };
    let frames = match toc & 3 {
        0 => 1,
        1 | 2 => 2,
        _ => u32::from(
            *packet
                .get(1)
                .ok_or_else(|| EngineError::MediaSource("truncated Opus packet".into()))?
                & 0x3f,
        ),
    };
    let samples = samples_per_frame * frames;
    if samples == 0 || samples > 5_760 {
        return Err(EngineError::MediaSource(
            "invalid Opus packet duration".into(),
        ));
    }
    Ok(Duration::from_secs_f64(f64::from(samples) / 48_000.0))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{AudioConfig, VideoConfig};

    fn capture() -> CaptureConfig {
        CaptureConfig {
            display: ":7.0".into(),
            offset_x: 12,
            offset_y: 34,
            source_width: 2560,
            source_height: 1440,
            video: VideoConfig {
                width: 1920,
                height: 1080,
                fps: 60,
                bitrate_kbps: 8_000,
            },
            audio: AudioConfig {
                enabled: true,
                device: Some("sink.monitor".into()),
                bitrate_kbps: 128,
            },
        }
    }

    #[test]
    fn video_command_is_low_latency_and_scoped_to_selected_geometry() {
        let args = ProcessRunner::video_arguments(&capture());
        assert!(args.windows(2).any(|item| item == ["-tune", "zerolatency"]));
        assert!(args.contains(&"2560x1440".into()));
        assert!(args.contains(&"scale=1920:1080".into()));
        assert!(args.contains(&":7.0+12,34".into()));
        assert!(args.windows(2).any(|item| item == ["-bf", "0"]));
        assert!(args
            .windows(2)
            .any(|item| item == ["-bsf:v", "h264_metadata=aud=insert"]));
    }

    #[test]
    fn audio_command_uses_only_the_selected_monitor() {
        let args = ProcessRunner::audio_arguments(&capture()).expect("audio command");
        assert!(args.contains(&"sink.monitor".into()));
        assert!(args
            .windows(2)
            .any(|item| item == ["-application", "lowdelay"]));
    }

    #[tokio::test]
    async fn a_lagging_transport_never_accumulates_more_than_one_video_frame() {
        let (sender, mut receiver) = mpsc::channel(1);
        let frame = |value| MediaFrame {
            data: Bytes::from_static(value),
            duration: Duration::from_millis(33),
            keyframe: false,
        };

        assert!(send_without_lag(&sender, frame(b"old")));
        assert!(send_without_lag(&sender, frame(b"discarded-while-late")));
        assert_eq!(
            receiver
                .recv()
                .await
                .expect("old frame")
                .expect("media")
                .data,
            b"old"[..]
        );
        assert!(send_without_lag(&sender, frame(b"fresh")));
        assert_eq!(
            receiver
                .recv()
                .await
                .expect("fresh frame")
                .expect("media")
                .data,
            b"fresh"[..]
        );
    }

    #[test]
    fn annex_b_chunks_are_assembled_at_aud_boundaries() {
        let first = [0, 0, 0, 1, 0x09, 0x10, 0, 0, 1, 0x65, 1, 2];
        let second = [0, 0, 1, 0x09, 0x10, 0, 0, 1, 0x41, 3, 4];
        let third = [0, 0, 1, 0x09, 0x10, 0, 0, 1, 0x41, 5, 6];
        let mut parser = AnnexBAccessUnits::default();
        assert!(parser
            .push(&first[..7], Duration::from_millis(40))
            .expect("partial access unit")
            .is_empty());
        let frames = parser
            .push(
                &[&first[7..], &second, &third[..3]].concat(),
                Duration::from_millis(40),
            )
            .expect("complete first access unit");
        assert_eq!(frames.len(), 1);
        assert!(frames[0].keyframe);
        let frames = parser
            .push(&third[3..], Duration::from_millis(40))
            .expect("complete second access unit");
        assert_eq!(frames.len(), 1);
        assert!(!frames[0].keyframe);
        assert_eq!(
            parser
                .finish(Duration::from_millis(40))
                .expect("last frame")
                .data
                .as_ref(),
            third
        );
    }

    fn ogg_page(packets: &[&[u8]]) -> Vec<u8> {
        let mut page = b"OggS\0".to_vec();
        page.resize(26, 0);
        page.push(u8::try_from(packets.len()).expect("segments"));
        page.extend(
            packets
                .iter()
                .map(|packet| u8::try_from(packet.len()).expect("packet size")),
        );
        for packet in packets {
            page.extend_from_slice(packet);
        }
        page
    }

    #[test]
    fn ogg_pages_yield_only_complete_opus_packets_with_timestamps() {
        let mut parser = OggOpusPackets::default();
        let page = ogg_page(&[b"OpusHead", b"OpusTags", &[0x98, 1, 2], &[0x98, 3, 4]]);
        assert!(parser.push(&page[..10]).expect("prefix").is_empty());
        let packets = parser.push(&page[10..]).expect("page");
        assert_eq!(packets.len(), 2);
        assert_eq!(packets[0].data.as_ref(), &[0x98, 1, 2]);
        assert_eq!(packets[0].duration, Duration::from_millis(20));
    }

    #[test]
    fn malformed_media_is_rejected_without_unbounded_scanning() {
        let mut parser = OggOpusPackets::default();
        assert!(matches!(
            parser.push(&[0; 27]),
            Err(EngineError::MediaSource(_))
        ));
        assert!(opus_duration(&[]).is_err());
        assert!(opus_duration(&[0x03]).is_err());
        assert!(parser.push(&vec![0; MAX_OGG_BUFFER + 1]).is_err());

        let mut h264 = AnnexBAccessUnits::default();
        assert!(h264
            .push(
                &vec![1; MAX_H264_ACCESS_UNIT + 1],
                Duration::from_millis(40)
            )
            .is_err());
    }

    #[tokio::test]
    async fn cleanup_reaps_a_process_that_already_exited() {
        let mut child = Some(
            Command::new("sh")
                .args(["-c", "exit 0"])
                .spawn()
                .expect("short-lived child"),
        );
        tokio::time::sleep(Duration::from_millis(10)).await;
        FfmpegMediaSource::stop_child(&mut child)
            .await
            .expect("reap child");
        assert!(child.is_none());
    }

    #[tokio::test]
    async fn failed_reap_keeps_the_process_tracked_for_a_retry() {
        let mut child = Some(
            Command::new("sh")
                .args(["-c", "sleep 30"])
                .spawn()
                .expect("long-lived child"),
        );

        let first =
            FfmpegMediaSource::stop_child_with_timeout(&mut child, Duration::ZERO, false).await;
        assert!(first.is_err());
        assert!(child.is_some(), "an unreaped process must remain tracked");

        FfmpegMediaSource::stop_child(&mut child)
            .await
            .expect("retry reaps child");
        assert!(child.is_none());
    }
}
