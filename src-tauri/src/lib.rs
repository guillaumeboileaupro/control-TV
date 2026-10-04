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
use std::sync::atomic::{AtomicU64, AtomicU8, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
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
/// A request that timed out before it was written: certainly not sent, so not ambiguous.
const CODE_BRIDGE_BUSY: &str = "bridge_busy";

/// The fate of one bridge call, settled once: see `call_bridge`.
const CLAIM_PENDING: u8 = 0;
const CLAIM_STARTED: u8 = 1;
const CLAIM_ABANDONED: u8 = 2;

/// The one process boundary to the shared Python control layer.
///
/// Spawned in `run()`'s `setup` hook and kept until it fails, when `BridgeSlot` replaces
/// it for later requests. Requests are sent and answered sequentially (single-flight, serialized by
/// `stdin`/`stdout` each being behind their own `Mutex`) and matched by a monotonically
/// increasing id; nothing today needs concurrent in-flight requests, so this is
/// deliberately the simplest correct thing, not a protocol limitation.
struct PythonBridge {
    /// Dropping `PythonBridge` drops `stdin` first (field order), closing that pipe and
    /// sending EOF, which is exactly what makes `bridge.py`'s `for line in stdin` loop end
    /// and the Python process exit on its own - no signal/kill is needed for a clean
    /// shutdown. A bridge found broken is stopped explicitly instead (`BridgeProcess`).
    stdin: Mutex<ChildStdin>,
    stdout: Mutex<BufReader<ChildStdout>>,
    next_id: AtomicU64,
    process: Arc<BridgeProcess>,
}

/// The bridge's operating-system process, shared so that a caller whose request timed out
/// after it was written can stop it without the bridge lock, which the worker still holds
/// while it waits for the reply.
struct BridgeProcess {
    child: Mutex<Child>,
    /// Set by the first `terminate`; the process group is killed only once, while its
    /// leader is known to be ours.
    terminated: std::sync::atomic::AtomicBool,
}

impl BridgeProcess {
    fn lock_child(&self) -> std::sync::MutexGuard<'_, Child> {
        self.child
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    fn has_exited(&self) -> bool {
        !matches!(self.lock_child().try_wait(), Ok(None))
    }

    /// Kill the process and everything it started (on Unix, its process group), then reap
    /// it, so no zombie is left behind. With no process left holding its pipes, a worker
    /// blocked on them sees end of file and returns.
    fn terminate(&self) {
        let mut child = self.lock_child();
        if !self.terminated.swap(true, Ordering::SeqCst) {
            #[cfg(unix)]
            if let Ok(group) = i32::try_from(child.id()) {
                // SAFETY: plain kill(2) on the group this bridge leads (`process_group(0)`).
                unsafe {
                    libc::kill(-group, libc::SIGKILL);
                }
            }
        }
        let _ = child.kill();
        let _ = child.wait();
    }
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

/// How `run` starts the bridge process.
///
/// A debug build runs the shared package from the repository's `uv`-managed `.venv`
/// (`python -m control_tv.bridge`), so development needs no packaging step. A release build
/// runs only the frozen bridge bundled as the `python-bridge/` resource
/// (`dev.py bridge-build`), which carries its own Python runtime: there is no fallback to a
/// `.venv`, to `python`/`python3`, to `PATH` or to `PYTHONPATH`, and a missing bundle leaves
/// the backend unavailable instead of picking up another Python.
#[derive(Debug, Clone, PartialEq, Eq)]
struct BridgeProgram {
    executable: PathBuf,
    args: Vec<String>,
    /// Withhold the session's Python environment variables (`PYTHON_ENV_WITHHELD`), so they
    /// cannot point the bundled runtime at another Python installation or package.
    isolate_python_env: bool,
}

/// Python variables that could make the frozen bridge import code other than its own.
const PYTHON_ENV_WITHHELD: [&str; 2] = ["PYTHONPATH", "PYTHONHOME"];

#[cfg(any(debug_assertions, test))]
fn development_bridge_program(manifest_dir: &std::path::Path, windows: bool) -> BridgeProgram {
    let repo_root = manifest_dir
        .parent()
        .expect("src-tauri always has a parent directory");
    let executable = if windows {
        repo_root.join(".venv").join("Scripts").join("python.exe")
    } else {
        repo_root.join(".venv").join("bin").join("python")
    };
    BridgeProgram {
        executable,
        args: vec!["-m".to_string(), "control_tv.bridge".to_string()],
        isolate_python_env: false,
    }
}

#[cfg(any(not(debug_assertions), test))]
fn packaged_bridge_program(resource_dir: &std::path::Path, windows: bool) -> BridgeProgram {
    let executable = if windows {
        "control-tv-bridge.exe"
    } else {
        "control-tv-bridge"
    };
    BridgeProgram {
        executable: resource_dir.join("python-bridge").join(executable),
        args: Vec::new(),
        isolate_python_env: true,
    }
}

/// The bundled bridge under `resource_dir`, or why it cannot be used. Never another Python.
#[cfg(any(not(debug_assertions), test))]
fn packaged_bridge(
    resource_dir: Result<PathBuf, String>,
    windows: bool,
) -> Result<BridgeProgram, String> {
    let program = packaged_bridge_program(&resource_dir?, windows);
    if !program.executable.is_file() {
        return Err(format!(
            "the bundled Python control bridge is missing ({})",
            program.executable.display()
        ));
    }
    Ok(program)
}

#[cfg(debug_assertions)]
fn resolve_bridge_program<R: tauri::Runtime>(
    _app: &tauri::AppHandle<R>,
) -> Result<BridgeProgram, String> {
    Ok(development_bridge_program(
        std::path::Path::new(env!("CARGO_MANIFEST_DIR")),
        cfg!(windows),
    ))
}

#[cfg(not(debug_assertions))]
fn resolve_bridge_program<R: tauri::Runtime>(
    app: &tauri::AppHandle<R>,
) -> Result<BridgeProgram, String> {
    let resource_dir = app
        .path()
        .resource_dir()
        .map_err(|error| format!("failed to resolve the application resource directory: {error}"));
    packaged_bridge(resource_dir, cfg!(windows))
}

impl PythonBridge {
    fn command(program: &BridgeProgram) -> Command {
        let mut command = Command::new(&program.executable);
        // Its own process group, so recovery can stop everything it started.
        #[cfg(unix)]
        std::os::unix::process::CommandExt::process_group(&mut command, 0);
        command
            .args(&program.args)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit());
        if program.isolate_python_env {
            for variable in PYTHON_ENV_WITHHELD {
                command.env_remove(variable);
            }
        }
        command
    }

    fn spawn(program: &BridgeProgram) -> Result<Self, String> {
        let mut child = Self::command(program).spawn().map_err(|error| {
            format!(
                "failed to spawn the Python control bridge ({}): {error}",
                program.executable.display()
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
            process: Arc::new(BridgeProcess {
                child: Mutex::new(child),
                terminated: std::sync::atomic::AtomicBool::new(false),
            }),
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

/// Starts a bridge process: the same launch `run()` uses at startup.
type BridgeLauncher = Box<dyn Fn() -> Result<PythonBridge, String> + Send>;

/// The bridge and how to start a replacement for it.
///
/// Recovery replaces the process, never a request: a bridge found dead, broken or stuck is
/// stopped and marked unavailable, and the next request that reaches it starts a new one
/// before anything of that request is written. The request that met the failure keeps its
/// own result (`bridge_transport` or `bridge_timeout`, both "may have been delivered") and
/// is never sent again.
struct BridgeSlot {
    state: BridgeState,
    /// `None` only where no replacement may be started (some tests).
    launcher: Option<BridgeLauncher>,
}

impl BridgeSlot {
    fn new(launcher: BridgeLauncher) -> Self {
        let state = match launcher() {
            Ok(bridge) => BridgeState::Ready(bridge),
            Err(error) => {
                eprintln!("control-tv: Python control bridge unavailable: {error}");
                BridgeState::Unavailable(error)
            }
        };
        Self {
            state,
            launcher: Some(launcher),
        }
    }

    /// Stop the current process, if any, and leave the slot unavailable for `reason`.
    fn retire(&mut self, reason: String) {
        if let BridgeState::Ready(bridge) = &self.state {
            bridge.process.terminate();
        }
        eprintln!("control-tv: Python control bridge stopped: {reason}");
        self.state = BridgeState::Unavailable(reason);
    }

    /// Before a request is written: replace a process that has exited, and start one where
    /// none runs. Nothing is sent to the new process here.
    fn recover(&mut self) {
        if let BridgeState::Ready(bridge) = &self.state {
            if !bridge.process.has_exited() {
                return;
            }
            self.retire("the Python control bridge process exited".to_string());
        }
        if let Some(launcher) = &self.launcher {
            self.state = match launcher() {
                Ok(bridge) => {
                    eprintln!("control-tv: Python control bridge started again");
                    BridgeState::Ready(bridge)
                }
                Err(error) => BridgeState::Unavailable(error),
            };
        }
    }
}

/// `Arc` so a command can clone a handle to it and move that clone onto the blocking
/// worker thread `call_bridge` spawns, independently of the `'_`-scoped `tauri::State`
/// borrow (which cannot itself cross into a `'static` spawned task).
type SharedBridgeState = Arc<Mutex<BridgeSlot>>;

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
/// caller (and the UI) waiting forever. A blocking OS read cannot be cancelled, so a
/// request that times out after its claim kills its bridge process instead (see below):
/// that ends the read, and the worker returns.
///
/// A request that times out before it was written must never be written afterwards: the
/// caller has already been told it failed, so a late write would deliver a command nobody is
/// waiting for. Each call therefore has a claim, settled exactly once by a compare-and-swap:
/// the worker claims it (`CLAIM_STARTED`) after it holds the bridge and before anything
/// reaches stdin; the timeout claims it (`CLAIM_ABANDONED`). If the timeout wins, the worker
/// gives the bridge back without writing, and the error says the request was not sent; if the
/// worker won, the request was sent and the timeout stays ambiguous.
///
/// Recovery (`BridgeSlot`) only ever acts on the process: before a still-pending request
/// is claimed, a dead process is replaced; a request that ends in `bridge_transport`
/// retires its process; a request that times out after its claim stops the process it was
/// written to, which also frees a worker stuck reading from it. The next request then
/// starts a new process. No request is retried or replayed.
async fn call_bridge(
    state: SharedBridgeState,
    method: &'static str,
    params: Value,
    timeout: Duration,
) -> Result<Value, BridgeFailure> {
    let claim = Arc::new(AtomicU8::new(CLAIM_PENDING));
    let worker_claim = Arc::clone(&claim);
    // The process this request is written to, set before the claim, so a caller that loses
    // the claim race to the worker can stop that process (and only that one).
    let written_to: Arc<OnceLock<Arc<BridgeProcess>>> = Arc::new(OnceLock::new());
    let worker_written_to = Arc::clone(&written_to);
    let task = tauri::async_runtime::spawn_blocking(move || {
        let mut slot = match state.lock() {
            Ok(slot) => slot,
            Err(poisoned) => {
                // A worker panicked while it used the bridge, so its pipes may be mid-message.
                state.clear_poison();
                let mut slot = poisoned.into_inner();
                slot.retire("a request failed while it used the bridge".to_string());
                slot
            }
        };
        if worker_claim.load(Ordering::SeqCst) == CLAIM_PENDING {
            slot.recover();
        }
        let bridge = match &slot.state {
            BridgeState::Ready(bridge) => bridge,
            BridgeState::Unavailable(reason) => {
                return Err(BridgeFailure::new(CODE_BACKEND_UNAVAILABLE, reason.clone()));
            }
        };
        let _ = worker_written_to.set(Arc::clone(&bridge.process));
        if worker_claim
            .compare_exchange(
                CLAIM_PENDING,
                CLAIM_STARTED,
                Ordering::SeqCst,
                Ordering::SeqCst,
            )
            .is_err()
        {
            // The caller gave up while this request waited for the bridge.
            return Err(BridgeFailure::new(
                CODE_BRIDGE_BUSY,
                "the request was abandoned before it was sent",
            ));
        }
        let result = bridge.call(method, params);
        if let Err(failure) = &result {
            if failure.code == CODE_BRIDGE_TRANSPORT {
                slot.retire(failure.message.clone());
            }
        }
        result
    });

    match tokio::time::timeout(timeout, task).await {
        Ok(Ok(result)) => result,
        Ok(Err(join_error)) => Err(BridgeFailure::transport(format!(
            "bridge worker thread failed: {join_error}"
        ))),
        Err(_timed_out) => {
            let never_sent = claim
                .compare_exchange(
                    CLAIM_PENDING,
                    CLAIM_ABANDONED,
                    Ordering::SeqCst,
                    Ordering::SeqCst,
                )
                .is_ok();
            if never_sent {
                Err(BridgeFailure::new(
                    CODE_BRIDGE_BUSY,
                    format!(
                        "the request was not sent: the Python control bridge was still busy \
                         after {:.0}s",
                        timeout.as_secs_f64()
                    ),
                ))
            } else {
                // The request was written, or about to be: its result stays ambiguous. Its
                // process is stopped so that a stuck bridge cannot hold every later request.
                if let Some(process) = written_to.get() {
                    let process = Arc::clone(process);
                    tauri::async_runtime::spawn_blocking(move || process.terminate());
                }
                Err(BridgeFailure::new(
                    CODE_BRIDGE_TIMEOUT,
                    format!(
                        "the Python control bridge did not respond within {:.0}s",
                        timeout.as_secs_f64()
                    ),
                ))
            }
        }
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

/// The request for a volume change: the stable device id and the requested level from 0.0 to
/// 1.0. Whether the level is valid is decided by the control layer, never here.
fn volume_request(device_id: &str, level: f64) -> Value {
    json!({ "deviceId": device_id, "level": level })
}

/// The request for a mute change. `muted` is the state to reach (`true` mutes, `false` unmutes),
/// never a toggle: the same request sent twice ends in the same state.
fn muted_request(device_id: &str, muted: bool) -> Value {
    json!({ "deviceId": device_id, "muted": muted })
}

/// Forwards a volume change. A plain forward like the playback commands: no retry, no state of
/// its own, and the answer is data (`ok` means sent, `confirmation` says whether the TV showed it).
async fn send_set_volume(
    state: SharedBridgeState,
    device_id: &str,
    level: f64,
) -> Result<Value, BridgeFailure> {
    call_bridge(
        state,
        "set_volume",
        volume_request(device_id, level),
        COMMAND_TIMEOUT,
    )
    .await
}

async fn send_set_muted(
    state: SharedBridgeState,
    device_id: &str,
    muted: bool,
) -> Result<Value, BridgeFailure> {
    call_bridge(
        state,
        "set_muted",
        muted_request(device_id, muted),
        COMMAND_TIMEOUT,
    )
    .await
}

#[tauri::command]
async fn bridge_set_volume(
    state: tauri::State<'_, SharedBridgeState>,
    device_id: String,
    level: f64,
) -> Result<Value, BridgeFailure> {
    send_set_volume(state.inner().clone(), &device_id, level).await
}

#[tauri::command]
async fn bridge_set_muted(
    state: tauri::State<'_, SharedBridgeState>,
    device_id: String,
    muted: bool,
) -> Result<Value, BridgeFailure> {
    send_set_muted(state.inner().clone(), &device_id, muted).await
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            // Every launch, the first and every recovery, resolves the bridge program again:
            // a release build can only ever start its bundled bridge, never another Python.
            let handle = app.handle().clone();
            let slot = BridgeSlot::new(Box::new(move || {
                resolve_bridge_program(&handle).and_then(|program| PythonBridge::spawn(&program))
            }));
            app.manage(Arc::new(Mutex::new(slot)) as SharedBridgeState);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            bridge_ping,
            bridge_discover_devices,
            bridge_get_status,
            bridge_play,
            bridge_pause,
            bridge_stop,
            bridge_seek,
            bridge_set_volume,
            bridge_set_muted
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

    /// The development bridge `run()` starts in a debug build (and these tests spawn).
    fn development_program() -> BridgeProgram {
        development_bridge_program(
            std::path::Path::new(env!("CARGO_MANIFEST_DIR")),
            cfg!(windows),
        )
    }

    fn resolve_python() -> PathBuf {
        development_program().executable
    }

    #[test]
    fn bridge_programs_keep_debug_and_release_strictly_separate() {
        let debug = development_bridge_program(std::path::Path::new("/checkout/src-tauri"), false);
        assert_eq!(
            debug.executable,
            PathBuf::from("/checkout/.venv/bin/python")
        );
        assert_eq!(debug.args, ["-m", "control_tv.bridge"]);
        assert!(!debug.isolate_python_env);
        let release = packaged_bridge_program(std::path::Path::new("/installed/resources"), false);
        assert_eq!(
            release.executable,
            PathBuf::from("/installed/resources/python-bridge/control-tv-bridge")
        );
        assert!(release.args.is_empty());
        assert!(release.isolate_python_env);
    }

    #[test]
    fn windows_release_uses_only_the_bundled_executable() {
        let release = packaged_bridge_program(std::path::Path::new(r"C:\resources"), true);
        assert_eq!(
            release.executable,
            PathBuf::from(r"C:\resources")
                .join("python-bridge")
                .join("control-tv-bridge.exe")
        );
        assert!(release.args.is_empty());
    }

    fn scratch_dir(prefix: &str) -> PathBuf {
        let n = SCRIPT_COUNTER.fetch_add(1, Ordering::SeqCst);
        let dir =
            std::env::temp_dir().join(format!("control-tv-{prefix}-{}-{n}", std::process::id()));
        std::fs::create_dir_all(&dir).expect("create scratch directory");
        dir
    }

    #[test]
    fn a_release_without_its_bundled_bridge_never_falls_back_to_another_python() {
        // This checkout has a working `.venv`, and `python3` is on PATH: neither may be used.
        let resources = scratch_dir("no-bundle");

        let error = packaged_bridge(Ok(resources.clone()), false).unwrap_err();

        assert!(
            error.contains("bundled Python control bridge is missing"),
            "{error}"
        );
        assert!(
            error.contains(&resources.join("python-bridge").display().to_string()),
            "{error}"
        );
        let _ = std::fs::remove_dir_all(&resources);
    }

    #[test]
    fn a_release_without_a_resource_directory_reports_why() {
        let error = packaged_bridge(Err("no resource directory".to_string()), false).unwrap_err();

        assert_eq!(error, "no resource directory");
    }

    #[test]
    fn a_missing_bundled_bridge_leaves_the_backend_unavailable() {
        let resources = scratch_dir("unavailable");
        let bundle_dir = resources.clone();
        let state: SharedBridgeState = Arc::new(Mutex::new(BridgeSlot::new(Box::new(move || {
            packaged_bridge(Ok(bundle_dir.clone()), false)
                .and_then(|program| PythonBridge::spawn(&program))
        }))));

        let error = tauri::async_runtime::block_on(call_bridge(
            state,
            "ping",
            json!({}),
            Duration::from_secs(5),
        ))
        .unwrap_err();

        assert_eq!(error.code, CODE_BACKEND_UNAVAILABLE);
        assert!(error
            .message
            .contains("bundled Python control bridge is missing"));
        let _ = std::fs::remove_dir_all(&resources);
    }

    #[test]
    fn a_present_bundled_bridge_is_the_one_started() {
        let resources = scratch_dir("bundle");
        let bundle = resources.join("python-bridge");
        std::fs::create_dir_all(&bundle).unwrap();
        std::fs::write(bundle.join("control-tv-bridge"), "").unwrap();

        let program = packaged_bridge(Ok(resources.clone()), false).unwrap();

        assert_eq!(program, packaged_bridge_program(&resources, false));
        let _ = std::fs::remove_dir_all(&resources);
    }

    #[test]
    fn the_bundled_bridge_never_inherits_the_sessions_python_path() {
        let packaged = PythonBridge::command(&packaged_bridge_program(
            std::path::Path::new("/installed/resources"),
            false,
        ));
        let mut removed: Vec<_> = packaged
            .get_envs()
            .filter(|(_, value)| value.is_none())
            .map(|(key, _)| key.to_string_lossy().into_owned())
            .collect();
        removed.sort();
        let mut withheld = PYTHON_ENV_WITHHELD.to_vec();
        withheld.sort();
        assert_eq!(removed, withheld);

        let development = PythonBridge::command(&development_program());
        assert_eq!(development.get_envs().count(), 0);
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

        let bridge = PythonBridge::spawn(&development_program())
            .expect("failed to spawn the bridge process");
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

        let bridge = PythonBridge::spawn(&development_program())
            .expect("failed to spawn the bridge process");
        let error = bridge
            .call("get_status", status_request("never-discovered"))
            .unwrap_err();

        assert_eq!(error.code, "device_not_found");
    }

    /// A bridge state that is never replaced: the behavior these tests pin does not involve
    /// recovery (see the recovery tests for that).
    fn fixed_slot(state: BridgeState) -> SharedBridgeState {
        Arc::new(Mutex::new(BridgeSlot {
            state,
            launcher: None,
        }))
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

            let program = BridgeProgram {
                executable: path.clone(),
                args: Vec::new(),
                isolate_python_env: false,
            };
            match PythonBridge::spawn(&program) {
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
        (fixed_slot(BridgeState::Ready(bridge)), FakeScript(script))
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

    /// A fake bridge that appends every request line it receives to `log`, waits `slow_ms`
    /// before answering a request for the method "slow", and answers everything else at once.
    fn recording_state(log: &std::path::Path, slow_ms: u64) -> (SharedBridgeState, FakeScript) {
        let slow = slow_ms as f64 / 1000.0;
        let guard = PROCESS_SPAWN_LOCK
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let (script, bridge) = spawn_fake_bridge(&format!(
            r#"while IFS= read -r line; do
  printf '%s\n' "$line" >> '{log}'
  case "$line" in *'"method":"slow"'*) sleep {slow} ;; esac
  echo '{{"ok":true,"result":{{}}}}'
done"#,
            log = log.display()
        ));
        drop(guard);
        (fixed_slot(BridgeState::Ready(bridge)), FakeScript(script))
    }

    fn logged_methods(log: &std::path::Path) -> Vec<String> {
        std::fs::read_to_string(log)
            .unwrap_or_default()
            .lines()
            .map(|line| {
                serde_json::from_str::<Value>(line).unwrap()["method"]
                    .as_str()
                    .unwrap()
                    .to_string()
            })
            .collect()
    }

    #[test]
    fn a_request_that_times_out_while_waiting_for_the_bridge_is_never_sent() {
        let n = SCRIPT_COUNTER.fetch_add(1, Ordering::SeqCst);
        let log =
            std::env::temp_dir().join(format!("control-tv-sent-{}-{n}.log", std::process::id()));
        let (state, _script) = recording_state(&log, 1000);

        // A holds the bridge for about one second.
        let a_state = Arc::clone(&state);
        let a = std::thread::spawn(move || {
            tauri::async_runtime::block_on(call_bridge(
                a_state,
                "slow",
                json!({}),
                Duration::from_secs(10),
            ))
        });
        std::thread::sleep(Duration::from_millis(200));

        // B gives up while A still holds the bridge.
        let b = tauri::async_runtime::block_on(call_bridge(
            Arc::clone(&state),
            "pause",
            json!({"deviceId": "uuid-1"}),
            Duration::from_millis(200),
        ))
        .unwrap_err();

        // A finishes and releases the bridge; B's worker then gets the lock, but must not send.
        assert!(a.join().unwrap().is_ok());
        std::thread::sleep(Duration::from_millis(500));
        assert_eq!(logged_methods(&log), vec!["slow"]);
        assert_eq!(b.code, CODE_BRIDGE_BUSY);
        assert!(b.message.contains("was not sent"), "{b:?}");

        // The bridge is still usable: the next request is sent and answered.
        tauri::async_runtime::block_on(call_bridge(
            Arc::clone(&state),
            "ping",
            json!({}),
            Duration::from_secs(5),
        ))
        .unwrap();
        assert_eq!(logged_methods(&log), vec!["slow", "ping"]);
        let _ = std::fs::remove_file(&log);
    }

    #[test]
    fn a_request_already_sent_when_it_times_out_stays_ambiguous() {
        let n = SCRIPT_COUNTER.fetch_add(1, Ordering::SeqCst);
        let log =
            std::env::temp_dir().join(format!("control-tv-started-{}-{n}.log", std::process::id()));
        let (state, _script) = recording_state(&log, 1000);

        let error = tauri::async_runtime::block_on(call_bridge(
            Arc::clone(&state),
            "slow",
            json!({}),
            Duration::from_millis(200),
        ))
        .unwrap_err();

        // It reached the bridge, so the timeout cannot say it was not sent.
        assert_eq!(error.code, CODE_BRIDGE_TIMEOUT);
        assert!(
            error.message.contains("did not respond within"),
            "{error:?}"
        );
        // Nothing re-sends it. Its process was stopped, and this bridge has no way to start
        // another (see the recovery tests), so the next request is not written anywhere.
        let next = tauri::async_runtime::block_on(call_bridge(
            Arc::clone(&state),
            "ping",
            json!({}),
            Duration::from_secs(5),
        ))
        .unwrap_err();
        assert_eq!(next.code, CODE_BACKEND_UNAVAILABLE);
        std::thread::sleep(Duration::from_millis(1200));
        assert_eq!(logged_methods(&log), vec!["slow"]);
        let _ = std::fs::remove_file(&log);
    }

    fn temp_log(prefix: &str) -> PathBuf {
        let n = SCRIPT_COUNTER.fetch_add(1, Ordering::SeqCst);
        std::env::temp_dir().join(format!(
            "control-tv-{prefix}-{}-{n}.log",
            std::process::id()
        ))
    }

    /// Runs `slow` on the bridge for about a second on another thread, after which the bridge
    /// is held: the caller then sends requests that have to wait for it.
    fn hold_the_bridge(
        state: &SharedBridgeState,
    ) -> std::thread::JoinHandle<Result<Value, BridgeFailure>> {
        let busy = Arc::clone(state);
        let holder = std::thread::spawn(move || {
            tauri::async_runtime::block_on(call_bridge(
                busy,
                "slow",
                json!({}),
                Duration::from_secs(10),
            ))
        });
        std::thread::sleep(Duration::from_millis(200));
        holder
    }

    #[test]
    fn a_startup_ping_that_gives_up_behind_a_long_request_is_never_sent() {
        let log = temp_log("ping");
        let (state, _script) = recording_state(&log, 1000);
        let holder = hold_the_bridge(&state);

        let ping = tauri::async_runtime::block_on(call_bridge(
            Arc::clone(&state),
            "ping",
            json!({}),
            Duration::from_millis(200),
        ))
        .unwrap_err();

        assert!(holder.join().unwrap().is_ok());
        std::thread::sleep(Duration::from_millis(500));
        assert_eq!(logged_methods(&log), vec!["slow"]);
        // Busy, not broken: the UI can tell the service is alive (no false outage banner).
        assert_eq!(ping.code, CODE_BRIDGE_BUSY);
        let _ = std::fs::remove_file(&log);
    }

    #[test]
    fn several_abandoned_requests_are_never_sent_and_the_next_one_is_sent_once() {
        let log = temp_log("many");
        let (state, _script) = recording_state(&log, 1000);
        let holder = hold_the_bridge(&state);

        let waiting: Vec<_> = ["pause", "stop", "seek"]
            .into_iter()
            .map(|method| {
                let state = Arc::clone(&state);
                std::thread::spawn(move || {
                    tauri::async_runtime::block_on(call_bridge(
                        state,
                        method,
                        json!({"deviceId": "uuid-1"}),
                        Duration::from_millis(200),
                    ))
                })
            })
            .collect();
        for request in waiting {
            assert_eq!(request.join().unwrap().unwrap_err().code, CODE_BRIDGE_BUSY);
        }

        assert!(holder.join().unwrap().is_ok());
        std::thread::sleep(Duration::from_millis(500));
        tauri::async_runtime::block_on(call_bridge(
            Arc::clone(&state),
            "play",
            json!({"deviceId": "uuid-1"}),
            Duration::from_secs(5),
        ))
        .unwrap();
        assert_eq!(logged_methods(&log), vec!["slow", "play"]);
        let _ = std::fs::remove_file(&log);
    }

    #[test]
    fn call_bridge_reports_an_unavailable_backend_with_its_own_code() {
        let state: SharedBridgeState = fixed_slot(BridgeState::Unavailable(
            "failed to spawn the Python control bridge".to_string(),
        ));

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
    fn read_status_relays_the_media_details_untouched_and_keeps_null_as_null() {
        let (state, _script) = ready_state(
            r#"echo '{"id":1,"ok":true,"result":{"status":{"deviceId":"uuid-1","connection":"connected","receiver":null,"media":{"playbackState":"paused","contentId":"","title":"A video","artist":"A channel","streamType":"buffered","metadataType":"generic","positionSeconds":12.5,"durationSeconds":90.0,"supportsSeek":true,"supportsPause":null}}}}'"#,
        );

        let result = tauri::async_runtime::block_on(read_status(state, "uuid-1")).unwrap();

        let media = &result["status"]["media"];
        assert_eq!(media["artist"], "A channel");
        assert_eq!(media["streamType"], "buffered");
        assert_eq!(media["metadataType"], "generic");
        assert_eq!(media["supportsSeek"], true);
        assert_eq!(media["supportsPause"], Value::Null);
        assert_eq!(media["contentId"], "");
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
        let state: SharedBridgeState = fixed_slot(BridgeState::Unavailable(
            "failed to spawn the Python control bridge".to_string(),
        ));

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

    // --- volume and mute: plain forwards, sent is not confirmed --------------------------

    #[test]
    fn a_volume_request_carries_the_device_id_and_a_numeric_level() {
        assert_eq!(
            volume_request("uuid-1", 0.45),
            json!({"deviceId": "uuid-1", "level": 0.45})
        );
        assert!(volume_request("uuid-1", 0.0)["level"].is_number());
    }

    #[test]
    fn a_mute_request_carries_a_json_boolean_never_a_toggle_or_a_string() {
        for muted in [true, false] {
            let request = muted_request("uuid-1", muted);

            assert_eq!(request, json!({"deviceId": "uuid-1", "muted": muted}));
            assert!(request["muted"].is_boolean());
        }
    }

    #[test]
    fn a_level_that_is_not_a_finite_number_reaches_the_bridge_as_null_so_it_is_refused() {
        // serde_json writes NaN and infinity as null; the control layer then refuses the request.
        assert!(volume_request("uuid-1", f64::NAN)["level"].is_null());
        assert!(volume_request("uuid-1", f64::INFINITY)["level"].is_null());
    }

    #[test]
    fn set_volume_sends_its_own_method_with_the_level_as_a_number_next_to_the_device_id() {
        let (state, _script) =
            ready_state(r#"printf '{"id":1,"ok":true,"result":{"received":%s}}\n' "$request""#);

        let result =
            tauri::async_runtime::block_on(send_set_volume(state, "uuid-1", 0.35)).unwrap();

        assert_eq!(result["received"]["method"], "set_volume");
        assert_eq!(
            result["received"]["params"],
            json!({"deviceId": "uuid-1", "level": 0.35})
        );
    }

    #[test]
    fn set_muted_sends_its_own_method_with_the_state_as_a_boolean() {
        for muted in [true, false] {
            let (state, _script) =
                ready_state(r#"printf '{"id":1,"ok":true,"result":{"received":%s}}\n' "$request""#);

            let result =
                tauri::async_runtime::block_on(send_set_muted(state, "uuid-1", muted)).unwrap();

            assert_eq!(result["received"]["method"], "set_muted");
            assert_eq!(
                result["received"]["params"],
                json!({"deviceId": "uuid-1", "muted": muted})
            );
        }
    }

    #[test]
    fn a_volume_or_mute_that_is_sent_but_unconfirmed_is_relayed_as_data_not_an_error() {
        let (state, _script) = ready_state(
            r#"echo '{"id":1,"ok":true,"result":{"result":{"command":"set_volume","deviceId":"uuid-1","confirmation":"unconfirmed","detail":"command sent; expected volume near 0.5, but volume is 0.47","observed":null}}}'"#,
        );

        let result = tauri::async_runtime::block_on(send_set_volume(state, "uuid-1", 0.5)).unwrap();

        assert_eq!(result["result"]["confirmation"], "unconfirmed");
        assert_eq!(result["result"]["command"], "set_volume");

        let (state, _script) = ready_state(
            r#"echo '{"id":1,"ok":true,"result":{"result":{"command":"set_muted","deviceId":"uuid-1","confirmation":"not_checked","detail":null,"observed":null}}}'"#,
        );

        let result = tauri::async_runtime::block_on(send_set_muted(state, "uuid-1", true)).unwrap();

        assert_eq!(result["result"]["confirmation"], "not_checked");
    }

    #[test]
    fn a_sound_command_error_code_is_relayed_untouched() {
        for code in [
            "command_rejected",
            "invalid_argument",
            "device_unavailable",
            "device_not_found",
            "timeout",
        ] {
            let body = format!(
                r#"echo '{{"id":1,"ok":false,"error":{{"code":"{code}","message":"m"}}}}'"#
            );
            let (state, _script) = ready_state(&body);
            let volume =
                tauri::async_runtime::block_on(send_set_volume(state, "uuid-1", 0.5)).unwrap_err();
            let (state, _script) = ready_state(&body);
            let mute =
                tauri::async_runtime::block_on(send_set_muted(state, "uuid-1", true)).unwrap_err();

            assert_eq!(volume, BridgeFailure::new(code, "m"));
            assert_eq!(mute, BridgeFailure::new(code, "m"));
        }
    }

    #[test]
    fn sound_commands_report_an_unavailable_backend_with_its_own_code() {
        let unavailable = || -> SharedBridgeState {
            fixed_slot(BridgeState::Unavailable(
                "failed to spawn the Python control bridge".to_string(),
            ))
        };

        let volume = tauri::async_runtime::block_on(send_set_volume(unavailable(), "uuid-1", 0.5))
            .unwrap_err();
        let mute = tauri::async_runtime::block_on(send_set_muted(unavailable(), "uuid-1", false))
            .unwrap_err();

        assert_eq!(volume.code, CODE_BACKEND_UNAVAILABLE);
        assert_eq!(mute.code, CODE_BACKEND_UNAVAILABLE);
    }

    #[test]
    fn a_stuck_bridge_times_out_a_sound_command_instead_of_hanging() {
        let (state, _script) = ready_state("sleep 30");

        let started = std::time::Instant::now();
        let error = tauri::async_runtime::block_on(call_bridge(
            state,
            "set_volume",
            volume_request("uuid-1", 0.5),
            Duration::from_millis(200),
        ))
        .unwrap_err();

        assert!(
            started.elapsed() < Duration::from_secs(5),
            "{:?}",
            started.elapsed()
        );
        assert_eq!(error.code, CODE_BRIDGE_TIMEOUT);
    }

    /// The P1 review on PR #4 once more, for sound commands: a slow bridge call runs on a
    /// worker thread, so a task spawned on a single-threaded runtime still gets to run.
    #[test]
    fn a_slow_volume_change_does_not_block_the_async_thread() {
        let (state, _script) = ready_state(
            r#"sleep 1
echo '{"id":1,"ok":true,"result":{"result":{"command":"set_volume","confirmation":"confirmed"}}}'"#,
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

        let result = runtime.block_on(send_set_volume(state, "uuid-1", 0.5));
        let finished = std::time::Instant::now();

        assert_eq!(result.unwrap()["result"]["confirmation"], "confirmed");
        let tick = ticked
            .lock()
            .unwrap()
            .expect("the async thread was blocked");
        assert!(tick < finished, "the other task only ran after the command");
    }

    /// The real bridge, real `ControlService`, real `PyChromecastTransport`: an id that was
    /// never discovered is refused before any network I/O. Reaching `device_not_found` (and
    /// not `invalid_argument`) proves the method names and the `level`/`muted` parameter
    /// names this shell sends are the ones the bridge accepts, without a Chromecast.
    #[test]
    fn the_real_bridge_refuses_volume_and_mute_for_an_undiscovered_device_with_its_code() {
        let python = resolve_python();
        if !python.exists() {
            eprintln!(
                "skipping: {} not found - run `python3 scripts/dev.py setup`",
                python.display()
            );
            return;
        }

        let bridge = PythonBridge::spawn(&development_program())
            .expect("failed to spawn the bridge process");
        let volume = bridge
            .call("set_volume", volume_request("never-discovered", 0.4))
            .unwrap_err();
        let mute = bridge
            .call("set_muted", muted_request("never-discovered", true))
            .unwrap_err();
        let unmute = bridge
            .call("set_muted", muted_request("never-discovered", false))
            .unwrap_err();

        assert_eq!(volume.code, "device_not_found");
        assert_eq!(mute.code, "device_not_found");
        assert_eq!(unmute.code, "device_not_found");
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

    // --- Bridge recovery: the process is replaced, a request never is ----------------------

    /// A fake bridge started by a launcher, as `run()` starts the real one. Every instance
    /// takes the next number (`$n`), records its pid, and logs each request line it reads as
    /// "<instance> <method>", so a test can tell which process received what, and how often.
    struct Relaunching {
        state: SharedBridgeState,
        script: FakeScript,
        dir: PathBuf,
    }

    impl Relaunching {
        fn log(&self) -> Vec<String> {
            std::fs::read_to_string(self.dir.join("log"))
                .unwrap_or_default()
                .lines()
                .map(|line| {
                    let (instance, request) = line.split_once(' ').unwrap();
                    let method = serde_json::from_str::<Value>(request).unwrap()["method"]
                        .as_str()
                        .unwrap()
                        .to_string();
                    format!("{instance} {method}")
                })
                .collect()
        }

        fn instances(&self) -> usize {
            self.pids().len()
        }

        fn pids(&self) -> Vec<u32> {
            std::fs::read_to_string(self.dir.join("pids"))
                .unwrap_or_default()
                .lines()
                .map(|pid| pid.parse().unwrap())
                .collect()
        }

        fn call(&self, method: &'static str, timeout: Duration) -> Result<Value, BridgeFailure> {
            tauri::async_runtime::block_on(call_bridge(
                Arc::clone(&self.state),
                method,
                json!({"deviceId": "uuid-1"}),
                timeout,
            ))
        }

        fn wait_until(&self, what: &str, done: impl Fn() -> bool) {
            let started = std::time::Instant::now();
            while !done() {
                assert!(
                    started.elapsed() < Duration::from_secs(10),
                    "timed out: {what}"
                );
                std::thread::sleep(Duration::from_millis(10));
            }
        }
    }

    impl Drop for Relaunching {
        fn drop(&mut self) {
            for pid in self.pids() {
                let _ = Command::new("kill").arg("-9").arg(pid.to_string()).status();
            }
            let _ = std::fs::remove_dir_all(&self.dir);
            let _ = &self.script;
        }
    }

    /// The answer every healthy instance gives, after logging the request.
    const ANSWER: &str =
        r#"printf '%s %s\n' "$n" "$line" >> "$dir/log"; echo '{"ok":true,"result":{}}'"#;

    /// `first` is what instance 1 does; every later instance logs and answers each request.
    fn relaunching(first: &str) -> Relaunching {
        let dir = std::env::temp_dir().join(format!(
            "control-tv-recovery-{}-{}",
            std::process::id(),
            SCRIPT_COUNTER.fetch_add(1, Ordering::SeqCst)
        ));
        std::fs::create_dir_all(&dir).unwrap();
        let body = format!(
            r#"dir='{dir}'
n=$(( $(cat "$dir/count" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$dir/count"
echo $$ >> "$dir/pids"
if [ "$n" = 1 ]; then
{first}
fi
while IFS= read -r line; do {ANSWER}; done"#,
            dir = dir.display()
        );
        let guard = PROCESS_SPAWN_LOCK
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let (path, first_bridge) = spawn_fake_bridge(&body);
        drop(guard);
        let launch_path = path.clone();
        let launcher: BridgeLauncher = Box::new(move || {
            PythonBridge::spawn(&BridgeProgram {
                executable: launch_path.clone(),
                args: Vec::new(),
                isolate_python_env: false,
            })
        });
        let relaunching = Relaunching {
            state: Arc::new(Mutex::new(BridgeSlot {
                state: BridgeState::Ready(first_bridge),
                launcher: Some(launcher),
            })),
            script: FakeScript(path),
            dir,
        };
        relaunching.wait_until("instance 1 started", || relaunching.instances() == 1);
        relaunching
    }

    /// Gone from the process table: killed and reaped, so not even a zombie remains.
    fn reaped(pid: u32) -> bool {
        !std::path::Path::new(&format!("/proc/{pid}")).exists()
    }

    #[test]
    fn a_bridge_that_died_before_a_request_is_replaced_before_anything_is_written() {
        let bridge = relaunching("exit 0");
        let first = bridge.pids()[0];
        bridge.wait_until("instance 1 exited", || {
            let slot = bridge.state.lock().unwrap();
            matches!(&slot.state, BridgeState::Ready(b) if b.process.has_exited())
        });

        bridge.call("ping", Duration::from_secs(5)).unwrap();

        assert_eq!(bridge.log(), ["2 ping"]);
        assert_eq!(bridge.instances(), 2);
        assert!(reaped(first));
    }

    #[test]
    fn a_bridge_that_dies_during_a_request_fails_it_once_and_serves_the_next_one() {
        let bridge = relaunching(
            r#"IFS= read -r line; printf '%s %s\n' "$n" "$line" >> "$dir/log"; exit 3"#,
        );
        let first = bridge.pids()[0];

        let failure = bridge.call("pause", Duration::from_secs(5)).unwrap_err();

        // It reached the process: maybe delivered, never reported as not sent.
        assert_eq!(failure.code, CODE_BRIDGE_TRANSPORT);
        bridge.call("ping", Duration::from_secs(5)).unwrap();
        assert_eq!(bridge.log(), ["1 pause", "2 ping"]);
        assert!(reaped(first));
    }

    #[test]
    fn a_broken_pipe_retires_the_process_and_the_next_request_gets_a_new_one() {
        // Instance 1 closes its input but stays alive: the write fails with a broken pipe.
        let bridge = relaunching("exec 0<&-; sleep 30");
        let first = bridge.pids()[0];
        std::thread::sleep(Duration::from_millis(100));

        let failure = bridge.call("pause", Duration::from_secs(5)).unwrap_err();

        assert_eq!(failure.code, CODE_BRIDGE_TRANSPORT);
        assert!(failure.message.contains("failed to write"), "{failure:?}");
        assert!(reaped(first), "the live process was not stopped and reaped");
        bridge.call("ping", Duration::from_secs(5)).unwrap();
        assert_eq!(bridge.log(), ["2 ping"]);
    }

    #[test]
    fn a_timeout_after_the_claim_stops_the_stuck_process_without_resending_the_request() {
        // Instance 1 reads the request and never answers.
        let bridge = relaunching(
            r#"IFS= read -r line; printf '%s %s\n' "$n" "$line" >> "$dir/log"; sleep 30"#,
        );
        let first = bridge.pids()[0];

        let failure = bridge
            .call("pause", Duration::from_millis(300))
            .unwrap_err();

        assert_eq!(failure.code, CODE_BRIDGE_TIMEOUT);
        bridge.wait_until("instance 1 reaped", || reaped(first));
        bridge.call("ping", Duration::from_secs(5)).unwrap();
        // The paused request went to instance 1 once and to no other process.
        assert_eq!(bridge.log(), ["1 pause", "2 ping"]);
        assert_eq!(bridge.instances(), 2);
    }

    #[test]
    fn a_request_abandoned_behind_a_stuck_one_is_never_written_even_after_recovery() {
        let bridge = relaunching(
            r#"IFS= read -r line; printf '%s %s\n' "$n" "$line" >> "$dir/log"; sleep 30"#,
        );
        let holder_state = Arc::clone(&bridge.state);
        let holder = std::thread::spawn(move || {
            tauri::async_runtime::block_on(call_bridge(
                holder_state,
                "slow",
                json!({}),
                Duration::from_millis(800),
            ))
        });
        std::thread::sleep(Duration::from_millis(200));

        let waiting = bridge
            .call("pause", Duration::from_millis(200))
            .unwrap_err();

        assert_eq!(waiting.code, CODE_BRIDGE_BUSY);
        assert_eq!(
            holder.join().unwrap().unwrap_err().code,
            CODE_BRIDGE_TIMEOUT
        );
        bridge.call("ping", Duration::from_secs(5)).unwrap();
        // "pause" reached no process, the abandoned worker started none, and nothing repeats.
        assert_eq!(bridge.log(), ["1 slow", "2 ping"]);
        assert_eq!(bridge.instances(), 2);
    }

    #[test]
    fn concurrent_requests_after_a_crash_start_one_replacement_and_each_is_written_once() {
        let bridge = relaunching("exit 0");
        bridge.wait_until("instance 1 exited", || {
            let slot = bridge.state.lock().unwrap();
            matches!(&slot.state, BridgeState::Ready(b) if b.process.has_exited())
        });

        let requests: Vec<_> = ["play", "pause", "stop"]
            .into_iter()
            .map(|method| {
                let state = Arc::clone(&bridge.state);
                std::thread::spawn(move || {
                    tauri::async_runtime::block_on(call_bridge(
                        state,
                        method,
                        json!({}),
                        Duration::from_secs(10),
                    ))
                })
            })
            .collect();
        for request in requests {
            request.join().unwrap().unwrap();
        }

        let mut log = bridge.log();
        log.sort();
        assert_eq!(log, ["2 pause", "2 play", "2 stop"]);
        assert_eq!(bridge.instances(), 2);
    }

    #[test]
    fn a_poisoned_bridge_lock_retires_the_process_and_the_next_request_recovers() {
        let bridge = relaunching(":");
        let first = bridge.pids()[0];
        let poisoner = Arc::clone(&bridge.state);
        let _ = std::thread::spawn(move || {
            let _held = poisoner.lock().unwrap();
            panic!("a worker panicked while it used the bridge");
        })
        .join();
        assert!(bridge.state.is_poisoned());

        bridge.call("ping", Duration::from_secs(5)).unwrap();

        assert!(!bridge.state.is_poisoned());
        assert!(reaped(first));
        assert_eq!(bridge.log(), ["2 ping"]);
    }

    #[test]
    fn a_bridge_that_could_not_start_is_started_by_a_later_request() {
        let attempts = Arc::new(AtomicU64::new(0));
        let counted = Arc::clone(&attempts);
        let ok_state = ready_state(r#"echo '{"ok":true,"result":{"status":"ready"}}'"#);
        let ready = Arc::new(Mutex::new(Some(ok_state)));
        let launcher: BridgeLauncher = Box::new(move || {
            if counted.fetch_add(1, Ordering::SeqCst) == 0 {
                return Err("failed to spawn the Python control bridge".to_string());
            }
            let (state, script) = ready.lock().unwrap().take().expect("launched twice");
            std::mem::forget(script);
            let mut slot = state.lock().unwrap();
            Ok(std::mem::replace(
                &mut slot.state,
                BridgeState::Unavailable("moved".to_string()),
            ))
            .and_then(|taken| match taken {
                BridgeState::Ready(bridge) => Ok(bridge),
                BridgeState::Unavailable(reason) => Err(reason),
            })
        });
        let state: SharedBridgeState = Arc::new(Mutex::new(BridgeSlot::new(launcher)));
        assert!(matches!(
            state.lock().unwrap().state,
            BridgeState::Unavailable(_)
        ));

        let result = tauri::async_runtime::block_on(call_bridge(
            state,
            "ping",
            json!({}),
            Duration::from_secs(5),
        ))
        .unwrap();

        assert_eq!(result["status"], "ready");
        assert_eq!(attempts.load(Ordering::SeqCst), 2);
    }

    #[test]
    fn a_failed_restart_reports_an_unavailable_backend_and_sends_nothing() {
        let state: SharedBridgeState = Arc::new(Mutex::new(BridgeSlot::new(Box::new(|| {
            Err("failed to spawn the Python control bridge".to_string())
        }))));

        let error = tauri::async_runtime::block_on(call_bridge(
            state,
            "pause",
            json!({}),
            Duration::from_secs(5),
        ))
        .unwrap_err();

        assert_eq!(error.code, CODE_BACKEND_UNAVAILABLE);
    }
}
