//! The one process boundary to the shared Python control layer: the bridge process (or the
//! Android app's embedded Python), its request claim, its recovery and its failures. No
//! Cast/control logic lives here; requests and answers are forwarded as JSON.

use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::atomic::{AtomicU64, AtomicU8, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Duration;
use std::time::Instant;

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
// `app.path()`, used only by the packaged (release) bridge resolution.
#[cfg(not(debug_assertions))]
use tauri::Manager;

use crate::diagnostics::{self, CommandDiagnostic};

/// Codes raised by this crate itself, as opposed to codes relayed from the bridge (which
/// are `ControlError` codes such as `device_unavailable`, or `internal_error`). They let
/// the UI tell "the backend is not there" apart from "the device is not there".
pub(crate) const CODE_BACKEND_UNAVAILABLE: &str = "backend_unavailable";
pub(crate) const CODE_BRIDGE_TIMEOUT: &str = "bridge_timeout";
pub(crate) const CODE_BRIDGE_TRANSPORT: &str = "bridge_transport";
/// A request that timed out before it was written: certainly not sent, so not ambiguous.
pub(crate) const CODE_BRIDGE_BUSY: &str = "bridge_busy";

/// One application request - one order from the user - as the bridge's `requestId`: every
/// message of that order carries it, so the acceptance trace counts every invocation of the
/// order (C7). A new order gets a new id; issuing the same order again must reuse it. Unique
/// from the process id, the clock and a counter, without a dependency; letters, digits and
/// dashes only, at most 64 characters (the bridge's `requestId` format).
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct ApplicationRequestId(String);

impl ApplicationRequestId {
    pub(crate) fn new() -> Self {
        static NEXT: AtomicU64 = AtomicU64::new(0);
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0, |elapsed| elapsed.as_nanos());
        Self(format!(
            "{:x}-{nanos:x}-{:x}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ))
    }

    pub(crate) fn as_str(&self) -> &str {
        &self.0
    }
}

impl Default for ApplicationRequestId {
    fn default() -> Self {
        Self::new()
    }
}

/// The fate of one bridge call, settled once: see `call_bridge`.
pub(crate) const CLAIM_PENDING: u8 = 0;
pub(crate) const CLAIM_STARTED: u8 = 1;
pub(crate) const CLAIM_ABANDONED: u8 = 2;

/// The one process boundary to the shared Python control layer.
///
/// Spawned in `run()`'s `setup` hook and kept until it fails, when `BridgeSlot` replaces
/// it for later requests. Requests are sent and answered sequentially (single-flight, serialized by
/// `stdin`/`stdout` each being behind their own `Mutex`) and matched by a monotonically
/// increasing id; nothing today needs concurrent in-flight requests, so this is
/// deliberately the simplest correct thing, not a protocol limitation.
pub(crate) struct PythonBridge {
    pub(crate) channel: BridgeChannel,
    pub(crate) next_id: AtomicU64,
    pub(crate) process: Arc<BridgeProcess>,
}

/// How one request line reaches the shared Python control layer and its response comes back.
pub(crate) enum BridgeChannel {
    /// The desktop bridge process's stdin and stdout. Dropping `PythonBridge` drops this
    /// channel, closing stdin and sending EOF, which is exactly what makes `bridge.py`'s
    /// `for line in stdin` loop end and the Python process exit on its own - no signal/kill
    /// is needed for a clean shutdown. A bridge found broken is stopped explicitly instead
    /// (`BridgeProcess`).
    #[cfg_attr(target_os = "android", allow(dead_code))] // desktop bridge process only
    Pipes {
        stdin: Mutex<ChildStdin>,
        stdout: Mutex<BufReader<ChildStdout>>,
    },
    /// Python embedded in this process (the Android app): one call takes one request line
    /// and returns one response line, through the same `control_tv.bridge.handle_line`.
    /// There is no process to stop or replace; the request claim and timeouts still apply.
    #[cfg_attr(not(any(target_os = "android", test)), allow(dead_code))]
    InProcess(InProcessCall),
}

/// Runs one request line through the embedded Python bridge and returns its response line.
pub(crate) type InProcessCall = Box<dyn Fn(String) -> Result<String, BridgeFailure> + Send + Sync>;

/// The bridge's operating-system process, shared so that a caller whose request timed out
/// after it was written can stop it without the bridge lock, which the worker still holds
/// while it waits for the reply.
pub(crate) struct BridgeProcess {
    /// `None` for a bridge embedded in this process: nothing to kill or reap.
    pub(crate) child: Option<Mutex<Child>>,
    /// Set by the first `terminate`; the process group is killed only once, while its
    /// leader is known to be ours.
    pub(crate) terminated: std::sync::atomic::AtomicBool,
}

impl BridgeProcess {
    #[cfg_attr(not(any(target_os = "android", test)), allow(dead_code))]
    pub(crate) fn in_process() -> Self {
        Self {
            child: None,
            terminated: std::sync::atomic::AtomicBool::new(false),
        }
    }

    pub(crate) fn lock_child(child: &Mutex<Child>) -> std::sync::MutexGuard<'_, Child> {
        child
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    pub(crate) fn has_exited(&self) -> bool {
        match &self.child {
            Some(child) => !matches!(Self::lock_child(child).try_wait(), Ok(None)),
            None => false,
        }
    }

    /// Kill the process and everything it started (on Unix, its process group), then reap
    /// it, so no zombie is left behind. With no process left holding its pipes, a worker
    /// blocked on them sees end of file and returns. A bridge embedded in this process has
    /// nothing to stop.
    pub(crate) fn terminate(&self) {
        let Some(child) = &self.child else {
            self.terminated.store(true, Ordering::SeqCst);
            return;
        };
        let mut child = Self::lock_child(child);
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
pub(crate) struct BridgeFailure {
    pub(crate) code: String,
    pub(crate) message: String,
}

impl BridgeFailure {
    pub(crate) fn new(code: &str, message: impl Into<String>) -> Self {
        Self {
            code: code.to_string(),
            message: message.into(),
        }
    }

    /// The request or response could not travel to/from the bridge process at all.
    pub(crate) fn transport(message: impl Into<String>) -> Self {
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
#[cfg_attr(target_os = "android", allow(dead_code))] // desktop bridge process only
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct BridgeProgram {
    pub(crate) executable: PathBuf,
    pub(crate) args: Vec<String>,
    /// Withhold the session's Python environment variables (`PYTHON_ENV_WITHHELD`), so they
    /// cannot point the bundled runtime at another Python installation or package.
    pub(crate) isolate_python_env: bool,
}

/// Python variables that could make the frozen bridge import code other than its own.
#[cfg_attr(target_os = "android", allow(dead_code))] // desktop bridge process only
pub(crate) const PYTHON_ENV_WITHHELD: [&str; 2] = ["PYTHONPATH", "PYTHONHOME"];

#[cfg(any(debug_assertions, test))]
#[cfg_attr(target_os = "android", allow(dead_code))] // desktop bridge process only
pub(crate) fn development_bridge_program(
    manifest_dir: &std::path::Path,
    windows: bool,
) -> BridgeProgram {
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
#[cfg_attr(target_os = "android", allow(dead_code))] // desktop bridge process only
pub(crate) fn packaged_bridge_program(
    resource_dir: &std::path::Path,
    windows: bool,
) -> BridgeProgram {
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
#[cfg_attr(target_os = "android", allow(dead_code))] // desktop bridge process only
pub(crate) fn packaged_bridge(
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
#[cfg_attr(target_os = "android", allow(dead_code))] // desktop bridge process only
pub(crate) fn resolve_bridge_program<R: tauri::Runtime>(
    _app: &tauri::AppHandle<R>,
) -> Result<BridgeProgram, String> {
    Ok(development_bridge_program(
        std::path::Path::new(env!("CARGO_MANIFEST_DIR")),
        cfg!(windows),
    ))
}

#[cfg(not(debug_assertions))]
#[cfg_attr(target_os = "android", allow(dead_code))] // desktop bridge process only
pub(crate) fn resolve_bridge_program<R: tauri::Runtime>(
    app: &tauri::AppHandle<R>,
) -> Result<BridgeProgram, String> {
    let resource_dir = app
        .path()
        .resource_dir()
        .map_err(|error| format!("failed to resolve the application resource directory: {error}"));
    packaged_bridge(resource_dir, cfg!(windows))
}

impl PythonBridge {
    #[cfg_attr(target_os = "android", allow(dead_code))] // desktop bridge process only
    pub(crate) fn command(program: &BridgeProgram) -> Command {
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

    #[cfg_attr(target_os = "android", allow(dead_code))] // desktop bridge process only
    pub(crate) fn spawn(program: &BridgeProgram) -> Result<Self, String> {
        Self::spawn_command(Self::command(program), &program.executable)
    }

    /// Spawn a bridge from a prepared `command` (`executable` names it in errors).
    #[cfg_attr(target_os = "android", allow(dead_code))] // desktop bridge process only
    pub(crate) fn spawn_command(
        mut command: Command,
        executable: &std::path::Path,
    ) -> Result<Self, String> {
        let mut child = command.spawn().map_err(|error| {
            format!(
                "failed to spawn the Python control bridge ({}): {error}",
                executable.display()
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
            channel: BridgeChannel::Pipes {
                stdin: Mutex::new(stdin),
                stdout: Mutex::new(BufReader::new(stdout)),
            },
            next_id: AtomicU64::new(1),
            process: Arc::new(BridgeProcess {
                child: Some(Mutex::new(child)),
                terminated: std::sync::atomic::AtomicBool::new(false),
            }),
        })
    }

    /// A bridge to Python embedded in this process (the Android app).
    #[cfg_attr(not(any(target_os = "android", test)), allow(dead_code))]
    pub(crate) fn in_process(call: InProcessCall) -> Self {
        Self {
            channel: BridgeChannel::InProcess(call),
            next_id: AtomicU64::new(1),
            process: Arc::new(BridgeProcess::in_process()),
        }
    }

    /// One request as a new order of its own.
    #[cfg(test)]
    pub(crate) fn call(&self, method: &str, params: Value) -> Result<Value, BridgeFailure> {
        self.call_as(&ApplicationRequestId::new(), method, params)
    }

    /// Send one `{method, params}` request of `order` and return its result, or the bridge's
    /// own `{code, message}` error, or a `bridge_transport` failure when the request or its
    /// response could not travel to/from the process at all. Every message of one order
    /// carries the same `requestId`; nothing here sends a message again.
    pub(crate) fn call_as(
        &self,
        order: &ApplicationRequestId,
        method: &str,
        params: Value,
    ) -> Result<Value, BridgeFailure> {
        let id = self.next_id.fetch_add(1, Ordering::SeqCst);
        let request = json!({
            "id": id,
            "method": method,
            "params": params,
            "requestId": order.as_str(),
        });
        let line = serde_json::to_string(&request).map_err(|error| {
            BridgeFailure::transport(format!("failed to encode bridge request: {error}"))
        })?;

        let (stdin, stdout) = match &self.channel {
            BridgeChannel::Pipes { stdin, stdout } => (stdin, stdout),
            BridgeChannel::InProcess(call) => return parse_response(&call(line)?, id),
        };
        {
            let mut stdin = stdin
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
            let mut stdout = stdout
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

        parse_response(&response_line, id)
    }
}

/// Pure parsing/translation of one response line - factored out of `call` so it is
/// testable without spawning a real process.
pub(crate) fn parse_response(line: &str, expected_id: u64) -> Result<Value, BridgeFailure> {
    let response: Value = serde_json::from_str(line.trim()).map_err(|error| {
        BridgeFailure::transport(format!("received a malformed bridge response: {error}"))
    })?;

    let response_id = response.get("id").and_then(Value::as_u64);
    if response_id != Some(expected_id) {
        return Err(BridgeFailure::transport(format!(
            "bridge response id does not match request: expected {expected_id}, received {}",
            response
                .get("id")
                .map_or_else(|| "no id".to_string(), Value::to_string)
        )));
    }

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
pub(crate) enum BridgeState {
    Ready(PythonBridge),
    Unavailable(String),
}

/// Starts a bridge process: the same launch `run()` uses at startup.
pub(crate) type BridgeLauncher = Box<dyn Fn() -> Result<PythonBridge, String> + Send>;

/// The bridge and how to start a replacement for it.
///
/// Recovery replaces the process, never a request: a bridge found dead, broken or stuck is
/// stopped and marked unavailable, and the next request that reaches it starts a new one
/// before anything of that request is written. The request that met the failure keeps its
/// own result (`bridge_transport` or `bridge_timeout`, both "may have been delivered") and
/// is never sent again.
pub(crate) struct BridgeSlot {
    pub(crate) state: BridgeState,
    /// `None` only where no replacement may be started (some tests).
    pub(crate) launcher: Option<BridgeLauncher>,
}

impl BridgeSlot {
    pub(crate) fn new(launcher: BridgeLauncher) -> Self {
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

    /// Stop the current process, if there is one, and leave the slot unavailable for `reason`.
    pub(crate) fn retire(&mut self, reason: String) {
        if let BridgeState::Ready(bridge) = &self.state {
            bridge.process.terminate();
        }
        eprintln!("control-tv: Python control bridge stopped: {reason}");
        self.state = BridgeState::Unavailable(reason);
    }

    /// Before a request is written: replace a process that has exited, and start one where
    /// none runs. Nothing is sent to the new process here.
    pub(crate) fn recover(&mut self) {
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
pub(crate) type SharedBridgeState = Arc<Mutex<BridgeSlot>>;

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
pub(crate) async fn call_bridge(
    state: SharedBridgeState,
    method: &'static str,
    params: Value,
    timeout: Duration,
) -> Result<Value, BridgeFailure> {
    // This call is one order: its id is made once, here, and travels with the request.
    let order = ApplicationRequestId::new();
    let started = Instant::now();
    diagnostics::record(CommandDiagnostic::new(
        order.as_str(),
        "command_received",
        method,
        None,
        None,
    ));
    let claim = Arc::new(AtomicU8::new(CLAIM_PENDING));
    let worker_claim = Arc::clone(&claim);
    let worker_order = order.clone();
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
        diagnostics::record(CommandDiagnostic::new(
            worker_order.as_str(),
            "bridge_dispatched",
            method,
            Some(started.elapsed()),
            None,
        ));
        let result = bridge.call_as(&worker_order, method, params);
        if let Err(failure) = &result {
            if failure.code == CODE_BRIDGE_TRANSPORT {
                slot.retire(failure.message.clone());
            }
        }
        result
    });

    let result = match tokio::time::timeout(timeout, task).await {
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
    };
    let (phase, error_code) = match &result {
        Ok(_) => ("command_completed", None),
        Err(failure) if failure.code == CODE_BRIDGE_TIMEOUT || failure.code == CODE_BRIDGE_BUSY => {
            ("command_timed_out", Some(failure.code.as_str()))
        }
        Err(failure) => ("command_failed", Some(failure.code.as_str())),
    };
    diagnostics::record(CommandDiagnostic::new(
        order.as_str(),
        phase,
        method,
        Some(started.elapsed()),
        error_code,
    ));
    result
}

/// The failure of an embedded (Android) bridge call. The Android side rejects with
/// `backend_unavailable` only when the embedded Python could not start, before a request
/// reached the shared control layer (certainly not sent); every other rejection may have come
/// after the request was handled, so it is the ambiguous `bridge_transport`.
#[cfg_attr(not(any(target_os = "android", test)), allow(dead_code))]
pub(crate) fn embedded_rejection(code: Option<&str>, message: Option<&str>) -> BridgeFailure {
    let message = message
        .unwrap_or("the embedded Python control bridge failed")
        .to_string();
    match code {
        Some(CODE_BACKEND_UNAVAILABLE) => BridgeFailure::new(CODE_BACKEND_UNAVAILABLE, message),
        _ => BridgeFailure::transport(message),
    }
}
