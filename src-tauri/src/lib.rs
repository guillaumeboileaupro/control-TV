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
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tauri::Manager;

/// Upper bound on `ping`: this call never has an operator-supplied timeout, so it gets
/// a fixed, generous one - long enough for a healthy bridge under load, short enough
/// that a genuinely stuck bridge is reported quickly.
const PING_TIMEOUT: Duration = Duration::from_secs(5);
/// Safety margin added on top of a caller-supplied discovery timeout, so a slow-but-
/// working discovery (bounded by `ControlService` itself) is never cut off by this
/// outer guard first; only a bridge that is truly stuck exceeds it.
const DISCOVERY_TIMEOUT_MARGIN: Duration = Duration::from_secs(5);
/// Outer bound on one `get_status` round trip: twice the control layer's own 5s status
/// budget (`DEFAULT_STATUS_TIMEOUT` in `control_tv.service`), so only a genuinely stuck
/// bridge exceeds it. The bridge is single-flight and this timer starts before a request
/// gets its turn, so a request must never wait behind another: the UI keeps at most one
/// request in flight (it offers no selection, refresh or discovery while one is running).
const STATUS_TIMEOUT: Duration = Duration::from_secs(10);
/// Outer bound on one playback command. The control layer runs a command inside its own
/// windows - an identity snapshot and the confirmation share one 5s window, and delivering
/// the command may first wait for a connection with one bounded recovery, about 35s in the
/// worst case - so this is deliberately generous: only a genuinely stuck bridge exceeds it.
/// The UI keeps one request in flight and never resends on a timeout (delivery is then
/// ambiguous), so a long bound only ever delays reporting a stuck bridge.
const COMMAND_TIMEOUT: Duration = Duration::from_secs(60);

/// Codes raised by this crate itself, as opposed to codes relayed from the bridge (which
/// are `ControlError` codes such as `device_unavailable`, or `internal_error`). They let
/// the UI tell "the backend is not there" apart from "the device is not there".
const CODE_BACKEND_UNAVAILABLE: &str = "backend_unavailable";
const CODE_BRIDGE_TIMEOUT: &str = "bridge_timeout";
const CODE_BRIDGE_TRANSPORT: &str = "bridge_transport";

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

/// The failure of one bridge-backed command, as the frontend receives it: a stable
/// machine-readable `code` plus a human-readable `message`. It is both what the bridge
/// sends as `error` (`Deserialize`) and what a rejected `invoke` carries (`Serialize`),
/// so a `ControlError` code reaches the UI untouched instead of being flattened into text.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
struct BridgeFailure {
    code: String,
    message: String,
}

impl BridgeFailure {
    fn new(code: &str, message: impl Into<String>) -> Self {
        Self {
            code: code.to_string(),
            message: message.into(),
        }
    }

    /// The request or response could not travel to/from the bridge process at all.
    fn transport(message: impl Into<String>) -> Self {
        Self::new(CODE_BRIDGE_TRANSPORT, message)
    }
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

    /// Send one `{method, params}` request and return its result, or the bridge's own
    /// `{code, message}` error, or a `bridge_transport` failure when the request or its
    /// response could not travel to/from the process at all.
    fn call(&self, method: &str, params: Value) -> Result<Value, BridgeFailure> {
        let id = self.next_id.fetch_add(1, Ordering::SeqCst);
        let request = json!({ "id": id, "method": method, "params": params });
        let line = serde_json::to_string(&request).map_err(|error| {
            BridgeFailure::transport(format!("failed to encode bridge request: {error}"))
        })?;

        {
            let mut stdin = self
                .stdin
                .lock()
                .map_err(|_| BridgeFailure::transport("bridge stdin lock poisoned"))?;
            writeln!(stdin, "{line}").map_err(|error| {
                BridgeFailure::transport(format!("failed to write to the bridge process: {error}"))
            })?;
            stdin.flush().map_err(|error| {
                BridgeFailure::transport(format!(
                    "failed to flush the bridge process stdin: {error}"
                ))
            })?;
        }

        let mut response_line = String::new();
        {
            let mut stdout = self
                .stdout
                .lock()
                .map_err(|_| BridgeFailure::transport("bridge stdout lock poisoned"))?;
            let bytes_read = stdout.read_line(&mut response_line).map_err(|error| {
                BridgeFailure::transport(format!("failed to read from the bridge process: {error}"))
            })?;
            if bytes_read == 0 {
                return Err(BridgeFailure::transport(
                    "the Python control bridge process exited unexpectedly",
                ));
            }
        }

        parse_response(&response_line)
    }
}

/// Pure parsing/translation of one response line - factored out of `call` so it is
/// testable without spawning a real process.
fn parse_response(line: &str) -> Result<Value, BridgeFailure> {
    let response: Value = serde_json::from_str(line.trim()).map_err(|error| {
        BridgeFailure::transport(format!("received a malformed bridge response: {error}"))
    })?;

    let ok = response.get("ok").and_then(Value::as_bool).unwrap_or(false);
    if ok {
        Ok(response.get("result").cloned().unwrap_or(Value::Null))
    } else {
        let failure = serde_json::from_value(response.get("error").cloned().unwrap_or(Value::Null))
            .unwrap_or_else(|error| {
                BridgeFailure::transport(format!(
                    "bridge reported an error in an unexpected shape: {error}"
                ))
            });
        Err(failure)
    }
}

/// Managed application state: either the bridge started successfully, or it did not -
/// in which case every bridge-backed command reports why (`backend_unavailable`), instead
/// of the whole application failing to launch.
enum BridgeState {
    Ready(PythonBridge),
    Unavailable(String),
}

/// `Arc` so a command can clone a handle to it and move that clone onto the blocking
/// worker thread `call_bridge` spawns, independently of the `'_`-scoped `tauri::State`
/// borrow (which cannot itself cross into a `'static` spawned task).
type SharedBridgeState = Arc<Mutex<BridgeState>>;

/// Run one bridge request off Tauri's async/main thread and bound how long a command
/// will wait for it.
///
/// `PythonBridge::call` is a blocking, synchronous stdio round trip (P1 review on PR
/// #4: a *synchronous* Tauri command runs `call` on Tauri's main thread, so an ordinary
/// discovery - or a bridge that never replies - freezes the window). Moving the blocking
/// call onto a dedicated blocking-pool thread via `spawn_blocking` keeps the async/main
/// thread free regardless of how long the bridge takes; wrapping that in
/// `tokio::time::timeout` additionally guarantees this function itself always resolves
/// within `timeout`, so a stuck bridge is reported as an error instead of leaving the
/// caller (and the UI) waiting forever. The dedicated worker thread can still be left
/// blocked on `read_line` in that case - there is no way to cancel a blocking OS read
/// without also killing the process, which this does not do, so it can reply to a
/// *later* request after an earlier one timed out; this is a documented limitation, not
/// a correctness bug (responses are still matched by id).
async fn call_bridge(
    state: SharedBridgeState,
    method: &'static str,
    params: Value,
    timeout: Duration,
) -> Result<Value, BridgeFailure> {
    let task = tauri::async_runtime::spawn_blocking(move || {
        let guard = state
            .lock()
            .map_err(|_| BridgeFailure::transport("bridge state lock poisoned"))?;
        match &*guard {
            BridgeState::Ready(bridge) => bridge.call(method, params),
            BridgeState::Unavailable(reason) => {
                Err(BridgeFailure::new(CODE_BACKEND_UNAVAILABLE, reason.clone()))
            }
        }
    });

    match tokio::time::timeout(timeout, task).await {
        Ok(Ok(result)) => result,
        Ok(Err(join_error)) => Err(BridgeFailure::transport(format!(
            "bridge worker thread failed: {join_error}"
        ))),
        Err(_timed_out) => Err(BridgeFailure::new(
            CODE_BRIDGE_TIMEOUT,
            format!(
                "the Python control bridge did not respond within {:.0}s",
                timeout.as_secs_f64()
            ),
        )),
    }
}

#[tauri::command]
async fn bridge_ping(state: tauri::State<'_, SharedBridgeState>) -> Result<Value, BridgeFailure> {
    call_bridge(state.inner().clone(), "ping", json!({}), PING_TIMEOUT).await
}

/// Pure request-building logic for `bridge_discover_devices`, factored out so it is
/// testable without a running `tauri::App` to construct a `State` from.
fn discovery_request(timeout_seconds: Option<f64>) -> (Value, Duration) {
    let mut params = serde_json::Map::new();
    let mut timeout = PING_TIMEOUT + DISCOVERY_TIMEOUT_MARGIN;
    if let Some(timeout_seconds) = timeout_seconds {
        params.insert("timeoutSeconds".to_string(), json!(timeout_seconds));
        timeout = Duration::from_secs_f64(timeout_seconds.max(0.0)) + DISCOVERY_TIMEOUT_MARGIN;
    }
    (Value::Object(params), timeout)
}

#[tauri::command]
async fn bridge_discover_devices(
    state: tauri::State<'_, SharedBridgeState>,
    timeout_seconds: Option<f64>,
) -> Result<Value, BridgeFailure> {
    let (params, timeout) = discovery_request(timeout_seconds);
    call_bridge(state.inner().clone(), "discover_devices", params, timeout).await
}

/// The request for one status read: only the stable device id, exactly as discovery
/// reported it. Whether that id is valid, known or reachable is decided by the shared
/// control layer, never here.
fn status_request(device_id: &str) -> Value {
    json!({ "deviceId": device_id })
}

async fn read_status(state: SharedBridgeState, device_id: &str) -> Result<Value, BridgeFailure> {
    call_bridge(
        state,
        "get_status",
        status_request(device_id),
        STATUS_TIMEOUT,
    )
    .await
}

/// Read-only: asks the shared control layer for the selected device's observed status.
/// Sends no playback, volume or mute command.
#[tauri::command]
async fn bridge_get_status(
    state: tauri::State<'_, SharedBridgeState>,
    device_id: String,
) -> Result<Value, BridgeFailure> {
    read_status(state.inner().clone(), &device_id).await
}

/// The request for a seek: the stable device id and the requested position in seconds. Whether
/// the position is valid, or the media can be seeked at all, is decided by the control layer.
fn seek_request(device_id: &str, position_seconds: f64) -> Value {
    json!({ "deviceId": device_id, "positionSeconds": position_seconds })
}

/// Forwards `play`, `pause` or `stop` for one device. A plain forward: no retry (a timeout
/// leaves delivery ambiguous, so resending is the operator's decision) and no state of its
/// own. The answer is data: `ok` means the command was sent, `confirmation` says whether the
/// TV then showed the result.
async fn send_transport(
    state: SharedBridgeState,
    method: &'static str,
    device_id: &str,
) -> Result<Value, BridgeFailure> {
    call_bridge(state, method, status_request(device_id), COMMAND_TIMEOUT).await
}

async fn send_seek(
    state: SharedBridgeState,
    device_id: &str,
    position_seconds: f64,
) -> Result<Value, BridgeFailure> {
    call_bridge(
        state,
        "seek",
        seek_request(device_id, position_seconds),
        COMMAND_TIMEOUT,
    )
    .await
}

#[tauri::command]
async fn bridge_play(
    state: tauri::State<'_, SharedBridgeState>,
    device_id: String,
) -> Result<Value, BridgeFailure> {
    send_transport(state.inner().clone(), "play", &device_id).await
}

#[tauri::command]
async fn bridge_pause(
    state: tauri::State<'_, SharedBridgeState>,
    device_id: String,
) -> Result<Value, BridgeFailure> {
    send_transport(state.inner().clone(), "pause", &device_id).await
}

#[tauri::command]
async fn bridge_stop(
    state: tauri::State<'_, SharedBridgeState>,
    device_id: String,
) -> Result<Value, BridgeFailure> {
    send_transport(state.inner().clone(), "stop", &device_id).await
}

#[tauri::command]
async fn bridge_seek(
    state: tauri::State<'_, SharedBridgeState>,
    device_id: String,
    position_seconds: f64,
) -> Result<Value, BridgeFailure> {
    send_seek(state.inner().clone(), &device_id, position_seconds).await
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
            app.manage(Arc::new(Mutex::new(state)) as SharedBridgeState);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            bridge_ping,
            bridge_discover_devices,
            bridge_get_status,
            bridge_play,
            bridge_pause,
            bridge_stop,
            bridge_seek
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
    fn keeps_the_bridge_error_code_and_message_separate() {
        let error = parse_response(
            r#"{"id":1,"ok":false,"error":{"code":"invalid_argument","message":"bad timeout"}}"#,
        )
        .unwrap_err();

        assert_eq!(error, BridgeFailure::new("invalid_argument", "bad timeout"));
    }

    #[test]
    fn rejects_malformed_json() {
        let error = parse_response("not json").unwrap_err();

        assert_eq!(error.code, CODE_BRIDGE_TRANSPORT);
        assert!(
            error.message.contains("malformed bridge response"),
            "{error:?}"
        );
    }

    #[test]
    fn a_missing_ok_field_is_treated_as_failure_not_success() {
        let error = parse_response(r#"{"id":1}"#).unwrap_err();

        assert_eq!(error.code, CODE_BRIDGE_TRANSPORT);
        assert!(error.message.contains("unexpected shape"), "{error:?}");
    }

    #[test]
    fn a_failure_serializes_as_the_code_and_message_object_the_frontend_reads() {
        let failure = BridgeFailure::new("device_unavailable", "tv is asleep");

        assert_eq!(
            serde_json::to_value(&failure).unwrap(),
            json!({"code": "device_unavailable", "message": "tv is asleep"})
        );
    }

    #[test]
    fn status_request_carries_only_the_stable_device_id() {
        assert_eq!(status_request("uuid-1"), json!({"deviceId": "uuid-1"}));
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

    /// The real bridge, real `ControlService`, real `PyChromecastTransport`: a device that
    /// was never discovered is rejected before any network I/O, so this proves the Rust
    /// -> Python `get_status` path and its error code end to end without a Chromecast.
    #[test]
    fn the_real_bridge_answers_get_status_for_an_undiscovered_device_with_its_code() {
        let python = resolve_python();
        if !python.exists() {
            eprintln!(
                "skipping: {} not found - run `python3 scripts/dev.py setup`",
                python.display()
            );
            return;
        }

        let bridge = PythonBridge::spawn(&python).expect("failed to spawn the bridge process");
        let error = bridge
            .call("get_status", status_request("never-discovered"))
            .unwrap_err();

        assert_eq!(error.code, "device_not_found");
    }

    // --- P1 review (PR #4): bridge calls must not run on the async/main thread ---------

    /// Writes a fake "python" - a plain shell script - that `PythonBridge::spawn` can
    /// launch in place of the real interpreter, so these tests control exactly how and
    /// when (or whether) it responds, without a real Python process or network access.
    /// A counter, not a timestamp: guarantees a unique filename per call within this test
    /// binary regardless of the system clock's actual resolution.
    static SCRIPT_COUNTER: AtomicU64 = AtomicU64::new(0);

    /// Held while writing and spawning a fake script, to keep concurrent test threads
    /// from forking+exec'ing at the exact same moment (observed, on this environment, to
    /// intermittently trip a spurious "text file busy" even on distinct, never-reused
    /// paths - not a production concern: `PythonBridge` itself is spawned exactly once,
    /// from one thread, in `run()`'s `setup`). `unwrap_or_else` recovers from a poisoned
    /// lock: one test's unrelated panic must not cascade into failing every other test
    /// that merely spawns a script after it.
    static PROCESS_SPAWN_LOCK: Mutex<()> = Mutex::new(());

    /// Retries on "text file busy" (a fresh path, so this is the same environment race
    /// `PROCESS_SPAWN_LOCK` targets, not a real conflict) rather than failing the test.
    fn spawn_fake_bridge(body: &str) -> (PathBuf, PythonBridge) {
        for attempt in 0.. {
            let n = SCRIPT_COUNTER.fetch_add(1, Ordering::SeqCst);
            let path = std::env::temp_dir().join(format!(
                "control-tv-fake-bridge-{}-{n}.sh",
                std::process::id()
            ));
            std::fs::write(&path, format!("#!/bin/sh\n{body}\n"))
                .expect("write fake bridge script");
            let mut perms = std::fs::metadata(&path).unwrap().permissions();
            std::os::unix::fs::PermissionsExt::set_mode(&mut perms, 0o755);
            std::fs::set_permissions(&path, perms).expect("chmod fake bridge script");

            match PythonBridge::spawn(&path) {
                Ok(bridge) => return (path, bridge),
                Err(error) if error.contains("Text file busy") && attempt < 5 => {
                    let _ = std::fs::remove_file(&path);
                    std::thread::sleep(Duration::from_millis(20));
                }
                Err(error) => panic!("failed to spawn the fake bridge: {error}"),
            }
        }
        unreachable!()
    }

    /// Deletes the backing script file on drop, once the caller no longer needs it.
    ///
    /// Unlike a compiled binary, a `#!/bin/sh` script is not mapped by the kernel at
    /// `execve` time: the *interpreter* (`/bin/sh`) opens and reads the script file
    /// itself, some unpredictable time after `Command::spawn` returns to us. Deleting
    /// the file right after `spawn` (as an earlier version of this helper did) is a real,
    /// reproducible race - `sh` can lose the open() to the unlink and fail with "No such
    /// file". Keeping this guard alive for the whole test avoids it; only its `Drop`
    /// deletes the file, well after the fake bridge has read and acted on it.
    struct FakeScript(PathBuf);

    impl Drop for FakeScript {
        fn drop(&mut self) {
            let _ = std::fs::remove_file(&self.0);
        }
    }

    /// `read request` first, exactly like the real bridge reads one line before acting on
    /// it: without this, a script that responds/exits fast can close its stdin pipe
    /// before our write reaches it, racing a "broken pipe" instead of exercising `body`.
    fn ready_state(body: &str) -> (SharedBridgeState, FakeScript) {
        let guard = PROCESS_SPAWN_LOCK
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let (script, bridge) = spawn_fake_bridge(&format!("read request\n{body}"));
        drop(guard);
        (
            Arc::new(Mutex::new(BridgeState::Ready(bridge))),
            FakeScript(script),
        )
    }

    #[test]
    fn call_bridge_succeeds_through_the_async_path() {
        let (state, _script) =
            ready_state(r#"echo '{"id":1,"ok":true,"result":{"status":"ready"}}'"#);

        let result = tauri::async_runtime::block_on(call_bridge(
            state,
            "ping",
            json!({}),
            Duration::from_secs(2),
        ));

        assert_eq!(result.unwrap(), json!({"status": "ready"}));
    }

    #[test]
    fn call_bridge_reports_a_process_that_exits_without_responding() {
        // Exits immediately, writing nothing: `call` sees EOF on the first read.
        let (state, _script) = ready_state("exit 0");

        let error = tauri::async_runtime::block_on(call_bridge(
            state,
            "ping",
            json!({}),
            Duration::from_secs(2),
        ))
        .unwrap_err();

        assert_eq!(error.code, CODE_BRIDGE_TRANSPORT);
        assert!(error.message.contains("exited unexpectedly"), "{error:?}");
    }

    #[test]
    fn call_bridge_times_out_instead_of_hanging_forever_on_a_stuck_bridge() {
        // Never writes a response line: a real hung/deadlocked bridge, deterministically.
        let (state, _script) = ready_state("sleep 30");

        let started = std::time::Instant::now();
        let error = tauri::async_runtime::block_on(call_bridge(
            state,
            "ping",
            json!({}),
            Duration::from_millis(200),
        ))
        .unwrap_err();

        // The call returns close to the 200ms bound, not after the script's 30s sleep.
        assert!(
            started.elapsed() < Duration::from_secs(5),
            "{:?}",
            started.elapsed()
        );
        assert_eq!(error.code, CODE_BRIDGE_TIMEOUT);
        assert!(
            error.message.contains("did not respond within"),
            "{error:?}"
        );
    }

    #[test]
    fn call_bridge_reports_an_unavailable_backend_with_its_own_code() {
        let state: SharedBridgeState = Arc::new(Mutex::new(BridgeState::Unavailable(
            "failed to spawn the Python control bridge".to_string(),
        )));

        let error = tauri::async_runtime::block_on(read_status(state, "uuid-1")).unwrap_err();

        assert_eq!(
            error,
            BridgeFailure::new(
                CODE_BACKEND_UNAVAILABLE,
                "failed to spawn the Python control bridge"
            )
        );
    }

    #[test]
    fn read_status_sends_a_get_status_request_with_the_stable_device_id() {
        // Echoes the request line it received back inside the result, so the test sees the
        // exact wire request the command produced.
        let (state, _script) =
            ready_state(r#"printf '{"id":1,"ok":true,"result":{"received":%s}}\n' "$request""#);

        let result = tauri::async_runtime::block_on(read_status(state, "uuid-1")).unwrap();

        assert_eq!(result["received"]["method"], "get_status");
        assert_eq!(result["received"]["params"], json!({"deviceId": "uuid-1"}));
    }

    #[test]
    fn read_status_returns_the_bridge_status_result_unchanged() {
        let (state, _script) = ready_state(
            r#"echo '{"id":1,"ok":true,"result":{"status":{"deviceId":"uuid-1","connection":"connected","receiver":null,"media":null}}}'"#,
        );

        let result = tauri::async_runtime::block_on(read_status(state, "uuid-1")).unwrap();

        assert_eq!(result["status"]["connection"], "connected");
        assert_eq!(result["status"]["receiver"], Value::Null);
    }

    #[test]
    fn read_status_relays_a_device_error_code_untouched() {
        let (state, _script) = ready_state(
            r#"echo '{"id":1,"ok":false,"error":{"code":"device_unavailable","message":"tv is asleep"}}'"#,
        );

        let error = tauri::async_runtime::block_on(read_status(state, "uuid-1")).unwrap_err();

        assert_eq!(
            error,
            BridgeFailure::new("device_unavailable", "tv is asleep")
        );
    }

    // --- playback commands: plain forwards, sent is not confirmed ------------------------

    #[test]
    fn a_seek_request_carries_the_device_id_and_a_numeric_position() {
        assert_eq!(
            seek_request("uuid-1", 42.5),
            json!({"deviceId": "uuid-1", "positionSeconds": 42.5})
        );
    }

    #[test]
    fn the_command_timeout_leaves_room_for_the_control_layers_worst_case() {
        // Snapshot and confirmation share 5s; delivery may wait for a connection with one
        // bounded recovery (about 35s). A tighter bound would report a working command as stuck.
        assert!(COMMAND_TIMEOUT >= Duration::from_secs(45));
        assert!(COMMAND_TIMEOUT > STATUS_TIMEOUT);
    }

    #[test]
    fn each_transport_command_sends_its_own_method_with_only_the_stable_device_id() {
        for method in ["play", "pause", "stop"] {
            // Echoes the request line it received back inside the result.
            let (state, _script) =
                ready_state(r#"printf '{"id":1,"ok":true,"result":{"received":%s}}\n' "$request""#);

            let result =
                tauri::async_runtime::block_on(send_transport(state, method, "uuid-1")).unwrap();

            assert_eq!(result["received"]["method"], method);
            assert_eq!(result["received"]["params"], json!({"deviceId": "uuid-1"}));
        }
    }

    #[test]
    fn seek_sends_the_position_as_a_number_next_to_the_device_id() {
        let (state, _script) =
            ready_state(r#"printf '{"id":1,"ok":true,"result":{"received":%s}}\n' "$request""#);

        let result = tauri::async_runtime::block_on(send_seek(state, "uuid-1", 30.5)).unwrap();

        assert_eq!(result["received"]["method"], "seek");
        assert_eq!(
            result["received"]["params"],
            json!({"deviceId": "uuid-1", "positionSeconds": 30.5})
        );
    }

    #[test]
    fn a_sent_but_unconfirmed_command_is_relayed_as_data_not_turned_into_an_error() {
        let (state, _script) = ready_state(
            r#"echo '{"id":1,"ok":true,"result":{"result":{"command":"pause","deviceId":"uuid-1","confirmation":"unconfirmed","detail":"command sent; expected playback paused, but playback is playing","observed":null}}}'"#,
        );

        let result =
            tauri::async_runtime::block_on(send_transport(state, "pause", "uuid-1")).unwrap();

        assert_eq!(result["result"]["confirmation"], "unconfirmed");
        assert_eq!(result["result"]["command"], "pause");
    }

    #[test]
    fn a_command_error_code_is_relayed_untouched() {
        for code in [
            "command_rejected",
            "unsupported_operation",
            "device_unavailable",
            "timeout",
        ] {
            let body = format!(
                r#"echo '{{"id":1,"ok":false,"error":{{"code":"{code}","message":"m"}}}}'"#
            );
            let (state, _script) = ready_state(&body);

            let error = tauri::async_runtime::block_on(send_transport(state, "play", "uuid-1"))
                .unwrap_err();

            assert_eq!(error, BridgeFailure::new(code, "m"));
        }
    }

    #[test]
    fn commands_report_an_unavailable_backend_with_its_own_code() {
        let state: SharedBridgeState = Arc::new(Mutex::new(BridgeState::Unavailable(
            "failed to spawn the Python control bridge".to_string(),
        )));

        let error = tauri::async_runtime::block_on(send_seek(state, "uuid-1", 5.0)).unwrap_err();

        assert_eq!(error.code, CODE_BACKEND_UNAVAILABLE);
    }

    /// The P1 review on PR #4 again, for commands: a bridge call that takes a while must not
    /// run on the async thread. On a single-threaded runtime an inline blocking read would
    /// starve every other task; here a spawned task must get to run while the command waits.
    #[test]
    fn a_slow_command_does_not_block_the_async_thread() {
        let (state, _script) = ready_state(
            r#"sleep 1
echo '{"id":1,"ok":true,"result":{"result":{"command":"pause","confirmation":"confirmed"}}}'"#,
        );
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_time()
            .build()
            .unwrap();
        let ticked = Arc::new(Mutex::new(None::<std::time::Instant>));
        let ticker = Arc::clone(&ticked);
        runtime.spawn(async move {
            tokio::time::sleep(Duration::from_millis(50)).await;
            *ticker.lock().unwrap() = Some(std::time::Instant::now());
        });

        let result = runtime.block_on(send_transport(state, "pause", "uuid-1"));
        let finished = std::time::Instant::now();

        assert_eq!(result.unwrap()["result"]["confirmation"], "confirmed");
        let tick = ticked
            .lock()
            .unwrap()
            .expect("the async thread was blocked");
        assert!(tick < finished, "the other task only ran after the command");
    }

    #[test]
    fn discovery_request_defaults_when_no_timeout_is_given() {
        let (params, timeout) = discovery_request(None);

        assert_eq!(params, json!({}));
        assert_eq!(timeout, PING_TIMEOUT + DISCOVERY_TIMEOUT_MARGIN);
    }

    #[test]
    fn discovery_request_forwards_and_bounds_a_caller_supplied_timeout() {
        let (params, timeout) = discovery_request(Some(30.0));

        assert_eq!(params, json!({"timeoutSeconds": 30.0}));
        assert_eq!(timeout, Duration::from_secs(35));
    }

    #[test]
    fn discovery_request_clamps_a_negative_timeout_to_zero() {
        let (_, timeout) = discovery_request(Some(-10.0));

        assert_eq!(timeout, DISCOVERY_TIMEOUT_MARGIN);
    }
}
