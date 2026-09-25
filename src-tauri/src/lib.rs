//! Tauri application shell.
//!
//! This crate owns *no* Cast/control logic: it only spawns the shared Python control
//! layer (`control_tv.bridge`, in the repository's `src/control_tv/` package) as a
//! long-lived child process and forwards requests to it over line-delimited JSON on
//! stdin/stdout. See `src/control_tv/bridge.py` for the wire protocol and the
//! authoritative behavior. `ControlService` is never reimplemented here.

use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;

use serde::Deserialize;
use serde_json::{json, Value};
use tauri::Manager;

/// The one process boundary to the shared Python control layer.
///
/// Spawned once in `run()`'s `setup` hook and kept alive for the application's
/// lifetime. Requests are sent and answered sequentially (single-flight, serialized by
/// `stdin`/`stdout` each being behind their own `Mutex`) and matched by a monotonically
/// increasing id; nothing today needs concurrent in-flight requests, so this is
/// deliberately the simplest correct thing, not a protocol limitation.
struct PythonBridge {
    /// Kept alive so the process is not dropped early; not otherwise touched. Dropping
    /// `PythonBridge` drops `stdin` first (field order), closing that pipe and sending
    /// EOF, which is exactly what makes `bridge.py`'s `for line in stdin` loop end and
    /// the Python process exit on its own - no signal/kill is needed for a clean shutdown.
    stdin: Mutex<ChildStdin>,
    stdout: Mutex<BufReader<ChildStdout>>,
    next_id: AtomicU64,
    _child: Child,
}

#[derive(Debug, Deserialize)]
struct BridgeError {
    code: String,
    message: String,
}

/// Where `run` looks for the bridge implementation.
///
/// Placeholder for this iteration only: resolves the `uv`-managed development virtual
/// environment next to the repository this crate is compiled from
/// (`CARGO_MANIFEST_DIR/../.venv`), which only makes sense for a locally built,
/// locally run application. Packaging a real Python runtime/sidecar for a distributed
/// build is Phase 7 work and will replace this function's body, not its signature.
fn resolve_python() -> PathBuf {
    let repo_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("src-tauri always has a parent directory")
        .to_path_buf();
    if cfg!(windows) {
        repo_root.join(".venv").join("Scripts").join("python.exe")
    } else {
        repo_root.join(".venv").join("bin").join("python")
    }
}

impl PythonBridge {
    fn spawn(python: &PathBuf) -> Result<Self, String> {
        let mut child = Command::new(python)
            .args(["-m", "control_tv.bridge"])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .spawn()
            .map_err(|error| {
                format!(
                    "failed to spawn the Python control bridge ({}): {error}",
                    python.display()
                )
            })?;

        let stdin = child
            .stdin
            .take()
            .ok_or("spawned bridge process has no stdin")?;
        let stdout = child
            .stdout
            .take()
            .ok_or("spawned bridge process has no stdout")?;

        Ok(Self {
            stdin: Mutex::new(stdin),
            stdout: Mutex::new(BufReader::new(stdout)),
            next_id: AtomicU64::new(1),
            _child: child,
        })
    }

    /// Send one `{method, params}` request and return its result, or a plain-text
    /// error suitable for display (built from the bridge's `{code, message}` or from a
    /// transport-level failure - the frontend does not need to distinguish the two yet).
    fn call(&self, method: &str, params: Value) -> Result<Value, String> {
        let id = self.next_id.fetch_add(1, Ordering::SeqCst);
        let request = json!({ "id": id, "method": method, "params": params });
        let line = serde_json::to_string(&request)
            .map_err(|error| format!("failed to encode bridge request: {error}"))?;

        {
            let mut stdin = self
                .stdin
                .lock()
                .map_err(|_| "bridge stdin lock poisoned".to_string())?;
            writeln!(stdin, "{line}")
                .map_err(|error| format!("failed to write to the bridge process: {error}"))?;
            stdin
                .flush()
                .map_err(|error| format!("failed to flush the bridge process stdin: {error}"))?;
        }

        let mut response_line = String::new();
        {
            let mut stdout = self
                .stdout
                .lock()
                .map_err(|_| "bridge stdout lock poisoned".to_string())?;
            let bytes_read = stdout
                .read_line(&mut response_line)
                .map_err(|error| format!("failed to read from the bridge process: {error}"))?;
            if bytes_read == 0 {
                return Err("the Python control bridge process exited unexpectedly".to_string());
            }
        }

        parse_response(&response_line)
    }
}

/// Pure parsing/translation of one response line - factored out of `call` so it is
/// testable without spawning a real process.
fn parse_response(line: &str) -> Result<Value, String> {
    let response: Value = serde_json::from_str(line.trim())
        .map_err(|error| format!("received a malformed bridge response: {error}"))?;

    let ok = response.get("ok").and_then(Value::as_bool).unwrap_or(false);
    if ok {
        Ok(response.get("result").cloned().unwrap_or(Value::Null))
    } else {
        let error: BridgeError = serde_json::from_value(
            response.get("error").cloned().unwrap_or(Value::Null),
        )
        .map_err(|error| format!("bridge reported an error in an unexpected shape: {error}"))?;
        Err(format!("{} ({})", error.message, error.code))
    }
}

/// Managed application state: either the bridge started successfully, or it did not -
/// in which case every bridge-backed command reports why, instead of the whole
/// application failing to launch. This is what "clear unavailable/error states" (an
/// explicit interface requirement) means at the boundary layer, before any UI exists.
enum BridgeState {
    Ready(PythonBridge),
    Unavailable(String),
}

fn with_bridge<T>(
    state: &tauri::State<Mutex<BridgeState>>,
    call: impl FnOnce(&PythonBridge) -> Result<T, String>,
) -> Result<T, String> {
    let guard = state
        .lock()
        .map_err(|_| "bridge state lock poisoned".to_string())?;
    match &*guard {
        BridgeState::Ready(bridge) => call(bridge),
        BridgeState::Unavailable(reason) => {
            Err(format!("Python control backend unavailable: {reason}"))
        }
    }
}

#[tauri::command]
fn bridge_ping(state: tauri::State<Mutex<BridgeState>>) -> Result<Value, String> {
    with_bridge(&state, |bridge| bridge.call("ping", json!({})))
}

#[tauri::command]
fn bridge_discover_devices(
    state: tauri::State<Mutex<BridgeState>>,
    timeout_seconds: Option<f64>,
) -> Result<Value, String> {
    let mut params = serde_json::Map::new();
    if let Some(timeout_seconds) = timeout_seconds {
        params.insert("timeoutSeconds".to_string(), json!(timeout_seconds));
    }
    with_bridge(&state, |bridge| {
        bridge.call("discover_devices", Value::Object(params))
    })
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            let python = resolve_python();
            let state = match PythonBridge::spawn(&python) {
                Ok(bridge) => BridgeState::Ready(bridge),
                Err(error) => {
                    eprintln!("control-tv: Python control bridge unavailable: {error}");
                    BridgeState::Unavailable(error)
                }
            };
            app.manage(Mutex::new(state));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            bridge_ping,
            bridge_discover_devices
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_a_successful_response() {
        let result = parse_response(r#"{"id":1,"ok":true,"result":{"status":"ready"}}"#).unwrap();
        assert_eq!(result, json!({"status": "ready"}));
    }

    #[test]
    fn a_missing_result_becomes_null() {
        let result = parse_response(r#"{"id":1,"ok":true}"#).unwrap();
        assert_eq!(result, Value::Null);
    }

    #[test]
    fn translates_an_error_response_into_a_display_string() {
        let error = parse_response(
            r#"{"id":1,"ok":false,"error":{"code":"invalid_argument","message":"bad timeout"}}"#,
        )
        .unwrap_err();

        assert_eq!(error, "bad timeout (invalid_argument)");
    }

    #[test]
    fn rejects_malformed_json() {
        let error = parse_response("not json").unwrap_err();

        assert!(error.contains("malformed bridge response"), "{error}");
    }

    #[test]
    fn a_missing_ok_field_is_treated_as_failure_not_success() {
        let error = parse_response(r#"{"id":1}"#).unwrap_err();

        assert!(error.contains("unexpected shape"), "{error}");
    }

    #[test]
    fn resolve_python_points_inside_the_repository_venv() {
        let python = resolve_python();
        let normalized = python.to_string_lossy().replace('\\', "/");

        assert!(
            normalized.ends_with(".venv/bin/python")
                || normalized.ends_with(".venv/Scripts/python.exe"),
            "unexpected path: {normalized}"
        );
    }

    /// Real, end-to-end proof that this crate can actually talk to the Python bridge -
    /// the same binary and module `run()` spawns. Skips (rather than fails) when the
    /// `.venv` this iteration relies on has not been created yet, so a bare `cargo test`
    /// without the Python environment set up does not fail CI for an unrelated reason;
    /// the CI workflow runs `scripts/dev.py setup` first specifically so this is not
    /// skipped there.
    #[test]
    fn spawns_and_pings_the_real_python_bridge() {
        let python = resolve_python();
        if !python.exists() {
            eprintln!(
                "skipping spawns_and_pings_the_real_python_bridge: {} not found - run \
                 `python3 scripts/dev.py setup` first",
                python.display()
            );
            return;
        }

        let bridge = PythonBridge::spawn(&python).expect("failed to spawn the bridge process");
        let result = bridge.call("ping", json!({})).expect("ping request failed");

        assert_eq!(result["status"], "ready");
        assert!(result["controlTvVersion"].is_string());
    }
}
