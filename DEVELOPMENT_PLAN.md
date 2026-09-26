# Development plan

## Goal

Build a lightweight standalone Chromecast / Google TV controller for Android, Windows and Debian/Ubuntu. Manual control works independently. An MCP adapter exposes the same control capabilities to an external assistant. Python is part of the implementation, with `pychromecast` available for Chromecast discovery/control. Tauri 2 provides the cross-platform application shell; Rust/native components are used where they bring a concrete benefit.

Two kinds of evidence are kept apart everywhere in this plan (see "Progress rule" at the end): **automation-validated** means deterministic tests, CI, or the real application driven against a fake TV / fake transport; **real-hardware validated** means the behavior was observed on a real Chromecast/Google TV or on the real target platform, with the evidence recorded. A checked implementation line never implies the matching hardware line.

## Architecture constraints

- [x] Python and the Python Chromecast ecosystem are part of the architecture, including `pychromecast` for applicable Cast capabilities.
- [x] Tauri 2 is the cross-platform application shell and packaging layer.
- [x] JavaScript or TypeScript may be used for the UI according to implementation needs.
- [x] GUI and MCP share the same authoritative control/domain behavior.
- [x] Media/service resolution remains separated from low-level Cast transport where practical.
- [x] Manual application control operates independently of ChatGPT/MCP.
- [x] Build output, platform intermediates and temporary files are controlled and disposable.
- [x] Project cleanup is limited to verified project-owned generated output; shared/global caches remain separate.

## Phase 0 - Repository and development discipline

Deliverables:
- [x] repository hygiene and generated-file exclusions;
- [x] documented architecture and development plan;
- [x] reproducible local development commands;
- [x] explicit `clean`, `dist-clean` and disk-usage inspection commands for the current Python tooling;
- [x] extend `clean` / `dist-clean` / disk-usage coverage to Tauri, Rust and Android output; PR #4 added the allowlisted paths/tests and validated cleanup against real generated Tauri/Rust/frontend artifacts;
- [x] small, reviewable iterations and Conventional Commit / pull-request workflow.

Exit criteria:
- [x] project-owned versus shared caches are clearly distinguished;
- [x] architecture is documented consistently across project context files;

## Phase 1 - Shared control foundation

Deliverables:
- [x] define shared control/domain interfaces used by GUI and MCP;
- [x] Python project/package structure and typed device/connection models;
- [x] explicit errors and operation results;
- [x] integrate the selected Python Chromecast modules behind a focused adapter;
- [ ] add native/Rust components only where a measured or platform requirement justifies them;
- [x] focused deterministic unit tests for domain and shared control behavior.

Exit criteria:
- [x] control/domain behavior is testable independently of the UI;
- [x] GUI/MCP concerns are absent from the domain and shared control layer;
- [x] tests cover meaningful deterministic control behavior, including explicit confirmation and unconfirmed observations.

## Active review follow-up

- [x] **Review fixes automation-validated:** superseded-instance cleanup uses the remaining status deadline and an empty-snapshot browser is stopped immediately; both targeted mutations fail, 291 tests pass with 97.76% coverage.
- [x] **Implemented:** keep the PyChromecast discovery/zeroconf context alive for the lifetime of the cached Chromecast instances; repeated discovery atomically replaces the owned snapshot and cleanup tolerates a connection thread that has not started.
- [x] **Automation and read-only hardware validated:** 289 tests and all Python quality gates pass; on real hardware, two bounded discoveries found the same UUID, each subsequent status read succeeded, and repeated close completed cleanly; this was revalidated after the review fixes.
- [x] **Implemented and automation-validated:** disconnect each superseded PyChromecast instance before replacing the same UUID; repeated discovery and idempotent `close()` are covered by deterministic tests.
- [x] **Implemented and automation-validated:** expose PyChromecast `adjusted_current_time` for actively playing media while preserving the last reported position for paused media; deterministic tests verify progression.
- [x] **Implemented and automation-validated:** perform one bounded same-UUID rediscovery before command delivery when a cached connection is stale; never replay a command after its invocation starts.
- [ ] **Physical validation still required:** exercise stale-connection recovery, command acknowledgement and observed state after a command on a real Chromecast/Google TV (discovery, repeated discovery with replacement and repeated `close()` were validated read-only, see the entry above and Phase 2).

## Current implementation status - shared control service

- [x] Reproducible Python 3.12 environment is pinned with uv, `.python-version`, and committed `uv.lock`; setup fails when the lockfile is stale or uv is unavailable.
- [x] Shared control confirmation reports only device state actually observed; missing, contradictory, and disconnected states remain explicitly unconfirmed in deterministic tests.
- [x] Real-hardware evidence is required before any hardware/platform checkbox can be completed.
- [x] Coverage (95% minimum) and installed-dependency quality commands are implemented and validated; deterministic command tests cover orchestration and fail-fast behavior, and the defensive verification invariant is explicit.
- [x] **P2 review follow-up:** `CastTransport.get_status` now takes an explicit `timeout`; `ControlService._verify` passes only the confirmation budget actually remaining before every read, and never treats a status that arrives after the budget expired as confirmation. The PyChromecast adapter's `get_status` (connection, bounded same-UUID recovery, and the receiver-status round trip) now honors that same per-call budget instead of its own fixed instance timeouts. Deterministic tests cover a blocked/hung read, the exact `timeout` handed to each poll, total elapsed time never exceeding the budget, a match confirmed just before the deadline, and a match arriving just after it (never confirmed).
- [x] **P2 review follow-up:** a zero confirmation budget rejects seek before any status read or command instead of bypassing the capability guard or creating an independent timeout;
- [x] Read-only physical validation completed for discovery -> UUID selection -> connected receiver status, repeated discovery -> same UUID -> status, and repeated close; no media or receiver command was sent.

## Current implementation status - Tauri application shell

- [x] Tauri 2 shell scaffolded (`src-tauri/`, Rust) with a minimal frontend (`ui/`, vanilla TypeScript + Vite); no frontend framework or UI library is used yet, matching the size of a one-page skeleton.
- [x] The shell owns no Cast/control logic: `src-tauri/src/lib.rs` only spawns `src/control_tv/bridge.py` as a long-lived child process and forwards line-delimited JSON requests/responses over its stdin/stdout, matched by request id. `ControlService` is not reimplemented in Rust or TypeScript.
- [x] Nine bridge methods are exposed so far: three read-only (`ping`, `discover_devices`, `get_status`) and six commands (`play`, `pause`, `stop`, `seek`, `set_volume`, `set_muted`), each a plain forward to the same-named `ControlService` method; every other `TvControl` capability is added to the bridge and to a Tauri command as its own view needs it, not in advance.
- [x] A failed bridge spawn does not crash the application: the Tauri command returns an explicit "Python control backend unavailable" error instead, surfaced as plain text in the UI.
- [x] Real, installed-package validation on this Debian/Ubuntu-family desktop (Ubuntu 22.04): a Tauri CLI debug build with the `deb` bundle (today `npx --prefix ui tauri build --debug --bundles deb` from the repository root) produced a real `.deb`; it was installed with `dpkg -i`, launched from `/usr/bin/control-tv` (not the raw build output), showed "Control backend ready", and was cleanly uninstalled afterward (`dpkg -r`). The Tauri dev mode was also run and screenshotted before the packaged validation. This `.deb` still depends on this developer's repository `.venv` (see `resolve_python()` below), so it is not a distributable package.
- [x] Real end-to-end proof the mechanism reaches the shared control layer: clicking "Discover devices" in the running application performed a real LAN discovery through `ControlService.discover_devices` and returned real Chromecast devices present on the operator's network (no device names/addresses are recorded in this public file; see the local handoff). No real-hardware result for a control command (play/pause/stop/seek/volume/mute) is recorded; the UI's command controls have only been exercised against a fake TV (see the playback-controls and volume entries below).
- [x] Rust unit tests cover response parsing/error-translation as pure functions, plus one test that spawns the real `python -m control_tv.bridge` process and pings it (`cargo test`, run as part of `scripts/dev.py rust-check`); it skips (not fails) when `.venv` does not exist yet, which the CI job avoids by running Python setup first.
- [x] **P1 review follow-up (PR #4):** `bridge_ping`/`bridge_discover_devices` are async Tauri commands; the blocking bridge call runs on a dedicated worker thread (`tauri::async_runtime::spawn_blocking`), never on the async/main thread, and is wrapped in a bounded `tokio::time::timeout` so a slow or fully stuck bridge process reports an error instead of hanging the command forever (the underlying worker thread can still remain blocked - a documented limitation, not a correctness issue, since responses stay matched by id). Deterministic Rust tests cover a successful async call, a process that exits without responding, and a process that never responds at all (times out at the configured bound, not after). Re-verified manually in the Tauri dev mode: the window kept repainting ("Discovering…") while a real discovery call was in flight.
- [x] **Device selection and read-only status view (implemented and automation-validated):** a discovered device is selected by its stable id (never its display name; two devices may share a name); the selection lives in UI state only, so the bridge stays stateless. `get_status` (bridge) -> `bridge_get_status` (Tauri) -> `ControlService.get_status`, with no Cast logic added in Rust or TypeScript. Failures now reach the UI as `{code, message}` (control-layer codes plus `backend_unavailable`, `bridge_timeout`, `bridge_transport`) so an unavailable device, a timeout, an unknown device and an unavailable backend are told apart; an unexpected Python exception becomes an `internal_error` response instead of killing the bridge. The UI shows no-selection, searching, empty, loading, failure, disconnected, partial (each unreported field worded as not reported, never zero/off) and nothing-playing states, in plain language with raw error text only in a collapsed "Details" disclosure (no internal component names in the normal view); at most one bridge request is in flight from the UI (no selection, refresh or discovery is offered while a read or discovery runs, because the bridge is single-flight and each request's timeout starts before its turn), and a late answer never overwrites the current read. Tests: Python bridge, Rust (`cargo test`) and 58 TypeScript model tests (`npm test` in `ui/`, run by `dev.py ui-check`).
- [x] The visual design follows the local UI skills (`ui-desktop-minimal`, `ui-ux-accessibility`, `ui-responsive-app`, `ui-design-review`, `ui-component-design`; installed outside this repository) and remains open to owner direction: a light, single-surface remote rather than a dashboard, the validated `assets/logo.svg` reused unchanged (served through Vite's `publicDir`), one consistent inline SVG icon set, no decorative gradients, shadows or extra cards, the selected device as one row that expands into the device list, and what is playing as the main context. Playback controls are drawn only for media the TV reports on a connected device, and only what the observed state offers.
- [x] UI consolidation review (`ui-design-review`, 20 states at 900px and 320px on a fake backend, plus the real window): the structure needed no rethink; localized defects were fixed - a dropped selection was silent (the notice was never drawn), loading looked like disabled and was unreadable, live regions re-announced an unchanged status after every discovery, Escape did not close the device list, focus was lost after Try again, the Details target was under 44px, and a 480px breakpoint was replaced by fluid CSS. Measured: no horizontal overflow at 320, 390, 481, 768 and 1280px across ten content-stress states; every colour pair meets WCAG AA (body 15:1, muted 5.9:1, accent 5.06:1, error 6.6:1). Keyboard paths were exercised on the real window and in a browser; nothing was run with a screen reader, so accessibility is not claimed as complete.
- [x] Real desktop run (Ubuntu 22.04, Tauri dev mode): real discovery, selection by click, change of selection and a second discovery that kept the selection were observed. No control command was sent.
- [x] The adapter defect found while validating PR #8 (discovery stopped PyChromecast's zeroconf before the cached devices connected, so every real status read timed out) was fixed by PR #9. The merged application was then revalidated end to end on real hardware (Ubuntu 22.04, Tauri dev mode, read-only) on 2026-09-26: UI -> Tauri -> bridge -> `ControlService` -> PyChromecast for discovery, selection by stable id, status, refresh, a rediscovery that kept the selection and the status, and a change of selection to a second device; the window stayed responsive (it was resized while a discovery was running). No control command was sent.
- [x] **Playback controls (play, pause, stop, seek) implemented and automation-validated, no real device involved:** the bridge forwards `play`/`pause`/`stop`/`seek` (`deviceId`, plus a numeric `positionSeconds` for seek) to the existing `ControlService` methods and serializes the `CommandResult`; `ok: true` on a command means it was *sent*, and `confirmation` (`confirmed` / `unconfirmed` / `not_checked`) says whether the TV then showed the result, so unconfirmed is never reported as success. Four async Tauri commands (`bridge_play`/`_pause`/`_stop`/`_seek`) reuse `call_bridge` (worker thread plus a 60s bound covering the control layer's own windows), never retry, and relay control-layer error codes untouched; a single-threaded-runtime test proves a slow command does not block the async thread (it fails when the bridge call is run inline, recreating the PR #4 P1). The UI offers one play/pause toggle that always offers the opposite of the observed state (pause while playing or buffering, play while paused), a quieter Stop, and a seek slider only when the TV reports seekable media with a known length and position; a seek being composed is shown as a draft ("Go to 4:09") and a single seek is sent after the control settles, never a burst; the position shown changes only through a status the TV reported. A command in flight blocks every other request (a rapid triple click sends one command). A sent-but-unconfirmed command keeps the last status the TV reported, is worded as not confirmed, and offers Check state instead of resending; a timeout says the command may or may not have arrived. Tests: 51 new bridge tests (367 Python tests, 97.61% coverage), 28 Rust tests (20 before), 115 TypeScript tests (58 before) including mutation checks of the no-double-command, unconfirmed-is-not-success and no-simulated-position rules.
- [x] **Playback controls exercised in the real application against a fake TV:** the real Tauri shell, Rust commands, bridge and `ControlService` were run with only the Cast transport replaced by a scratch double (a fleet of 15 scriptable fake TVs, kept outside the repository, run on a private X display so it could not touch the desktop). Observed: confirmed pause/play/seek, a pending state that fades the other controls, a TV that ignores commands (unconfirmed after the 5s window, exactly one command delivered, Check state), a TV that acts after the window (unconfirmed, then Paused after Check state), refused, unreachable and send-timeout failures with their plain wording, live media (no seek), unknown length, capability not reported, buffering, idle and nothing-playing states, and Stop on a TV that drops its media session (see the Stop entry below). The states were also checked at 480px in the real window and at 320, 390, 481, 768 and 1280px in a browser harness (no horizontal overflow in 50 width and state combinations).
- [ ] **Stop on a real receiver is expected to be sent-but-unconfirmed (audit answered, effect unobserved):** the Cast backend audit (PR #12, since merged) confirmed the service never treats an absent media session as proof of a stop ("stop does not invent IDLE"), and changed nothing. A real receiver drops its media session when stopped, so the application will likely word a successful Stop as not yet confirmed. Whether that wording is acceptable is judged on real hardware, only after an explicit go-ahead; no backend or UI change was made for it here.
- [x] **Volume and mute implemented and automation-validated, no real device involved:** the bridge forwards `set_volume` (`level`, a number from 0 to 1, never a boolean) and `set_muted` (`muted`, a JSON boolean: an absolute state, so mute is `true` and unmute is `false`, never a toggle) to the existing `ControlService` methods, with the same sent-versus-confirmed answer as the playback commands; a refused value (a boolean level, `0`/`1`/`"true"` as a mute state, NaN, out of range) fails as `invalid_argument` before any transport call. Two async Tauri commands (`bridge_set_volume`, `bridge_set_muted`) reuse `call_bridge` (worker thread, 60s bound, no retry, error codes relayed untouched), and a real-bridge test proves the parameter names match. The UI shows a mute button and a volume slider under the playback controls for any connected device, playing or not. The slider works on a local draft and sends one command once it has settled (300 ms after a pointer release, 800 ms after a key press, so a held key's repeat pause does not split it into two), never one per pixel or key repeat; at most one command is ever in flight, so a second gesture cannot queue behind the first; a level equal to the one the TV reports is not sent. The level shown is the one the TV reported, except while a level is being composed ("Set volume to 60%") or is on its way ("Setting volume to 60%…"), and mute asks for the opposite of the state the TV reported without keeping a state of its own. A volume the TV did not show is worded as not confirmed and says what the TV reports instead ("Volume sent, but the TV reports 47%, not 50%"), offers Check state and is never resent; a volume or mute state the TV did not report offers no control and says so, never zero or unmuted. Tests: 59 new bridge tests (453 Python tests, 97.66% coverage), 39 Rust tests (28 before), 203 TypeScript tests at that point (115 before; 238 with the raise limit below) including mutation checks of the no-double-command, one-command-per-gesture, opposite-of-observed, unknown-is-not-zero and unconfirmed-is-not-success rules.
- [x] **Volume and mute exercised in the real application against a fake TV:** the real Tauri shell, Rust commands, bridge and `ControlService` were run with only the Cast transport replaced by a scratch double (a fleet of 15 scriptable fake TVs, kept outside the repository, on a private X display). Observed: a 34-move mouse drag and eight quick key presses each sent exactly one command, a held key sent one, a triple click on mute sent one, extra clicks and drags during a slow command sent nothing, a TV that ignores commands stayed at one command 9s later (unconfirmed, the level it reports, Check state), a stepped-volume TV settled on 47% for a 49% request (worded as not confirmed), a TV that acts after the window showed the new level after Check state, and refused, unreachable and send-timeout failures were worded in plain language; muted, no-media, volume-not-reported, mute-not-reported and nothing-reported TVs were checked, and so were the keyboard order and focus, the 480px window, and 320, 390, 481, 768 and 1280px in a browser harness (no horizontal overflow in 50 width and state combinations).
- [x] **Volume and mute display validated read-only on a real device (2026-09-26, no command sent):** the application discovered one real device, selected it by its stable id and read its status: it reported volume 100% and not muted with nothing playing, the sound row drew "Volume 100%" with the slider at its end, and no playback controls were drawn (nothing to control). Nothing was pressed after the selection. Whether that device's volume is adjustable (a receiver can report a fixed volume of 100%) is not known: `ReceiverStatus` does not carry PyChromecast's `volume_control_type`, so this is recorded as a question for the physical validation, not as a finding.
- [x] **Volume raise limit (volume-slam protection) implemented and automation-validated, owner decision:** one gesture can raise the volume by at most 10 points above the level the TV last reported; lowering is never limited (0% is one gesture); the reference is only the reported level (never a draft or a command in flight), so once the TV reports the new level it becomes the reference of the next gesture. Home goes to 0%, End to the reference + 10 (never above 100%); at the limit the line under the slider reads "Set volume to 55% - raising is limited to 10% at a time". The limit is applied to the draft, to what the slider shows and again when the command is built (`ui/src/sound.ts`); there is no dialog, override, extra timer, automatic retry or invented value, and one gesture still sends at most one command. A gesture also ends on the pointer or key release, not only on `change`, because WebKit stops reporting `change` once the value is clamped (found in the real window). UI protection only: `ControlService`, the bridge and Rust are unchanged, so a future MCP client is not covered by it. Tests: 238 TypeScript tests (35 for the limit and its release triggers, with 15 mutation checks); exercised in the real application against fake TVs only.
- [ ] **Volume and mute are not validated on a real Chromecast:** no real-hardware result for a volume or mute command is recorded. This waits for the physical validation of the playback commands, its review and an explicit go-ahead. Things to watch there are listed under "Known open items" below (volume tolerance, volume control type, service-level boolean check).
- [ ] **Not validated on a real Chromecast:** no real-hardware result for a play, pause, stop or seek command is recorded. The backend audit (PR #12) is merged; the physical validation is run separately and is checked here only once its evidence is recorded. Read-only discovery and status remain the only recorded hardware validation.
- [ ] Windows and Android are not built, installed or launched; nothing here validates them.
- [ ] Packaging a real Python runtime for a distributed build (not this developer's own `.venv`) is not implemented; `resolve_python()` is explicitly a development-only placeholder (see its docstring) and is Phase 7 work.

## Known open items (audit of 2026-09-26)

Verified in the code on 2026-09-26 and intentionally not fixed yet; each needs its own slice. None of them is a hardware finding.

- [ ] **Distributable Python runtime:** `resolve_python()` (`src-tauri/src/lib.rs`) resolves the repository `.venv` from a compile-time path, so a built package only runs on the machine that built it (Phase 7).
- [ ] **Tauri content security policy:** `tauri.conf.json` sets `"csp": null` (with `withGlobalTauri: true`); define a restrictive CSP before a release.
- [ ] **Bridge overflow handling:** a JSON integer too large for a float in `seek.positionSeconds` or `discover_devices.timeoutSeconds` returns `internal_error` instead of `invalid_argument` (nothing is sent; `set_volume` already handles it).
- [ ] **Boolean inputs:** `discover_devices` accepts a JSON boolean as `timeoutSeconds`; `ControlService.set_muted` does not check that `muted` is a boolean and `ControlService.set_volume` accepts `True`/`False` as a level (the bridge and the PyChromecast adapter refuse them, but a future MCP caller would reach the service directly).
- [ ] **Volume control type:** `ReceiverStatus` does not carry PyChromecast's `volume_control_type` (`attenuation`, `fixed`, `master`), so a fixed-volume receiver is offered a slider; a coordinated domain/adapter/bridge/UI change.
- [ ] **Volume confirmation tolerance:** `volume_tolerance` is 0.01, so a receiver that settles on its own volume steps answers `unconfirmed` although it acted; decide after the real-hardware volume validation.
- [ ] **Slider focus return:** after a command, focus is given back to the seek or volume slider even when the user moved focus elsewhere during the command (`ui/src/main.ts`); clear the flag when focus moves.
- [ ] **Assistive-technology slider change:** a value change made with neither pointer nor key events and clamped by the raise limit stays an unsent draft until the next release or refresh (safe, nothing is sent); not tested with a screen reader.
- [ ] **Load media in the application:** `ControlService.load_media` exists and is tested, but it is not exposed through the bridge, Tauri or the UI, so the application only controls media that is already playing; decide whether the first desktop release needs it (Phase 5 covers content resolution).
- [ ] **Status refresh:** the status is read on selection and on demand only; there is no periodic refresh, so the shown position and state can be stale (also needed by the tray, Phase 4b).
- [ ] **Version and license metadata:** `pyproject.toml` is at 0.0.0 while `src-tauri/Cargo.toml`, `tauri.conf.json` and `ui/package.json` are at 0.1.0; none declares the project license (GPL-3.0, `LICENSE`).
- [ ] **Stop wording on real hardware:** see the Stop entry above; a product decision after the real-hardware result.

## Phase 2 - Cast discovery and connection

Deliverables:
- [x] LAN discovery using the selected Chromecast integration;
- [x] stable device selection separate from display names;
- [x] bounded discovery/connection timeouts;
- [x] connection lifecycle and bounded recovery from stale device addresses;
- [x] receiver/device status retrieval.

Exit criteria:
- [x] at least one real compatible device was discovered on a real local network (validated 2026-09-25 through the Tauri shell -> Python bridge -> `ControlService.discover_devices` path added in this iteration; device names/addresses are not recorded here, see the local handoff);
- [x] a real device status can be read after discovery and again after repeated discovery (validated read-only on 2026-09-26);
- [ ] a real command can be sent and confirmed; no control command was sent during the read-only lifecycle validation;
- [x] failures are represented explicitly rather than as false success.

## Phase 3 - Media controls

Current playback-confirmation audit:
- [x] audit load, play, pause, stop, volume and mute confirmation against missing, contradictory, disconnected and late observations;
- [x] load remains bound to the requested URL, while volume and mute require an observed receiver value matching the request within the existing global deadline;
- [x] **Implemented and targeted-test validated:** prevent play, pause and stop from being confirmed by the expected playback state on different or unidentified media;
- [x] deterministic negative coverage verifies all three transitions, unavailable pre-command identity and the global deadline; removing the identity guard makes all four focused mutation tests fail;
- [x] complete local Ruff, format, strict mypy, 252-test suite, 98.07% coverage and dependency checks pass;
- [ ] physical Chromecast/Google TV validation remains required; automated fakes are not hardware evidence;
- [x] **P2 implemented and targeted-test validated:** share one confirmation deadline across the pre-command media snapshot and post-command verification, with every read limited to the remaining budget;
- [x] **P2 implemented and targeted-test validated:** treat `None`, empty and whitespace-only `content_id` values as absent evidence without rewriting usable identifiers;
- [x] deterministic timing/identity coverage passes; removing deadline reuse makes all four focused double-budget mutation tests fail;
- [x] final local Ruff, format, strict mypy, 266-test suite, 98.12% coverage and dependency checks pass;

Current play/pause/stop command audit:
- [x] **P2 review follow-up:** failed transport calls are counted separately from delivered commands, proving exactly one attempted play/pause/stop call and zero delivered calls without conflating SENT with attempted;
- [x] **Audit complete:** verify each command independently across delivery timeout, unavailable/rejected delivery, single-send/no-replay behavior, post-send disappearance, contradictory post-command state, late evidence and the shared confirmation budget;
- [x] deterministic matrix validates all three commands; removing shared deadline reuse makes the three focused late-snapshot mutation tests fail;
- [x] complete local Ruff, format, strict mypy, 343-test suite, 97.55% coverage and dependency checks pass; hosted CI is required on the final PR HEAD;
- [ ] physical Chromecast/Google TV validation remains required; this audit sends no command to real hardware;

Current confirmation-synchronization iteration:
- [x] **Implemented:** bind seek confirmation to the `content_id` observed before command delivery, so different or unidentified media can never satisfy a position-only confirmation;
- [x] deterministic targeted tests cover matching media, replaced media, missing media identity, contradictory positions and the unchanged global confirmation deadline;
- [x] complete local lint, format, strict typing, test, 98.01% coverage and dependency gates pass; hosted CI remains a separate PR requirement;
- [x] **Implemented:** make the pre-command seek status read and post-command confirmation share one bounded confirmation deadline, and treat empty or whitespace-only seek media identities as absent evidence;
- [x] deterministic timing and identity tests pass; removing the shared deadline makes all four focused double-budget mutation tests fail;
- [x] **P2 review follow-up:** a status returned exactly at the deadline remains usable only to block an explicitly unsupported seek; its late media identity cannot confirm the command;
- [x] **P2 review follow-up:** a zero confirmation budget rejects seek before any status read or command instead of bypassing the capability guard or creating an independent timeout;
- [x] complete local Ruff, format, strict mypy, 316-test suite, 97.55% coverage and dependency checks pass; hosted CI is required on the final PR HEAD;
- [ ] distinguish replacement sessions that reuse the exact same `content_id`; this needs a stable media-session identifier in the shared status model and must be coordinated with the open Tauri bridge PR before changing that interface;
- [ ] physical Chromecast/Google TV validation remains required; no fake or automated test completes it;

Current command-result hardening iteration:
- [x] **P2 review follow-up implemented and automation-validated:** `MEDIA_STATUS` is the only successful terminal LOAD response; every other terminal response is rejected before confirmation;
- [x] **Implemented and automation-validated:** commands not sent (`RequestFailed` -> unavailable) are distinct from receiver-declared media rejection (`LOAD_FAILED` -> rejected), and neither command is replayed;
- [x] deterministic coverage proves unavailable, ambiguous and explicitly rejected delivery never produces a `CommandResult` or invented confirmation;

Current hardening iteration:
- [x] **Review follow-up implemented and automation-validated:** valid optional whitespace before MIME parameter delimiters is accepted without weakening malformed MIME rejection;
- [x] **Implemented and automation-validated:** stale-instance cleanup inside `get_status` is capped by the remaining caller deadline; exhausted budgets signal non-blocking shutdown and never start rediscovery;
- [x] **Implemented and automation-validated:** media URLs/content types/titles and direct transport discovery, seek, volume and mute inputs are rejected before any Cast call when invalid;
- [x] **Automation-validated:** deterministic discovery -> UUID selection -> connection -> status coverage selects and reads only the requested UUID;
- [ ] **Physical validation required:** execute and record `docs/CAST_HARDWARE_VALIDATION.md` on a real Chromecast/Google TV;

Deliverables:
- [x] play/load supported media (in `ControlService` and the transport; `load_media` is not yet exposed in the application, see "Known open items");
- [x] pause/resume and stop;
- [x] seek where supported;
- [x] volume and mute;
- [x] receiver/media state synchronization;
- [ ] validation of URLs, content types, ranges and application identifiers.

Exit criteria:
- [x] supported operations have explicit results/errors, automation-validated across sent/confirmed/unavailable/timeout/rejected outcomes;
- [ ] real-device validation distinguishes a sent command from a confirmed resulting state.

## Phase 4 - Lightweight Tauri UI

Deliverables:
- [x] device discovery view (discovered devices are listed and selectable);
- [x] device selection by stable id (UI state; kept across a rediscovery that reports the same id, dropped with a notice otherwise);
- [x] current receiver/media state view for the selected device (read-only; implemented and automation-validated);
- [x] receiver/media state validated on a real device through the UI (read-only, 2026-09-26, after PR #9): the connected state, volume, mute state and "nothing playing" were read and refreshed for two devices; playing media, playback position and a paused/buffering state were not observed on real hardware because nothing was playing;
- [x] playback controls (play/pause toggle, stop, seek) implemented, automation-validated and exercised in the real application against a fake TV;
- [ ] playback controls validated on a real Chromecast (the backend audit, PR #12, is merged; no real-hardware result is recorded yet);
- [x] volume/mute controls implemented, automation-validated and exercised in the real application against a fake TV, including the 10-point raise limit per gesture;
- [ ] volume/mute validated on a real Chromecast (waits for the playback-command physical validation and an explicit go-ahead; no real-hardware result is recorded);
- [x] clear unavailable/error states for the control-backend boundary itself (bridge process unavailable, discovery failure are both surfaced in the UI as plain text; status-read failures - device unavailable, timeout, unknown device, backend unavailable or not responding - are told apart by error code; playback-command outcomes - not confirmed, refused, unreachable, timed out (delivery ambiguous), backend unavailable - are worded in plain language and never shown as success);
- [ ] responsive desktop/Android layout (fluid single-column layout with 44px touch targets and controls up to 56px; no horizontal overflow from 320px to 1280px across the status states and the playback-control states in a browser harness with a fake backend, and observed in the real window between 480px and 900px; nothing was run on Android or with a touch screen);
- [x] choose JavaScript/TypeScript and any UI tooling from concrete implementation needs (vanilla TypeScript + Vite: no frontend framework is justified yet by a single-page skeleton).

Exit criteria:
- [ ] application controls a TV manually without ChatGPT or MCP (needs a recorded real-hardware command result);
- [x] frontend presentation remains separated from Cast transport/control logic (verified in the code on 2026-09-26: TypeScript and Rust hold no Cast logic; every command goes through the bridge to `ControlService`, and only the adapter imports PyChromecast).

## Phase 4b - Desktop native integration

A system tray / status indicator gives quick access to the essential controls without the main window. Decided during the UI review of 2026-09-26; not started. The tray is a second view over the same chain as the window (Tauri -> Python bridge -> `ControlService`), never a second control engine.

Deliverables:
- [ ] native system tray / status indicator for the desktop application, using the validated control-TV icon (`assets/logo.svg` and the existing `src-tauri/icons/`);
- [ ] GNOME/Linux support, including the status-indicator (AppIndicator) behavior of a stock GNOME desktop, with the limits of the desktop environment documented;
- [ ] "Open control-TV" reopens and focuses the main window;
- [ ] "Quit" really stops the application and its Python bridge;
- [ ] explicit, documented behavior for closing the main window versus quitting the application (closing the window keeps the tray running, or quits, by an explicit decision);
- [ ] quick controls for the selected device: play/pause, stop, mute/unmute;
- [ ] volume control only if the platform tray integration allows an appropriate UX (otherwise "Open control-TV" is the path to volume);
- [ ] no Cast business logic in the Rust/Tauri tray code: every action goes through the existing bridge commands and `ControlService`;
- [ ] tray labels and states follow the same evidence as the window: a command is shown as confirmed only when `confirmation` is `confirmed`; `unconfirmed`, `not_checked`, errors and unreported fields are never shown as success or as a default value; one command at a time, no automatic retry;
- [ ] the selected device and state shared between the window and the tray have one owner, so both views stay consistent;
- [ ] a structure that lets an equivalent Windows notification-area integration be added later without duplicating logic.

Exit criteria:
- [ ] tray behavior automation-validated against a fake transport (actions, confirmation states, window close versus quit);
- [ ] tray installed and exercised on a real GNOME/Linux desktop, recorded separately from automated validation;
- [ ] tray commands validated on a real Chromecast only after the matching window commands are validated on hardware.

## Phase 5 - Media and service resolution

Deliverables:
- [ ] resolver boundary separate from Cast transport;
- [ ] explicit support for selected content/service sources;
- [ ] metadata and playable-target validation;
- [ ] clear unsupported-content behavior.

Exit criteria:
- [ ] natural content targets convert into explicit Cast actions through the shared control layer;
- [ ] service-specific resolution remains decoupled from low-level Cast transport.

## Phase 6 - MCP adapter

Deliverables:
- [ ] small typed MCP tool surface over shared control/domain capabilities;
- [ ] discovery/status and media-control operations;
- [ ] actionable tool errors;
- [ ] local/security boundary documented;
- [ ] standalone application remains independent of an embedded AI API client.

Exit criteria:
- [ ] MCP and GUI invoke the same authoritative behavior;
- [ ] tool/schema tests are separated from real-device validation;
- [ ] MCP lifecycle/state interactions are explicit and testable.

## Phase 7 - Cross-platform packaging

Targets:
- [ ] Android `.apk`;
- [ ] Windows `.exe` / appropriate installer artifact;
- [ ] Debian/Ubuntu `.deb`.

Deliverables:
- [ ] target-specific Tauri configuration;
- [ ] package the required Python runtime/components appropriately for each target;
- [ ] icons/metadata/version consistency;
- [ ] reproducible release commands;
- [ ] CI builds where useful;
- [ ] documented signing/sideloading status.

Exit criteria:
- [ ] package build and installation/launch validation are tracked separately;
- [ ] each claimed platform is installed and launched on that platform before validation is recorded.

## Phase 7b - Android application and home-screen widget

Runs after the Android application/APK target of Phase 7. Not started.

Blocking prerequisite:
- [ ] **Architecture decision (owner) before any Android work:** how the Python runtime and the shared control core (`ControlService`, the PyChromecast adapter) run on Android, or how Android reuses that core otherwise (for example an embedded Python runtime or another documented option); the decision must keep one authoritative control engine for the window, the tray, MCP and Android, and is recorded in `ARCHITECTURE.md` before implementation.

Android application:
- [ ] the Android application reuses the shared control core as decided above, with no duplicated Cast logic;
- [ ] discovery, selection, status and commands validated on a real Android device against a real Chromecast/Google TV, recorded separately from emulator or automated results.

Home-screen widget (decided 2026-09-26):
- [ ] an Android home-screen widget focused on quick controls, not a miniature copy of the application;
- [ ] shows the selected device and the useful current playback state;
- [ ] quick controls where supported: play/pause, stop, mute/unmute, volume;
- [ ] displayed state comes only from the state the device actually reported; after an unconfirmed command the widget never shows an invented confirmed playback, mute or volume state;
- [ ] reuses the shared control-TV domain and application logic rather than duplicating Cast logic in the widget;
- [ ] explicit behavior when no device is selected, when the device is unavailable and when the status cannot be obtained;
- [ ] widget behavior validated on real Android hardware.

## Phase 8 - CI/CD

### Continuous integration

- [x] create the primary GitHub Actions CI workflow (`.github/workflows/ci.yml`);
- [x] run CI on pull requests targeting `main`;
- [x] run CI on pushes to `main`;
- [x] install the Python environment reproducibly with `uv sync --locked`;
- [x] verify lockfile and dependency consistency with `--locked` and `uv pip check`;
- [x] run `ruff check`;
- [x] run `ruff format --check`;
- [x] run `mypy --strict`;
- [x] run `pytest`;
- [x] generate terminal coverage reporting and enforce a 95% minimum;
- [x] make required local quality failures fail the CI job;
- [x] validate the workflow in a hosted GitHub Actions run (run `36131989754` on commit `f872e08`, after rebasing onto main and fixing the P2 timeout-contract review; all steps passed);
- [x] replace the deprecated Node 20 `actions/checkout` runtime reported by the first hosted run with SHA-pinned v5.0.1 (Node 24);
- [ ] require applicable CI checks before a pull request is considered merge-ready (on 2026-09-26 `main` has no branch protection rule or ruleset).

### Application build CI

- [x] verify the Tauri 2 build (hosted run `36135704584`, job "Tauri shell (Rust and frontend)", 3m37s, all steps passed, including a real `.deb` bundle);
- [x] verify the frontend build (`tsc && vite build`, part of the same hosted run);
- [x] verify Python/Tauri integration (`cargo test` in that job spawns the real `control_tv.bridge` process and pings it);
- [x] add Linux build checks (the job above);
- [ ] add Windows build checks;
- [ ] add Android build checks;
- [x] keep CI build success distinct from real Chromecast/TV hardware validation (this CI job never touches a Cast device; it built/packaged/pinged the bridge process only).

### Continuous delivery and packaging

- [ ] build the Debian/Ubuntu package;
- [ ] build the Windows installer/application artifact;
- [ ] build the Android APK;
- [ ] retain controlled build artifacts from release workflows;
- [ ] apply consistent artifact versioning;
- [ ] generate checksums for release artifacts;
- [ ] generate or prepare release notes.

### Releases

- [ ] define the project versioning strategy;
- [ ] trigger release builds from the selected tag/release mechanism;
- [ ] publish verified artifacts to GitHub Releases;
- [ ] verify release artifacts before publication;
- [ ] document and implement signing where required;
- [ ] never mark a package/platform as validated without the corresponding real installation/launch test.

### Security policy and vulnerability reporting

`SECURITY.md` (added 2026-09-26) supports only the latest `main` during development and asks reporters to use GitHub private vulnerability reporting.

- [ ] enable GitHub Private Vulnerability Reporting for the repository (repository setting, owner action; it was disabled when checked on 2026-09-26);
- [ ] verify that private reporting actually works (the "Report a vulnerability" entry is visible to a non-maintainer and a report reaches the maintainers privately), and record how it was verified;
- [ ] at the first stable release, update `SECURITY.md`: replace the development-only support matrix with the release versions actually supported;
- [ ] at the first stable release, define the security support policy for older releases;
- [ ] at the first stable release, check that the private reporting procedure described in `SECURITY.md` is still current;
- [ ] review `SECURITY.md` for every major release and whenever the supported release lifecycle changes.

### CI/CD maintenance

- [ ] configure automated dependency update monitoring where appropriate;
- [ ] run CI against dependency update pull requests;
- [ ] add relevant security checks;
- [ ] define artifact retention policy;
- [ ] keep local build cleanup requirements independent from CI runner cleanup.

Exit criteria:
- [ ] pull requests cannot be considered merge-ready until required CI checks pass;
- [ ] release artifacts are produced reproducibly by automation;
- [ ] CI/build validation, package validation and physical-device validation remain explicitly distinct.

## Phase 9 - Release readiness

Verify:
- [ ] manual control without AI/MCP;
- [ ] discovery/reconnection and error recovery;
- [ ] Cast operations on real hardware;
- [ ] MCP adapter independently;
- [ ] Android, Windows and Debian/Ubuntu packages;
- [ ] required CI checks are green for the release commit;
- [ ] repository contains no credentials or generated build/temp output;
- [ ] `SECURITY.md` matches the released versions and private vulnerability reporting has been verified to work;
- [ ] build disk usage remains understood and controlled.

## Mandatory build/temp cleanup

At every build/test/package iteration:
- [ ] inspect free disk and relevant project-generated footprint before build-heavy work;
- [ ] identify generated project-owned output after the cycle, including failed/interrupted cycles;
- [ ] clean obsolete Tauri/Rust, Android, Python/package, test, log, staging, scratch and extracted temporary output;
- [ ] retain release artifacts only in a controlled distribution location;
- [ ] measure remaining project-generated footprint;
- [ ] record retained generated artifacts and why they remain;
- [ ] treat cleanup failure as an incomplete iteration and record exact path/size/reason.

Recursive cleanup targets are verified as project-owned before deletion. Shared/global Cargo, Gradle, Android SDK/NDK, Python environments/caches and unrelated system/user directories are outside normal project cleanup.

## Iteration protocol

At the end of each implementation iteration:
- [ ] keep one coherent objective;
- [ ] run and record relevant checks and unvalidated areas;
- [ ] complete mandatory cleanup and disk measurement;
- [ ] create a pull request using the owner's Conventional Commit/PR workflow;
- [ ] record objective, branch/PR, files/interfaces changed, architecture decisions, dependencies, exact tests, real hardware/platform validation, generated artifacts, cleanup, intentionally retained artifacts, remaining footprint, limitations, assumptions and exact next work.

## Progress rule

Every actionable development item uses a Markdown checkbox. `[x]` means the work and relevant validation are complete; future work remains `[ ]`. Update this plan continuously in the same iteration that changes project state, including newly discovered work, review findings and blockers. Do not defer plan synchronization until the end of an iteration.

A deliverable or exit criterion whose wording refers to real hardware, a real device or a real platform is checked `[x]` only after that validation actually ran on that hardware/platform, with the evidence recorded in the handoff. Passing automated tests against a fake/simulated transport, fake TV, fake clock or emulator is real, valuable engineering progress, but it is never by itself sufficient to check such an item; simulated validation is never substituted for or presented as hardware validation. When a deliverable bundles implementation with hardware validation (for example "receiver/media state synchronization"), split it in this file into two lines - one for the implementation, checked once it is built and covered by deterministic tests, and one for real-device validation, checked only once that validation actually happened - rather than checking the combined line early.
