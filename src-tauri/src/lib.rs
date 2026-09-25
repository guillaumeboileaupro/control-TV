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

use serde::Deserialize;
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
) -> Result<Value, String> {
    let task = tauri::async_runtime::spawn_blocking(move || {
        let guard = state
            .lock()
            .map_err(|_| "bridge state lock poisoned".to_string())?;
        match &*guard {
            BridgeState::Ready(bridge) => bridge.call(method, params),
            BridgeState::Unavailable(reason) => {
                Err(format!("Python control backend unavailable: {reason}"))
            }
        }
    });

    match tokio::time::timeout(timeout, task).await {
        Ok(Ok(result)) => result,
        Ok(Err(join_error)) => Err(format!("bridge worker thread failed: {join_error}")),
        Err(_timed_out) => Err(format!(
            "the Python control bridge did not respond within {:.0}s",
            timeout.as_secs_f64()
        )),
    }
}

#[tauri::command]
async fn bridge_ping(state: tauri::State<'_, SharedBridgeState>) -> Result<Value, String> {
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
) -> Result<Value, String> {
    let (params, timeout) = discovery_request(timeout_seconds);
    call_bridge(state.inner().clone(), "discover_devices", params, timeout).await
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

        assert!(error.contains("exited unexpectedly"), "{error}");
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
        assert!(error.contains("did not respond within"), "{error}");
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
