use crate::{
    CaptureConfig, EncoderReport, EngineError, MediaFrame, MediaSource, MediaStreams, VideoEncoder,
};
use async_trait::async_trait;
use bytes::Bytes;
use std::process::Stdio;
use std::time::Duration;
use tokio::io::AsyncReadExt;
use tokio::process::{Child, ChildStdout, Command};
use tokio::sync::mpsc::error::TrySendError;
use tokio::sync::{mpsc, oneshot};
use tokio::task::JoinHandle;

const READ_BUFFER: usize = 64 * 1024;
const VIDEO_QUEUE: usize = 1;
const AUDIO_QUEUE: usize = 4;
const MAX_H264_ACCESS_UNIT: usize = 8 * 1024 * 1024;
const MAX_OGG_BUFFER: usize = 1024 * 1024;
/// How long a VA-API encoder may take to deliver its first picture before the session falls back
/// to `libx264`: x11grab and the GPU encoder start in well under a second when they work.
const VAAPI_FIRST_PICTURE: Duration = Duration::from_secs(3);
const SOFTWARE_ENCODER: &str = "libx264";
const VAAPI_ENCODER: &str = "h264_vaapi";

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

    fn video_arguments(config: &CaptureConfig, encoder: &VideoEncoder) -> Vec<String> {
        let video = &config.video;
        let scale = format!("scale={}:{}", video.width, video.height);
        let mut arguments: Vec<String> =
            vec!["-hide_banner".into(), "-loglevel".into(), "warning".into()];
        if let VideoEncoder::Vaapi { render_node, .. } = encoder {
            arguments.extend(["-vaapi_device".into(), render_node.clone()]);
        }
        arguments.extend([
            "-f".into(),
            "x11grab".into(),
            "-draw_mouse".into(),
            "1".into(),
            "-framerate".into(),
            video.fps.to_string(),
            "-video_size".into(),
            format!("{}x{}", config.source_width, config.source_height),
            "-i".into(),
            format!("{}+{},{}", config.display, config.offset_x, config.offset_y),
            "-an".into(),
        ]);
        match encoder {
            VideoEncoder::Software => arguments.extend([
                "-vf".into(),
                scale,
                "-c:v".into(),
                SOFTWARE_ENCODER.into(),
                "-preset".into(),
                "ultrafast".into(),
                "-tune".into(),
                "zerolatency".into(),
                "-profile:v".into(),
                "baseline".into(),
                "-b:v".into(),
                format!("{}k", video.bitrate_kbps),
            ]),
            // Constant QP: the bitrate mode failed on the measured Intel GPU (D0, #79).
            VideoEncoder::Vaapi { qp, .. } => arguments.extend([
                "-vf".into(),
                format!("{scale},format=nv12,hwupload"),
                "-c:v".into(),
                VAAPI_ENCODER.into(),
                "-rc_mode".into(),
                "CQP".into(),
                "-qp".into(),
                qp.to_string(),
                "-profile:v".into(),
                "constrained_baseline".into(),
            ]),
        }
        arguments.extend([
            "-g".into(),
            video.fps.to_string(),
            "-bf".into(),
            "0".into(),
            "-bsf:v".into(),
            "h264_metadata=aud=insert".into(),
            // Each access unit reaches the pipe as soon as it is encoded, whatever the FFmpeg
            // version's default for non-seekable outputs.
            "-flush_packets".into(),
            "1".into(),
            "-f".into(),
            "h264".into(),
            "pipe:1".into(),
        ]);
        arguments
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
            // The Ogg muxer groups packets into pages of one second by default and the pipe
            // buffers 32 KiB: measured, no packet reached the reader within two seconds, so audio
            // lagged the picture. One 20 ms packet per page, flushed at once.
            "-page_duration".into(),
            "20000".into(),
            "-flush_packets".into(),
            "1".into(),
            "-f".into(),
            "opus".into(),
            "pipe:1".into(),
        ])
    }

    fn spawn(&self, arguments: &[String]) -> Result<(Child, ChildStdout), EngineError> {
        let mut command = Command::new(&self.executable);
        command
            .args(arguments)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .kill_on_drop(true);
        end_with_host(&mut command);
        let mut child = command
            .spawn()
            .map_err(|error| EngineError::MediaSource(error.to_string()))?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| EngineError::MediaSource("encoder stdout unavailable".into()))?;
        Ok((child, stdout))
    }
}

/// `kill_on_drop` cannot run when the host itself is killed (SIGKILL, OOM, crash): the kernel then
/// sends SIGKILL to the child so no capture or encoder survives the session that owned it.
///
/// Linux delivers it when the *thread* that spawned the child ends. The engine spawns from Tokio
/// runtime worker threads, which live as long as the runtime; never spawn from `spawn_blocking`.
#[cfg(target_os = "linux")]
fn end_with_host(command: &mut Command) {
    let host = std::process::id();
    // SAFETY: the closure runs in the forked child before `exec`. `prctl`, `getppid` and `_exit`
    // are async-signal-safe, and the closure only reads a copied integer.
    unsafe {
        command.pre_exec(move || {
            if libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL) == -1 {
                return Err(std::io::Error::last_os_error());
            }
            // The host may have died between `fork` and `prctl`: nothing would signal us then.
            if u32::try_from(libc::getppid()).ok() != Some(host) {
                libc::_exit(1);
            }
            Ok(())
        });
    }
}

#[cfg(not(target_os = "linux"))]
fn end_with_host(_command: &mut Command) {}

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
    first_picture_timeout: Duration,
}

impl FfmpegMediaSource {
    pub fn new(runner: ProcessRunner, config: CaptureConfig) -> Self {
        Self {
            runner,
            config,
            video_child: None,
            audio_child: None,
            readers: Vec::new(),
            first_picture_timeout: VAAPI_FIRST_PICTURE,
        }
    }

    /// Starts one video encoder; `first_picture` fires once it has produced a complete picture.
    fn spawn_video(
        &mut self,
        encoder: &VideoEncoder,
        first_picture: Option<oneshot::Sender<()>>,
    ) -> Result<mpsc::Receiver<Result<MediaFrame, EngineError>>, EngineError> {
        let (child, stdout) = self
            .runner
            .spawn(&ProcessRunner::video_arguments(&self.config, encoder))?;
        self.video_child = Some(child);
        let (sender, receiver) = mpsc::channel(VIDEO_QUEUE);
        let duration = Duration::from_secs_f64(1.0 / f64::from(self.config.video.fps));
        self.readers.push(tokio::spawn(read_h264(
            stdout,
            sender,
            duration,
            first_picture,
        )));
        Ok(receiver)
    }

    /// The requested encoder, or `libx264` with the reason when VA-API delivers no picture. A
    /// failed VA-API process must be reaped before the fallback starts: if it cannot be, the
    /// start fails and the process stays tracked for `stop`.
    async fn start_video(
        &mut self,
    ) -> Result<
        (
            mpsc::Receiver<Result<MediaFrame, EngineError>>,
            EncoderReport,
        ),
        EngineError,
    > {
        let requested = self.config.video.encoder.clone();
        let mut fallback = None;
        if matches!(requested, VideoEncoder::Vaapi { .. }) {
            let (picture_tx, picture_rx) = oneshot::channel();
            let receiver = self.spawn_video(&requested, Some(picture_tx))?;
            let reason = match tokio::time::timeout(self.first_picture_timeout, picture_rx).await {
                Ok(Ok(())) => {
                    return Ok((
                        receiver,
                        EncoderReport {
                            encoder: Some(VAAPI_ENCODER.into()),
                            fallback: None,
                        },
                    ))
                }
                Ok(Err(_)) => "the VA-API encoder stopped before its first picture",
                Err(_) => "the VA-API encoder produced no picture in time",
            };
            if let Some(reader) = self.readers.pop() {
                reader.abort();
                let _ = reader.await;
            }
            drop(receiver);
            Self::stop_child(&mut self.video_child).await?;
            fallback = Some(format!("{reason}; using {SOFTWARE_ENCODER}"));
        }
        let receiver = self.spawn_video(&VideoEncoder::Software, None)?;
        Ok((
            receiver,
            EncoderReport {
                encoder: Some(SOFTWARE_ENCODER.into()),
                fallback,
            },
        ))
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
        let (video_rx, encoder) = self.start_video().await?;
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
            encoder,
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
    mut first_picture: Option<oneshot::Sender<()>>,
) {
    let mut parser = AnnexBAccessUnits::default();
    let mut dropper = LagDropper::video();
    let mut chunk = vec![0_u8; READ_BUFFER];
    loop {
        match reader.read(&mut chunk).await {
            Ok(0) => {
                if let Some(frame) = parser.finish(duration) {
                    if !dropper.offer(&sender, frame) {
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
                // Only a picture closed by the next AUD counts: a partial one at EOF does not.
                if !frames.is_empty() {
                    if let Some(signal) = first_picture.take() {
                        let _ = signal.send(());
                    }
                }
                for frame in frames {
                    // Interactive video prefers dropping an obsolete complete frame to building lag.
                    if !dropper.offer(&sender, frame) {
                        return;
                    }
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
    let mut dropper = LagDropper::audio();
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
                        if !dropper.offer(&sender, frame) {
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

/// Discards media while the transport is late without corrupting what the receiver plays.
///
/// Each discarded unit is reported on the next unit sent (`dropped_before`), so the transport
/// skips its RTP time instead of compressing the timeline against the other track. A discarded
/// H.264 frame is the reference of the frames that follow it: those are discarded as well until
/// the next keyframe, so the picture freezes briefly instead of decoding into corruption.
struct LagDropper {
    resync_on_keyframe: bool,
    awaiting_keyframe: bool,
    dropped: u16,
}

impl LagDropper {
    fn video() -> Self {
        Self {
            resync_on_keyframe: true,
            awaiting_keyframe: false,
            dropped: 0,
        }
    }

    fn audio() -> Self {
        Self {
            resync_on_keyframe: false,
            awaiting_keyframe: false,
            dropped: 0,
        }
    }

    /// Queues `frame` or discards it; false once the session no longer reads the queue.
    fn offer(
        &mut self,
        sender: &mpsc::Sender<Result<MediaFrame, EngineError>>,
        mut frame: MediaFrame,
    ) -> bool {
        if sender.is_closed() {
            return false;
        }
        if self.awaiting_keyframe && !frame.keyframe {
            self.dropped = self.dropped.saturating_add(1);
            return true;
        }
        frame.dropped_before = self.dropped;
        match sender.try_send(Ok(frame)) {
            Ok(()) => {
                self.dropped = 0;
                self.awaiting_keyframe = false;
                true
            }
            Err(TrySendError::Full(_)) => {
                self.dropped = self.dropped.saturating_add(1);
                self.awaiting_keyframe = self.resync_on_keyframe;
                true
            }
            Err(TrySendError::Closed(_)) => false,
        }
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
        dropped_before: 0,
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
                            dropped_before: 0,
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
                encoder: VideoEncoder::Software,
            },
            audio: AudioConfig {
                enabled: true,
                device: Some("sink.monitor".into()),
                bitrate_kbps: 128,
            },
        }
    }

    fn vaapi() -> VideoEncoder {
        VideoEncoder::Vaapi {
            render_node: "/dev/dri/renderD128".into(),
            qp: 24,
        }
    }

    #[test]
    fn video_command_is_low_latency_and_scoped_to_selected_geometry() {
        let args = ProcessRunner::video_arguments(&capture(), &VideoEncoder::Software);
        assert!(args.windows(2).any(|item| item == ["-tune", "zerolatency"]));
        assert!(args.windows(2).any(|item| item == ["-c:v", "libx264"]));
        assert!(args.windows(2).any(|item| item == ["-b:v", "8000k"]));
        assert!(!args.contains(&"-vaapi_device".into()));
        assert!(args.contains(&"2560x1440".into()));
        assert!(args.contains(&"scale=1920:1080".into()));
        assert!(args.contains(&":7.0+12,34".into()));
        assert!(args.windows(2).any(|item| item == ["-bf", "0"]));
        assert!(args
            .windows(2)
            .any(|item| item == ["-bsf:v", "h264_metadata=aud=insert"]));
    }

    #[test]
    fn vaapi_command_uploads_the_scaled_picture_and_uses_constant_qp() {
        let args = ProcessRunner::video_arguments(&capture(), &vaapi());
        let device = args
            .windows(2)
            .position(|item| item == ["-vaapi_device", "/dev/dri/renderD128"])
            .expect("render node");
        let input = args.iter().position(|item| item == "-i").expect("input");
        assert!(
            device < input,
            "the device is a global option before the input"
        );
        assert!(args.contains(&"scale=1920:1080,format=nv12,hwupload".into()));
        for pair in [
            ["-c:v", "h264_vaapi"],
            ["-rc_mode", "CQP"],
            ["-qp", "24"],
            ["-profile:v", "constrained_baseline"],
            ["-bf", "0"],
            ["-g", "60"],
            ["-bsf:v", "h264_metadata=aud=insert"],
        ] {
            assert!(args.windows(2).any(|item| item == pair), "{pair:?}");
        }
        assert!(
            !args.contains(&"-b:v".into()),
            "bitrate mode fails on the measured GPU"
        );
    }

    #[test]
    fn audio_command_uses_only_the_selected_monitor() {
        let args = ProcessRunner::audio_arguments(&capture()).expect("audio command");
        assert!(args.contains(&"sink.monitor".into()));
        assert!(args
            .windows(2)
            .any(|item| item == ["-application", "lowdelay"]));
    }

    #[test]
    fn media_commands_flush_every_unit_at_once() {
        let audio = ProcessRunner::audio_arguments(&capture()).expect("audio command");
        assert!(audio
            .windows(2)
            .any(|item| item == ["-page_duration", "20000"]));
        for args in [
            audio,
            ProcessRunner::video_arguments(&capture(), &VideoEncoder::Software),
            ProcessRunner::video_arguments(&capture(), &vaapi()),
        ] {
            assert!(args.windows(2).any(|item| item == ["-flush_packets", "1"]));
        }
    }

    /// The production command with only its live input (`x11grab` or `pulse`, then `-i`) replaced
    /// by a real-time synthetic source, so encoder, muxer and flushing options run for real.
    fn with_synthetic_input(args: Vec<String>, format: &str, source: String) -> Vec<String> {
        let start = args
            .windows(2)
            .position(|item| item == ["-f", format])
            .expect("live input format");
        let input = start
            + args[start..]
                .iter()
                .position(|item| item == "-i")
                .expect("live input");
        let mut synthetic = args[..start].to_vec();
        synthetic.extend(["-re", "-f", "lavfi", "-i"].map(String::from));
        synthetic.push(source);
        synthetic.extend_from_slice(&args[input + 2..]);
        synthetic
    }

    /// Units a real FFmpeg delivers through the production parser during the second that follows
    /// the first one, with their arrival times relative to it. Measuring from the first unit keeps
    /// a loaded machine's slow FFmpeg start out of the result: what matters is the pacing.
    async fn paced_units(args: Vec<String>, audio: bool) -> Vec<(Duration, MediaFrame)> {
        let (child, stdout) = ProcessRunner::default().spawn(&args).expect("ffmpeg");
        let (sender, mut receiver) = mpsc::channel(256);
        let reader = if audio {
            tokio::spawn(read_ogg_opus(stdout, sender))
        } else {
            tokio::spawn(read_h264(stdout, sender, Duration::from_millis(33), None))
        };
        let first = tokio::time::timeout(Duration::from_secs(10), receiver.recv())
            .await
            .expect("a first unit")
            .expect("open queue")
            .expect("media");
        let started = tokio::time::Instant::now();
        let mut units = vec![(Duration::ZERO, first)];
        let window = started + Duration::from_secs(1);
        while let Ok(Some(unit)) = tokio::time::timeout_at(window, receiver.recv()).await {
            units.push((started.elapsed(), unit.expect("media")));
        }
        reader.abort();
        let mut child = Some(child);
        FfmpegMediaSource::stop_child(&mut child)
            .await
            .expect("reap ffmpeg");
        units
    }

    /// Real-time delivery: many units, spread over the second instead of one buffered burst.
    fn assert_paced(units: &[(Duration, MediaFrame)], kind: &str) {
        let spread = units.last().map(|(at, _)| *at).unwrap_or_default();
        assert!(
            units.len() >= 10,
            "only {} {kind} in one second",
            units.len()
        );
        assert!(
            spread >= Duration::from_millis(500),
            "{kind} arrived in a burst ({spread:?})"
        );
    }

    #[tokio::test]
    async fn opus_reaches_the_reader_in_real_time_not_in_one_second_pages() {
        let args = with_synthetic_input(
            ProcessRunner::audio_arguments(&capture()).expect("audio command"),
            "pulse",
            "sine=frequency=440:sample_rate=48000".into(),
        );
        // Before the fix nothing arrived for two seconds, then whole pages at once.
        let packets = paced_units(args, true).await;
        assert_paced(&packets, "Opus packets");
        assert!(packets
            .iter()
            .all(|(_, packet)| packet.duration == Duration::from_millis(20)));
    }

    #[tokio::test]
    async fn h264_access_units_reach_the_reader_frame_by_frame() {
        let mut config = capture();
        config.video = VideoConfig {
            width: 640,
            height: 360,
            fps: 30,
            bitrate_kbps: 1_000,
            encoder: VideoEncoder::Software,
        };
        let args = with_synthetic_input(
            ProcessRunner::video_arguments(&config, &VideoEncoder::Software),
            "x11grab",
            "color=c=gray:size=640x360:rate=30".into(),
        );
        let frames = paced_units(args, false).await;
        assert!(
            frames[0].1.keyframe,
            "the stream starts with an IDR picture"
        );
        assert_paced(&frames, "H.264 frames");
    }

    fn unit(value: &'static [u8], keyframe: bool) -> MediaFrame {
        MediaFrame {
            data: Bytes::from_static(value),
            duration: Duration::from_millis(33),
            keyframe,
            dropped_before: 0,
        }
    }

    async fn next(receiver: &mut mpsc::Receiver<Result<MediaFrame, EngineError>>) -> MediaFrame {
        receiver.recv().await.expect("queued unit").expect("media")
    }

    #[tokio::test]
    async fn a_lagging_transport_never_accumulates_more_than_one_video_frame() {
        let (sender, mut receiver) = mpsc::channel(1);
        let mut dropper = LagDropper::video();

        assert!(dropper.offer(&sender, unit(b"old", true)));
        assert!(dropper.offer(&sender, unit(b"discarded-while-late", false)));
        assert_eq!(next(&mut receiver).await.data, b"old"[..]);
        assert!(dropper.offer(&sender, unit(b"fresh", true)));
        let fresh = next(&mut receiver).await;
        assert_eq!(fresh.data, b"fresh"[..]);
        assert_eq!(fresh.dropped_before, 1);
    }

    #[tokio::test]
    async fn frames_referencing_a_discarded_frame_wait_for_the_next_keyframe() {
        let (sender, mut receiver) = mpsc::channel(1);
        let mut dropper = LagDropper::video();

        assert!(dropper.offer(&sender, unit(b"idr", true)));
        assert!(dropper.offer(&sender, unit(b"late-p", false)));
        assert_eq!(next(&mut receiver).await.data, b"idr"[..]);
        // The queue has room again, but these frames reference the discarded one.
        assert!(dropper.offer(&sender, unit(b"broken-p1", false)));
        assert!(dropper.offer(&sender, unit(b"broken-p2", false)));
        assert!(receiver.try_recv().is_err());
        assert!(dropper.offer(&sender, unit(b"next-idr", true)));
        let resync = next(&mut receiver).await;
        assert_eq!(resync.data, b"next-idr"[..]);
        assert_eq!(resync.dropped_before, 3);
        assert!(dropper.offer(&sender, unit(b"p-after-idr", false)));
        assert_eq!(next(&mut receiver).await.dropped_before, 0);
    }

    #[tokio::test]
    async fn discarded_audio_is_reported_on_the_next_packet_without_waiting() {
        let (sender, mut receiver) = mpsc::channel(1);
        let mut dropper = LagDropper::audio();

        assert!(dropper.offer(&sender, unit(b"first", false)));
        assert!(dropper.offer(&sender, unit(b"late-1", false)));
        assert!(dropper.offer(&sender, unit(b"late-2", false)));
        assert_eq!(next(&mut receiver).await.data, b"first"[..]);
        assert!(dropper.offer(&sender, unit(b"resumed", false)));
        let resumed = next(&mut receiver).await;
        assert_eq!(resumed.data, b"resumed"[..]);
        assert_eq!(resumed.dropped_before, 2);
    }

    #[tokio::test]
    async fn a_closed_queue_stops_the_reader() {
        let (sender, receiver) = mpsc::channel(1);
        drop(receiver);
        assert!(!LagDropper::video().offer(&sender, unit(b"idr", true)));
        assert!(!LagDropper::audio().offer(&sender, unit(b"opus", false)));
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

    /// Two complete AUD-delimited pictures (AUD, IDR slice) and the next AUD.
    const PICTURES: &str = r"\000\000\000\001\011\020\000\000\001\145\210\000\000\000\001\011\020\000\000\001\145\210\000\000\000\001\011\020";

    /// A stand-in `ffmpeg`: `vaapi` is what it does when asked for `h264_vaapi`.
    struct FakeFfmpeg(std::path::PathBuf);

    impl FakeFfmpeg {
        fn new(name: &str, vaapi: &str) -> Self {
            let path = std::env::temp_dir().join(format!(
                "control-tv-fake-ffmpeg-{}-{name}",
                std::process::id()
            ));
            let pictures = format!("printf '{PICTURES}'; exec sleep 30");
            let script = format!(
                "#!/bin/sh\ncase \" $* \" in *\" h264_vaapi \"*) {vaapi} ;; *) {pictures} ;; esac\n"
            );
            // Written by a separate process: a file this process holds open for writing would be
            // inherited by a concurrent test's fork and make `exec` fail with ETXTBSY.
            let mut writer = std::process::Command::new("sh")
                .args(["-c", "cat > \"$1\" && chmod 755 \"$1\"", "sh"])
                .arg(&path)
                .stdin(Stdio::piped())
                .spawn()
                .expect("script writer");
            std::io::Write::write_all(&mut writer.stdin.take().expect("stdin"), script.as_bytes())
                .expect("fake ffmpeg");
            assert!(writer.wait().expect("writer").success());
            Self(path)
        }

        fn source(&self, encoder: VideoEncoder) -> FfmpegMediaSource {
            let mut config = capture();
            config.audio.enabled = false;
            config.video.encoder = encoder;
            let mut source = FfmpegMediaSource::new(
                ProcessRunner::new(self.0.to_string_lossy().into_owned()),
                config,
            );
            source.first_picture_timeout = Duration::from_millis(500);
            source
        }
    }

    impl Drop for FakeFfmpeg {
        fn drop(&mut self) {
            let _ = std::fs::remove_file(&self.0);
        }
    }

    async fn first_picture(streams: &mut MediaStreams) -> MediaFrame {
        tokio::time::timeout(Duration::from_secs(5), streams.video.recv())
            .await
            .expect("a picture in time")
            .expect("open queue")
            .expect("media")
    }

    #[tokio::test]
    async fn a_working_vaapi_encoder_is_used_without_fallback() {
        let fake = FakeFfmpeg::new("vaapi-ok", &format!("printf '{PICTURES}'; exec sleep 30"));
        let mut source = fake.source(vaapi());
        let mut streams = source.start().await.expect("start");
        assert_eq!(
            streams.encoder,
            EncoderReport {
                encoder: Some("h264_vaapi".into()),
                fallback: None,
            }
        );
        assert!(first_picture(&mut streams).await.keyframe);
        source.stop().await.expect("stop");
        assert!(source.video_child.is_none());
    }

    #[tokio::test]
    async fn vaapi_failures_fall_back_to_libx264_and_say_why() {
        for (name, vaapi_behaviour, reason) in [
            ("vaapi-exits", "exit 1", "stopped before its first picture"),
            (
                "vaapi-silent",
                "exec sleep 30",
                "produced no picture in time",
            ),
            (
                "vaapi-partial",
                "printf '\\000\\000\\000\\001\\011\\020'; exit 1",
                "stopped before",
            ),
        ] {
            let fake = FakeFfmpeg::new(name, vaapi_behaviour);
            let mut source = fake.source(vaapi());
            let mut streams = source.start().await.expect("start");
            assert_eq!(
                streams.encoder.encoder.as_deref(),
                Some("libx264"),
                "{name}"
            );
            let fallback = streams.encoder.fallback.clone().expect("fallback reason");
            assert!(fallback.contains(reason), "{name}: {fallback}");
            assert!(fallback.ends_with("using libx264"), "{name}: {fallback}");
            assert!(first_picture(&mut streams).await.keyframe, "{name}");
            assert_eq!(source.readers.len(), 1, "{name}: the VA-API reader is gone");
            source.stop().await.expect("stop");
            assert!(source.video_child.is_none(), "{name}");
        }
    }

    #[tokio::test]
    async fn a_missing_encoder_is_an_error_not_a_fallback() {
        for encoder in [VideoEncoder::Software, vaapi()] {
            let mut config = capture();
            config.video.encoder = encoder;
            let mut source = FfmpegMediaSource::new(
                ProcessRunner::new("/nonexistent/control-tv-ffmpeg"),
                config,
            );
            assert!(matches!(
                source.start().await,
                Err(EngineError::MediaSource(_))
            ));
            assert!(source.video_child.is_none());
        }
    }

    const HOST_ENV: &str = "CONTROL_TV_TEST_MEDIA_HOST";

    /// Not a test on its own: the host process `media_processes_end_when_their_host_is_killed`
    /// starts and kills. It spawns a long-lived child the way a session does and reports its pid.
    #[test]
    fn media_host_for_the_kill_test() {
        if std::env::var_os(HOST_ENV).is_none() {
            return;
        }
        let runtime = tokio::runtime::Builder::new_multi_thread()
            .enable_all()
            .build()
            .expect("runtime");
        runtime.block_on(async {
            let child = tokio::spawn(async { ProcessRunner::new("sleep").spawn(&["60".into()]) })
                .await
                .expect("spawn task")
                .expect("child");
            println!("child-pid {}", child.0.id().expect("pid"));
            tokio::time::sleep(Duration::from_secs(60)).await;
            drop(child);
        });
    }

    fn running(pid: u32) -> bool {
        // A zombie has ended; only its new parent has not reaped it yet.
        std::fs::read_to_string(format!("/proc/{pid}/stat"))
            .ok()
            .and_then(|stat| stat.rsplit(") ").next().map(str::to_owned))
            .is_some_and(|fields| !fields.starts_with('Z'))
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn media_processes_end_when_their_host_is_killed() {
        use std::io::BufRead;
        let mut host = std::process::Command::new(std::env::current_exe().expect("test binary"))
            .args([
                "--exact",
                "ffmpeg::tests::media_host_for_the_kill_test",
                "--nocapture",
                "--test-threads=1",
            ])
            .env(HOST_ENV, "1")
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .expect("host");
        let mut lines = std::io::BufReader::new(host.stdout.take().expect("host stdout")).lines();
        let pid: u32 = lines
            .by_ref()
            .map_while(Result::ok)
            // The harness prints `test <name> ... ` before the test's own output, on the same line.
            .find_map(|line| line.split_once("child-pid ").map(|(_, pid)| pid.to_owned()))
            .expect("child pid")
            .parse()
            .expect("numeric pid");
        assert!(running(pid), "the child runs while its host lives");

        host.kill().expect("SIGKILL the host");
        host.wait().expect("reap the host");
        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        while running(pid) && std::time::Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(20));
        }
        assert!(!running(pid), "the child outlived its killed host");
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
