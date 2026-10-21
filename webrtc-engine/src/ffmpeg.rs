use crate::{CaptureConfig, EngineError, MediaSource};
use async_trait::async_trait;
use bytes::Bytes;
use std::process::Stdio;
use tokio::io::AsyncReadExt;
use tokio::process::{Child, ChildStdout, Command};

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
            format!("{}x{}", config.video.width, config.video.height),
            "-i".into(),
            format!("{}+{},{}", config.display, config.offset_x, config.offset_y),
            "-an".into(),
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
    video: Option<ChildStdout>,
    audio: Option<ChildStdout>,
}

impl FfmpegMediaSource {
    pub fn new(runner: ProcessRunner, config: CaptureConfig) -> Self {
        Self {
            runner,
            config,
            video_child: None,
            audio_child: None,
            video: None,
            audio: None,
        }
    }

    async fn stop_child(child: &mut Option<Child>) -> Result<(), EngineError> {
        let Some(mut process) = child.take() else {
            return Ok(());
        };
        process
            .kill()
            .await
            .map_err(|error| EngineError::Cleanup(error.to_string()))?;
        process
            .wait()
            .await
            .map_err(|error| EngineError::Cleanup(error.to_string()))?;
        Ok(())
    }
}

#[async_trait]
impl MediaSource for FfmpegMediaSource {
    async fn start(&mut self) -> Result<(), EngineError> {
        if self.video_child.is_some() {
            return Err(EngineError::MediaSource("source already started".into()));
        }
        let (video_child, video) = self
            .runner
            .spawn(&ProcessRunner::video_arguments(&self.config))?;
        self.video_child = Some(video_child);
        self.video = Some(video);
        if self.config.audio.enabled {
            let args = ProcessRunner::audio_arguments(&self.config)
                .ok_or_else(|| EngineError::MediaSource("audio device missing".into()))?;
            match self.runner.spawn(&args) {
                Ok((child, stdout)) => {
                    self.audio_child = Some(child);
                    self.audio = Some(stdout);
                }
                Err(error) => {
                    self.stop().await?;
                    return Err(error);
                }
            }
        }
        Ok(())
    }

    async fn next_video_chunk(&mut self) -> Result<Option<Bytes>, EngineError> {
        read_chunk(&mut self.video).await
    }

    async fn next_audio_chunk(&mut self) -> Result<Option<Bytes>, EngineError> {
        read_chunk(&mut self.audio).await
    }

    async fn stop(&mut self) -> Result<(), EngineError> {
        self.video.take();
        self.audio.take();
        let video_result = Self::stop_child(&mut self.video_child).await;
        let audio_result = Self::stop_child(&mut self.audio_child).await;
        video_result.and(audio_result)
    }
}

async fn read_chunk(reader: &mut Option<ChildStdout>) -> Result<Option<Bytes>, EngineError> {
    let Some(reader) = reader else {
        return Ok(None);
    };
    let mut buffer = vec![0_u8; 64 * 1024];
    let count = reader
        .read(&mut buffer)
        .await
        .map_err(|error| EngineError::MediaSource(error.to_string()))?;
    if count == 0 {
        return Ok(None);
    }
    buffer.truncate(count);
    Ok(Some(Bytes::from(buffer)))
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
        assert!(args.contains(&"1920x1080".into()));
        assert!(args.contains(&":7.0+12,34".into()));
        assert!(args.windows(2).any(|item| item == ["-bf", "0"]));
    }

    #[test]
    fn audio_command_uses_only_the_selected_monitor() {
        let args = ProcessRunner::audio_arguments(&capture()).expect("audio command");
        assert!(args.contains(&"sink.monitor".into()));
        assert!(args
            .windows(2)
            .any(|item| item == ["-application", "lowdelay"]));
    }
}
