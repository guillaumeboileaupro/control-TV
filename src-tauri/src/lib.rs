//! Tauri application shell.
//!
//! This crate owns *no* Cast/control logic: it only spawns the shared Python control
//! layer (`control_tv.bridge`, in the repository's `src/control_tv/` package) as a
//! long-lived child process and forwards requests to it over line-delimited JSON on
//! stdin/stdout. See `src/control_tv/bridge.py` for the wire protocol and the
//! authoritative behavior. `ControlService` is never reimplemented here.

mod bridge;
mod commands;
// A phone has no status area: only the desktop and the tests use this module.
#[cfg_attr(not(any(desktop, test)), allow(dead_code))]
mod tray;

/// The Android app: Python is embedded in the app process (Chaquopy).
#[cfg(target_os = "android")]
mod android;

#[cfg(not(target_os = "android"))]
use std::sync::Arc;
#[cfg(not(target_os = "android"))]
use std::sync::Mutex;
#[cfg(target_os = "android")]
use std::sync::{Arc, Mutex};

use tauri::Manager;

#[cfg(target_os = "android")]
use android::remember_selection;
use bridge::*;
#[cfg(not(target_os = "android"))]
use commands::remember_selection;
#[cfg(test)]
use commands::*;
/// How this build starts the bridge, the first time and on every recovery.
#[cfg(not(target_os = "android"))]
fn bridge_launcher(app: &tauri::App) -> BridgeLauncher {
    // Every launch resolves the bridge program again: a release build can only ever start
    // its bundled bridge, never another Python.
    let handle = app.handle().clone();
    Box::new(move || {
        resolve_bridge_program(&handle).and_then(|program| PythonBridge::spawn(&program))
    })
}

/// How this build starts the bridge: Python embedded in the Android app.
#[cfg(target_os = "android")]
fn bridge_launcher(app: &tauri::App) -> BridgeLauncher {
    let handle = Arc::clone(&app.state::<android::EmbeddedBridge>().0);
    android::launcher(handle)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let builder = tauri::Builder::default();
    #[cfg(target_os = "android")]
    let builder = builder.plugin(android::plugin());
    builder
        .setup(|app| {
            let slot = BridgeSlot::new(bridge_launcher(app));
            app.manage(Arc::new(Mutex::new(slot)) as SharedBridgeState);
            // Without a status area (or its AppIndicator support) the window works as before.
            #[cfg(desktop)]
            if let Err(error) = tray::install(app) {
                eprintln!("control-TV: no status-area icon: {error}");
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::bridge_ping,
            commands::bridge_discover_devices,
            commands::bridge_get_status,
            commands::bridge_play,
            commands::bridge_pause,
            commands::bridge_stop,
            commands::bridge_seek,
            commands::bridge_set_volume,
            commands::bridge_set_muted,
            commands::bridge_get_remote_status,
            commands::bridge_start_remote_pairing,
            commands::bridge_finish_remote_pairing,
            commands::bridge_press_remote_key,
            commands::bridge_type_remote_text,
            commands::bridge_list_tv_apps,
            commands::bridge_launch_tv_app,
            commands::show_main_window,
            remember_selection
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;
    use std::process::Command;
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::sync::{Arc, Mutex};
    use std::time::Duration;

    use serde_json::{json, Value};

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
    /// was never discovered is rejected before a network I/O, so this proves the Rust
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

    // --- the TV's own remote channel: plain forwards ---------------------------------------

    #[test]
    fn a_remote_key_request_carries_the_device_id_and_the_key_name_as_typed() {
        assert_eq!(
            remote_key_request("uuid-1", "volume_up"),
            json!({"deviceId": "uuid-1", "key": "volume_up"})
        );
    }

    #[test]
    fn a_text_request_carries_the_text_exactly_as_typed() {
        assert_eq!(
            remote_text_request("uuid-1", "héllo wörld"),
            json!({"deviceId": "uuid-1", "text": "héllo wörld"})
        );
    }

    // --- the status-area icon (tray): pure mapping, no desktop needed ----------------------

    #[test]
    fn tray_items_mean_open_quit_or_one_window_action() {
        use tray::{event_for, TrayEvent};

        assert_eq!(event_for("open"), TrayEvent::Open);
        assert_eq!(event_for("quit"), TrayEvent::Quit);
        for (id, _) in tray::ACTIONS {
            assert_eq!(event_for(id), TrayEvent::Action(id));
        }
        assert_eq!(event_for("set_volume"), TrayEvent::Unrecognized);
        assert_eq!(event_for(""), TrayEvent::Unrecognized);
    }

    #[test]
    fn tray_actions_are_the_quick_controls_and_never_a_level_or_a_free_command() {
        let ids: Vec<&str> = tray::ACTIONS.iter().map(|(id, _)| *id).collect();

        assert_eq!(
            ids,
            [
                "play_pause",
                "stop",
                "tv_mute",
                "tv_volume_down",
                "tv_volume_up"
            ]
        );
        assert_eq!(tray::EVENT, "tray-action");
    }

    #[test]
    fn an_app_request_carries_the_package_name_as_given() {
        assert_eq!(
            app_request("uuid-1", "com.example.video"),
            json!({"deviceId": "uuid-1", "package": "com.example.video"})
        );
    }

    #[test]
    fn the_app_list_bound_covers_the_owner_allowing_control_tv_on_the_tv() {
        // socket 5 + two 5s handshake reads + allow 30 + the launch's 15s budget, with a margin
        assert!(APPS_TIMEOUT >= Duration::from_secs(5 + 5 + 5 + 30 + 15) + Duration::from_secs(10));
    }

    #[test]
    fn a_pairing_code_request_carries_the_code_as_typed() {
        assert_eq!(
            pairing_code_request("uuid-1", " a1b2c3 "),
            json!({"deviceId": "uuid-1", "code": " a1b2c3 "})
        );
    }

    #[test]
    fn the_remote_bound_leaves_room_for_the_control_layers_own_8s_bound() {
        assert!(REMOTE_TIMEOUT >= Duration::from_secs(16));
        assert!(REMOTE_TIMEOUT < COMMAND_TIMEOUT);
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
    /// never discovered is refused before a network I/O. Reaching `device_not_found` (and
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
    fn the_real_bridge_refuses_the_remote_channel_for_a_tv_it_never_discovered() {
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
        let key = bridge
            .call(
                "press_remote_key",
                remote_key_request("never-discovered", "volume_up"),
            )
            .unwrap_err();
        let status = bridge
            .call("get_remote_status", status_request("never-discovered"))
            .unwrap_err();
        let pairing = bridge
            .call(
                "finish_remote_pairing",
                pairing_code_request("never-discovered", "A1B2C3"),
            )
            .unwrap_err();

        assert_eq!(key.code, "device_not_found");
        assert_eq!(status.code, "device_not_found");
        assert_eq!(pairing.code, "device_not_found");

        let text = bridge
            .call(
                "type_remote_text",
                remote_text_request("never-discovered", "hello"),
            )
            .unwrap_err();
        assert_eq!(text.code, "device_not_found");
        assert!(!text.message.contains("hello"));

        let apps = bridge
            .call("list_tv_apps", status_request("never-discovered"))
            .unwrap_err();
        let launch = bridge
            .call(
                "launch_tv_app",
                app_request("never-discovered", "com.example.video"),
            )
            .unwrap_err();
        assert_eq!(apps.code, "device_not_found");
        assert_eq!(launch.code, "device_not_found");
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

    // --- Embedded bridge (Android): one in-process call per request line -----------------

    /// A slot whose bridge is "embedded": `respond` stands for the Kotlin plugin running
    /// `control_tv.embedded.handle`. Every request line it receives is recorded.
    fn embedded_slot(
        respond: impl Fn(&Value) -> Result<String, BridgeFailure> + Send + Sync + 'static,
    ) -> (SharedBridgeState, Arc<Mutex<Vec<String>>>) {
        let seen = Arc::new(Mutex::new(Vec::new()));
        let respond = Arc::new(respond);
        let launch_seen = Arc::clone(&seen);
        let launcher: BridgeLauncher = Box::new(move || {
            let seen = Arc::clone(&launch_seen);
            let respond = Arc::clone(&respond);
            Ok(PythonBridge::in_process(Box::new(move |line| {
                let request: Value = serde_json::from_str(&line).unwrap();
                seen.lock()
                    .unwrap()
                    .push(request["method"].as_str().unwrap().to_string());
                respond(&request)
            })))
        });
        (Arc::new(Mutex::new(BridgeSlot::new(launcher))), seen)
    }

    fn answer(request: &Value, result: Value) -> Result<String, BridgeFailure> {
        Ok(json!({"id": request["id"], "ok": true, "result": result}).to_string())
    }

    fn embedded_call(
        state: &SharedBridgeState,
        method: &'static str,
        timeout: Duration,
    ) -> Result<Value, BridgeFailure> {
        tauri::async_runtime::block_on(call_bridge(Arc::clone(state), method, json!({}), timeout))
    }

    #[test]
    fn an_embedded_call_relays_the_request_line_and_its_response() {
        let (state, seen) = embedded_slot(|request| {
            assert_eq!(request["params"], json!({}));
            answer(
                request,
                json!({"status": "ready", "controlTvVersion": "0.1.0"}),
            )
        });

        let result = embedded_call(&state, "ping", Duration::from_secs(5)).unwrap();

        assert_eq!(result["controlTvVersion"], "0.1.0");
        assert_eq!(*seen.lock().unwrap(), ["ping"]);
    }

    #[test]
    fn a_failed_embedded_call_is_ambiguous_and_never_repeated() {
        let (state, seen) = embedded_slot(|request| match request["method"].as_str() {
            Some("pause") => Err(BridgeFailure::transport("plugin failed after the call")),
            _ => answer(request, json!({})),
        });

        let failure = embedded_call(&state, "pause", Duration::from_secs(5)).unwrap_err();
        embedded_call(&state, "ping", Duration::from_secs(5)).unwrap();

        assert_eq!(failure.code, CODE_BRIDGE_TRANSPORT);
        assert_eq!(*seen.lock().unwrap(), ["pause", "ping"]);
    }

    #[test]
    fn an_embedded_python_that_cannot_start_is_an_unavailable_backend_not_a_transport_failure() {
        let unavailable = embedded_rejection(Some("backend_unavailable"), Some("no Python"));
        let other = embedded_rejection(Some("bridge_transport"), None);
        let uncoded = embedded_rejection(None, Some("boom"));

        assert_eq!(
            unavailable,
            BridgeFailure::new(CODE_BACKEND_UNAVAILABLE, "no Python")
        );
        assert_eq!(other.code, CODE_BRIDGE_TRANSPORT);
        assert_eq!(uncoded, BridgeFailure::transport("boom"));

        let (state, _) =
            embedded_slot(|_| Err(BridgeFailure::new(CODE_BACKEND_UNAVAILABLE, "no Python")));
        let error = embedded_call(&state, "ping", Duration::from_secs(5)).unwrap_err();
        assert_eq!(error.code, CODE_BACKEND_UNAVAILABLE);
    }

    #[test]
    fn an_embedded_request_abandoned_behind_a_slow_one_is_never_handed_to_python() {
        let (state, seen) = embedded_slot(|request| {
            if request["method"] == "slow" {
                std::thread::sleep(Duration::from_millis(800));
            }
            answer(request, json!({}))
        });
        let holder_state = Arc::clone(&state);
        let holder = std::thread::spawn(move || {
            embedded_call(&holder_state, "slow", Duration::from_secs(5))
        });
        std::thread::sleep(Duration::from_millis(200));

        let waiting = embedded_call(&state, "pause", Duration::from_millis(200)).unwrap_err();

        assert_eq!(waiting.code, CODE_BRIDGE_BUSY);
        holder.join().unwrap().unwrap();
        std::thread::sleep(Duration::from_millis(300));
        assert_eq!(*seen.lock().unwrap(), ["slow"]);
    }

    #[test]
    fn an_embedded_timeout_after_the_claim_is_ambiguous_and_nothing_is_resent() {
        let (state, seen) = embedded_slot(|request| {
            if request["method"] == "slow" {
                std::thread::sleep(Duration::from_millis(800));
            }
            answer(request, json!({}))
        });

        let failure = embedded_call(&state, "slow", Duration::from_millis(200)).unwrap_err();
        embedded_call(&state, "ping", Duration::from_secs(5)).unwrap();

        assert_eq!(failure.code, CODE_BRIDGE_TIMEOUT);
        assert_eq!(*seen.lock().unwrap(), ["slow", "ping"]);
    }

    #[test]
    fn an_embedded_bridge_has_no_process_to_stop_or_replace() {
        let process = BridgeProcess::in_process();

        assert!(!process.has_exited());
        process.terminate();
        process.terminate();
        assert!(!process.has_exited());
    }
}
