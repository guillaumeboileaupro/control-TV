//! Privacy-preserving command diagnostics, persisted independently of the application window.
//!
//! The hot path only attempts a bounded channel send. A dedicated thread serializes JSONL and
//! rotates it, so a slow or broken disk never blocks a Tauri command. Events deliberately have no
//! command arguments, device identity, address, error message, pairing code, text or credential.

use std::collections::{HashSet, VecDeque};
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::mpsc::{self, SyncSender, TrySendError};
use std::sync::OnceLock;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde::Serialize;

#[cfg(unix)]
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};

const QUEUE_CAPACITY: usize = 256;
const MAX_LOG_BYTES: u64 = 2 * 1024 * 1024;
const RECENT_TRANSMISSIONS: usize = 1_024;
pub(crate) const LOG_FILE: &str = "commands.jsonl";

static EVENTS: OnceLock<SyncSender<CommandDiagnostic>> = OnceLock::new();

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub(crate) struct CommandDiagnostic {
    timestamp_ms: u64,
    correlation_id: String,
    phase: &'static str,
    method: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    duration_ms: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error_code: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    emitted_count: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    transmitted_count: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    confirmed_count: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    multiple_count: Option<u64>,
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    multiple_detected: bool,
}

impl CommandDiagnostic {
    pub(crate) fn new(
        correlation_id: &str,
        phase: &'static str,
        method: &'static str,
        duration: Option<Duration>,
        error_code: Option<&str>,
    ) -> Self {
        let timestamp_ms = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis()
            .try_into()
            .unwrap_or(u64::MAX);
        Self {
            timestamp_ms,
            correlation_id: correlation_id.to_string(),
            phase,
            method,
            duration_ms: duration.map(|elapsed| elapsed.as_millis().try_into().unwrap_or(u64::MAX)),
            error_code: error_code.map(str::to_string),
            emitted_count: None,
            transmitted_count: None,
            confirmed_count: None,
            multiple_count: None,
            multiple_detected: false,
        }
    }
}

#[derive(Default)]
struct DiagnosticCounts {
    emitted: u64,
    transmitted: u64,
    confirmed: u64,
    multiple: u64,
    recent_transmissions: HashSet<String>,
    transmission_order: VecDeque<String>,
}

impl DiagnosticCounts {
    fn annotate(&mut self, event: &mut CommandDiagnostic) {
        match event.phase {
            "command_emitted" => self.emitted = self.emitted.saturating_add(1),
            "command_transmitted" => {
                self.transmitted = self.transmitted.saturating_add(1);
                if !self
                    .recent_transmissions
                    .insert(event.correlation_id.clone())
                {
                    self.multiple = self.multiple.saturating_add(1);
                    event.multiple_detected = true;
                } else {
                    self.transmission_order
                        .push_back(event.correlation_id.clone());
                    if self.transmission_order.len() > RECENT_TRANSMISSIONS {
                        if let Some(expired) = self.transmission_order.pop_front() {
                            self.recent_transmissions.remove(&expired);
                        }
                    }
                }
            }
            "command_confirmed_by_tv" => self.confirmed = self.confirmed.saturating_add(1),
            _ => {}
        }
        event.emitted_count = Some(self.emitted);
        event.transmitted_count = Some(self.transmitted);
        event.confirmed_count = Some(self.confirmed);
        event.multiple_count = Some(self.multiple);
    }
}

/// Start the one writer for this process. A second call is harmless.
pub(crate) fn install(path: PathBuf) {
    if EVENTS.get().is_some() {
        return;
    }
    let (sender, receiver) = mpsc::sync_channel(QUEUE_CAPACITY);
    let Ok(()) = std::thread::Builder::new()
        .name("control-tv-diagnostics".to_string())
        .spawn(move || {
            let mut counts = DiagnosticCounts::default();
            for mut event in receiver {
                counts.annotate(&mut event);
                if let Err(error) = append(&path, &event) {
                    eprintln!("control-tv: command diagnostic write failed: {error}");
                }
            }
        })
        .map(|_| ())
    else {
        eprintln!("control-tv: command diagnostic writer could not start");
        return;
    };
    let _ = EVENTS.set(sender);
}

/// Best effort only: a full or disconnected queue drops diagnostics, never a command.
pub(crate) fn record(event: CommandDiagnostic) {
    let Some(sender) = EVENTS.get() else {
        return;
    };
    enqueue(sender, event);
}

fn enqueue(sender: &SyncSender<CommandDiagnostic>, event: CommandDiagnostic) {
    match sender.try_send(event) {
        Ok(()) | Err(TrySendError::Full(_)) | Err(TrySendError::Disconnected(_)) => {}
    }
}

fn append(path: &Path, event: &CommandDiagnostic) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| "diagnostic path has no parent".to_string())?;
    fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    #[cfg(unix)]
    fs::set_permissions(parent, fs::Permissions::from_mode(0o700))
        .map_err(|error| error.to_string())?;
    if path
        .metadata()
        .is_ok_and(|metadata| metadata.len() >= MAX_LOG_BYTES)
    {
        let rotated = path.with_extension("jsonl.1");
        let _ = fs::remove_file(&rotated);
        fs::rename(path, rotated).map_err(|error| error.to_string())?;
    }
    let line = serde_json::to_string(event).map_err(|error| error.to_string())?;
    let mut options = OpenOptions::new();
    options.create(true).append(true);
    #[cfg(unix)]
    options.mode(0o600);
    let mut output = options.open(path).map_err(|error| error.to_string())?;
    #[cfg(unix)]
    fs::set_permissions(path, fs::Permissions::from_mode(0o600))
        .map_err(|error| error.to_string())?;
    writeln!(output, "{line}").map_err(|error| error.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn event_json_has_only_the_safe_correlation_fields() {
        let event = CommandDiagnostic::new(
            "request-7",
            "command_failed",
            "press_remote_key",
            Some(Duration::from_millis(31)),
            Some("timeout"),
        );
        let value = serde_json::to_value(event).unwrap();

        assert_eq!(value["correlation_id"], "request-7");
        assert_eq!(value["phase"], "command_failed");
        assert_eq!(value["method"], "press_remote_key");
        assert_eq!(value["duration_ms"], 31);
        assert_eq!(value["error_code"], "timeout");
        let object = value.as_object().unwrap();
        assert_eq!(object.len(), 6);
        for forbidden in ["params", "message", "device", "address", "code", "text"] {
            assert!(!object.contains_key(forbidden));
        }
    }

    #[test]
    fn lifecycle_counts_are_correlated_and_detect_only_the_same_order_twice() {
        let mut counts = DiagnosticCounts::default();
        let mut emitted =
            CommandDiagnostic::new("request-1", "command_emitted", "pause", None, None);
        let mut transmitted =
            CommandDiagnostic::new("request-1", "command_transmitted", "pause", None, None);
        let mut repeated_press =
            CommandDiagnostic::new("request-2", "command_transmitted", "pause", None, None);
        let mut duplicate = transmitted.clone();
        let mut confirmed =
            CommandDiagnostic::new("request-1", "command_confirmed_by_tv", "pause", None, None);

        for event in [
            &mut emitted,
            &mut transmitted,
            &mut repeated_press,
            &mut duplicate,
            &mut confirmed,
        ] {
            counts.annotate(event);
        }

        assert_eq!(confirmed.emitted_count, Some(1));
        assert_eq!(confirmed.transmitted_count, Some(3));
        assert_eq!(confirmed.confirmed_count, Some(1));
        assert_eq!(confirmed.multiple_count, Some(1));
        assert!(!repeated_press.multiple_detected);
        assert!(duplicate.multiple_detected);

        let persisted = serde_json::to_value(duplicate).unwrap();
        assert_eq!(persisted["emitted_count"], 1);
        assert_eq!(persisted["transmitted_count"], 3);
        assert_eq!(persisted["confirmed_count"], 0);
        assert_eq!(persisted["multiple_count"], 1);
        assert_eq!(persisted["multiple_detected"], true);
    }

    #[test]
    fn recent_transmission_tracking_is_bounded() {
        let mut counts = DiagnosticCounts::default();
        for index in 0..=RECENT_TRANSMISSIONS {
            let mut event = CommandDiagnostic::new(
                &format!("request-{index}"),
                "command_transmitted",
                "pause",
                None,
                None,
            );
            counts.annotate(&mut event);
        }

        assert_eq!(counts.recent_transmissions.len(), RECENT_TRANSMISSIONS);
        assert_eq!(counts.transmission_order.len(), RECENT_TRANSMISSIONS);
        assert!(!counts.recent_transmissions.contains("request-0"));
    }

    #[test]
    fn append_writes_json_lines_without_command_arguments() {
        let directory = std::env::temp_dir().join(format!(
            "control-tv-diagnostics-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let path = directory.join(LOG_FILE);
        let event = CommandDiagnostic::new(
            "request-8",
            "command_completed",
            "press_remote_key",
            Some(Duration::from_millis(4)),
            None,
        );

        append(&path, &event).unwrap();

        let line = fs::read_to_string(&path).unwrap();
        assert!(line.ends_with('\n'));
        assert!(!line.contains("pairing"));
        assert!(!line.contains("secret"));
        #[cfg(unix)]
        {
            assert_eq!(path.metadata().unwrap().permissions().mode() & 0o777, 0o600);
            assert_eq!(
                directory.metadata().unwrap().permissions().mode() & 0o777,
                0o700
            );
        }
        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn a_full_queue_drops_diagnostics_without_waiting() {
        let (sender, receiver) = mpsc::sync_channel(1);
        let first = CommandDiagnostic::new("request-1", "command_received", "ping", None, None);
        let dropped = CommandDiagnostic::new("request-2", "command_received", "ping", None, None);

        enqueue(&sender, first.clone());
        enqueue(&sender, dropped);

        assert_eq!(receiver.try_recv().unwrap(), first);
        assert!(receiver.try_recv().is_err());
    }

    #[test]
    fn append_rotates_a_bounded_diagnostic_file() {
        let directory = std::env::temp_dir().join(format!(
            "control-tv-diagnostics-rotation-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&directory).unwrap();
        let path = directory.join(LOG_FILE);
        let oversized = vec![b'x'; MAX_LOG_BYTES as usize];
        fs::write(&path, oversized).unwrap();
        let event = CommandDiagnostic::new("request-3", "command_completed", "ping", None, None);

        append(&path, &event).unwrap();

        assert_eq!(
            directory.join("commands.jsonl.1").metadata().unwrap().len(),
            MAX_LOG_BYTES
        );
        assert!(fs::read_to_string(&path).unwrap().contains("request-3"));
        fs::remove_dir_all(directory).unwrap();
    }
}
