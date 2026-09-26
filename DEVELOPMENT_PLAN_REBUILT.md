# Development plan - rebuilt from project history

## Purpose

Build a lightweight standalone Chromecast / Google TV controller for Debian/Ubuntu, Windows and Android. Manual control must work independently of ChatGPT or MCP. A future MCP adapter must expose the same authoritative control capabilities rather than duplicate them.

This document is a reconstruction candidate based on the merged PR history, surviving feature branches, current `main`, hardware-validation records and the decisions made during development. It is intentionally kept separate from `DEVELOPMENT_PLAN.md` until reviewed. Once accepted, it should replace the current plan rather than coexist with it.

## Progress rule

Three kinds of evidence must never be conflated:

- **Implemented**: production code exists.
- **Automation-validated**: deterministic tests, CI, mutation checks, browser harnesses, or the real application running against fake TVs validate the behavior.
- **Real-hardware validated**: behavior was actually observed on a physical Chromecast/Google TV or target operating system and the evidence was recorded.

A checked implementation item never implies physical validation. `ok: true` means a command was sent, not that the receiver confirmed the resulting state. `UNCONFIRMED` must remain distinct from confirmed success. Ambiguous delivery must never trigger automatic replay.

Private device names, UUIDs, addresses, media titles and other network identifiers stay out of committed documentation.

## Architecture invariants

- [x] Python is part of the authoritative control architecture.
- [x] PyChromecast is isolated behind the Cast transport adapter.
- [x] `ControlService` owns shared control semantics and confirmation behavior.
- [x] Tauri 2 is the desktop/cross-platform shell and packaging layer.
- [x] The current desktop UI is vanilla TypeScript + Vite.
- [x] Rust and TypeScript do not duplicate Cast business logic.
- [x] UI -> Tauri -> stdio JSON bridge -> `ControlService` -> `CastTransport` / PyChromecast is the implemented desktop chain.
- [x] GUI and future MCP clients must share the same authoritative control/domain behavior.
- [x] Device selection is by stable id/UUID, never display name.
- [x] Manual control is independent of ChatGPT/MCP.
- [x] Project-owned generated output is disposable; shared/global caches are separate.
- [ ] Add native/Rust functionality only when a platform requirement or measured benefit justifies it.

## Historical implementation baseline

The following milestones are merged into `main`:

- [x] PR #1 - shared typed control foundation and PyChromecast transport.
- [x] PR #2 - shared `ControlService`, sent-vs-confirmed semantics, reproducible `uv` environment and quality gates.
- [x] PR #3 - status-deadline and media-input hardening.
- [x] PR #4 - Python stdio bridge, Tauri 2 shell, minimal Vite UI, Rust/frontend gates and initial `.deb` validation.
- [x] PR #5 - distinguish unavailable transport from receiver command rejection, preserving single-shot delivery.
- [x] PR #6 - bind seek confirmation to media identity.
- [x] PR #7 - bind play/pause/stop confirmation to media identity.
- [x] PR #8 - stable device selection and read-only status UI.
- [x] PR #9 - preserve PyChromecast discovery/zeroconf lifecycle and validate read-only hardware flow.
- [x] PR #10 - desktop UI accessibility/responsive consolidation.
- [x] PR #11 - one bounded deadline across seek pre-read and confirmation.
- [x] PR #12 - playback command-contract audit and deterministic no-replay coverage.
- [x] PR #13 - desktop play/pause/stop/seek controls.
- [x] PR #14 - volume/mute controls, +10-point raise protection and slider-focus correction.
- [x] PR #15 - documentation reconciliation and Community Standards alignment.

Current quality baseline recorded at PR #14/#15:

- [x] Python: 453 tests passing.
- [x] Python coverage: 97.66%.
- [x] Ruff and formatting checks pass.
- [x] mypy strict passes.
- [x] `uv pip check` passes.
- [x] Rust: 39 tests passing.
- [x] Frontend: 242 tests passing.
- [x] Hosted CI covers Python and Tauri/Rust/frontend gates.

Historical test counts in PR descriptions remain evidence for those slices, not the current totals.

---

## Phase 0 - Repository, development discipline and community files

### Completed

- [x] Repository hygiene and generated-file exclusions.
- [x] Reproducible Python 3.12 environment with `uv`, `.python-version` and committed `uv.lock`.
- [x] Setup rejects stale lockfiles or missing required tooling.
- [x] `clean`, `dist-clean` and disk-usage commands.
- [x] Cleanup coverage for Python, Tauri, Rust, frontend and Android-generated paths where applicable.
- [x] Cleanup restricted to verified project-owned paths.
- [x] Conventional Commit and pull-request workflow.
- [x] Small reviewable development slices.
- [x] GNU GPL v3 license.
- [x] `SECURITY.md`.
- [x] `CONTRIBUTING.md`.
- [x] Bug-report issue template.
- [x] Feature-request issue template.
- [x] Pull-request template.

### Remaining

- [ ] Enable GitHub Private Vulnerability Reporting.
- [ ] Verify that private vulnerability reporting works end to end.
- [ ] Decide later whether a Code of Conduct is required; it is intentionally not mandatory for the current development phase.
- [ ] Add branch protection/repository rules when the collaboration/release model requires them.

---

## Phase 1 - Shared domain and control service

### Completed

- [x] Typed device and status models.
- [x] Explicit operation results and control errors.
- [x] Shared `TvControl` / transport boundaries.
- [x] Domain/control behavior testable without UI.
- [x] GUI/MCP concerns absent from shared control behavior.
- [x] Sent state is distinct from confirmed state.
- [x] Missing, contradictory and disconnected observations remain unconfirmed.
- [x] Receiver rejection is distinct from transport unavailability.
- [x] Failed or ambiguous commands are never automatically replayed.
- [x] Confirmation reads share bounded deadlines with command workflows.
- [x] A status arriving after the deadline cannot confirm a command.
- [x] Zero confirmation budget cannot create an independent confirmation window.

### Remaining hardening

- [ ] Reject boolean `timeoutSeconds` at the service boundary.
- [ ] Reject extremely large discovery timeout values as `invalid_argument`, not `internal_error`.
- [ ] Reject extremely large seek values as `invalid_argument`, not `internal_error`.
- [ ] Make `ControlService.set_muted` itself require a boolean.
- [ ] Make `ControlService.set_volume` itself reject Python booleans as levels.
- [ ] Ensure future MCP/native clients receive the same service-level validation guarantees as the current bridge.

---

## Phase 2 - PyChromecast discovery, lifecycle and recovery

### Completed

- [x] LAN discovery through PyChromecast.
- [x] Stable UUID identity.
- [x] Duplicate friendly names remain distinguishable.
- [x] Bounded discovery/status operations.
- [x] Discovery browser/zeroconf context remains alive while cached Chromecast instances depend on it.
- [x] Repeated discovery atomically replaces the owned snapshot.
- [x] Superseded instances are disconnected before replacement.
- [x] Empty snapshots are stopped immediately.
- [x] Cleanup tolerates not-yet-started connection threads.
- [x] `close()` is idempotent.
- [x] Stale-instance cleanup respects the caller's remaining deadline.
- [x] One bounded same-UUID rediscovery may occur before command delivery when a cached connection is stale.
- [x] Recovery never replays a command after invocation starts.
- [x] Active media exposes adjusted current time; paused media preserves the last reported position.

### Real-hardware evidence already obtained

- [x] Real device discovery.
- [x] Selection by stable id.
- [x] Receiver status retrieval.
- [x] Repeated discovery found the same UUID.
- [x] Status succeeded after repeated discovery.
- [x] Repeated close completed cleanly.
- [x] End-to-end read-only UI -> Tauri -> bridge -> service -> PyChromecast path observed.
- [x] Selection change to another real device observed read-only.
- [x] Window remained responsive during discovery.

### Remaining hardware validation

- [ ] Exercise stale-connection recovery while issuing a real command.
- [ ] Validate command acknowledgement on physical hardware.
- [ ] Validate resulting state after physical commands.

---

## Phase 3 - Media command semantics

### Load media

- [x] `ControlService.load_media` exists.
- [x] Transport load support exists.
- [x] LOAD confirmation is tied to requested media identity.
- [x] `LOAD_FAILED` is explicit receiver rejection.
- [x] Invalid URLs/content types/titles are rejected before Cast calls.
- [ ] Expose `load_media` through the Python bridge.
- [ ] Expose `load_media` through Tauri.
- [ ] Expose `load_media` in the desktop UI if required.
- [ ] Decide whether loading media belongs in the first desktop release.

### Play / pause / stop

- [x] Play implemented.
- [x] Pause implemented.
- [x] Stop implemented.
- [x] Confirmation is bound to the media identity observed before command delivery.
- [x] Different or unidentified media cannot satisfy confirmation.
- [x] Exactly one command is attempted.
- [x] Delivery timeout, unavailable transport, receiver rejection, contradictory state, disappearance and late evidence are covered deterministically.

### Seek

- [x] Seek implemented.
- [x] Seek confirmation is bound to pre-command media identity.
- [x] Pre-read and post-command confirmation share one deadline.
- [x] Zero confirmation budget rejects before delivery.
- [x] Late identity cannot confirm a seek.
- [ ] Decide whether replacement sessions reusing exactly the same `content_id` require a stable media-session identifier.

### Stop semantics still requiring hardware evidence

- [x] Backend audit established that an absent media session is not invented as `IDLE` confirmation.
- [ ] Validate Stop on a real receiver.
- [ ] Decide whether the current sent-but-unconfirmed wording is acceptable when a real receiver drops the media session after Stop.
- [ ] Change semantics/wording only after observing the real behavior.

---

## Phase 4 - Desktop Tauri application

### Shell and bridge

- [x] Tauri 2 shell exists under `src-tauri/`.
- [x] Frontend exists under `ui/` with TypeScript + Vite.
- [x] Long-lived Python bridge uses line-delimited JSON over stdio with request ids.
- [x] Bridge exposes `ping`, `discover_devices`, `get_status`, `play`, `pause`, `stop`, `seek`, `set_volume`, `set_muted`.
- [x] Rust shell owns no Cast business logic.
- [x] Failed Python backend spawn is surfaced without crashing the application.
- [x] Blocking bridge work runs away from the async/main thread.
- [x] Tauri bridge calls have a bounded outer timeout.
- [x] A stuck bridge does not freeze the UI.

### Device/status UI

- [x] Device selected by stable id.
- [x] Selection remains UI state, not bridge state.
- [x] Structured error codes distinguish unavailable device, timeout, backend unavailable and bridge transport failures.
- [x] Unexpected Python exceptions become `internal_error` without killing the bridge.
- [x] No-selection, searching, empty, loading, failure, disconnected, partial and nothing-playing states exist.
- [x] Unknown receiver fields are never invented as zero/off.
- [x] At most one bridge request is in flight from the UI.
- [x] Late responses cannot overwrite the current read.

### UI/UX consolidation

- [x] Single-surface remote design.
- [x] Existing project logo reused.
- [x] Consistent inline SVG icon set.
- [x] Dropped-selection notice fixed.
- [x] Loading state distinguished from disabled state.
- [x] Live-region rebuilds reduced so unchanged state is not repeatedly announced.
- [x] Escape closes the device list and preserves focus.
- [x] Focus survives retry paths.
- [x] Details target meets the intended touch size.
- [x] Fluid layout replaced the old fixed breakpoint.
- [x] No horizontal overflow found in tested desktop/phone-width browser states.
- [x] Tested color pairs meet WCAG AA in the recorded design review.
- [ ] Run a real screen-reader validation.
- [ ] Run real touch-device interaction validation.

### Playback UI

- [x] Play/pause toggle offers the opposite of the observed state.
- [x] Stop control implemented.
- [x] Seek shown only when the receiver reports sufficient seek information.
- [x] Seek composition is a local draft.
- [x] One settled gesture sends one seek command, not a burst.
- [x] Displayed position changes only from TV-reported state.
- [x] Command in flight blocks other requests.
- [x] Unconfirmed commands preserve last reported state and offer Check state instead of resend.
- [x] Timeout wording preserves delivery ambiguity.
- [x] Real application exercised against a scripted fake-TV fleet.

### Volume UI

- [x] Volume slider implemented for connected devices.
- [x] TV-reported level is authoritative at rest.
- [x] Draft level remains local until gesture completion.
- [x] At most one command is sent per gesture.
- [x] Pointer drag and keyboard repeat do not create command storms.
- [x] Equal-to-reported level is not resent.
- [x] Unconfirmed result shows the level the TV actually reports.
- [x] Unknown volume is not represented as 0.

#### Volume-slam protection

- [x] A gesture may raise volume by at most 10 percentage points above the last TV-reported level.
- [x] Lowering is unrestricted, including directly to 0%.
- [x] The reference is the reported value, never a draft.
- [x] A confirmed/new reported value becomes the next gesture's reference.
- [x] Home goes to 0%.
- [x] End goes to reference +10, capped at 100%.
- [x] Limit is applied to the draft and again when the command is built.
- [x] Pointer/key release completes a gesture even when WebKit suppresses `change` after clamping.
- [x] No confirmation dialog, override mode or automatic retry.
- [x] Fake-TV real-app validation covered large jumps, repeated gestures, keyboard behavior and 100%/0% edges.
- [ ] Decide whether the same safety policy must be enforced below the UI for future MCP/native clients.

### Mute UI

- [x] Mute/unmute implemented.
- [x] Service/bridge command is absolute state, not toggle semantics.
- [x] UI requests the opposite of the TV-reported mute state.
- [x] Unknown mute state does not become false/unmuted.
- [x] Rapid repeated clicks do not queue commands.
- [x] Unconfirmed mute does not become success.

### Focus behavior

- [x] Volume and seek no longer steal focus back after a command when the user deliberately moved focus elsewhere.
- [x] Focus is restored when its loss came only from temporarily disabling the same slider.
- [x] Success, unconfirmed and failure paths are covered.
- [ ] Validate with a real screen reader/assistive technology.
- [ ] Investigate the safe edge case where assistive technology changes a clamped slider without pointer/key release; current behavior prefers no surprise send.

### Volume capability model

- [ ] Carry PyChromecast `volume_control_type` into shared receiver status.
- [ ] Distinguish fixed-volume receivers from adjustable receivers.
- [ ] Hide/disable misleading volume controls for fixed-volume receivers.
- [ ] Coordinate model, adapter, bridge and UI changes as one slice.

### Volume confirmation tolerance

- [ ] Evaluate the current 0.01 tolerance on physical receivers.
- [ ] Decide how stepped-volume receivers should be treated.
- [ ] Avoid false failure while still displaying only the TV-reported level.

---

## Phase 4b - Desktop native integration / GNOME tray

This phase is not started.

- [ ] Add a GNOME/Linux system tray/status indicator using the control-TV icon.
- [ ] Provide `Open control-TV`.
- [ ] Provide `Quit`.
- [ ] Decide whether closing the main window hides it to the tray or exits the application.
- [ ] Keep an explicit distinction between closing/hiding the window and quitting the process.
- [ ] Expose play/pause from the tray when state permits.
- [ ] Expose Stop from the tray when state permits.
- [ ] Expose mute/unmute from the tray.
- [ ] Add volume only if tray UX can do it safely and clearly.
- [ ] Reuse the authoritative shared control path; do not duplicate Cast logic in Rust.
- [ ] Preserve confirmed/unconfirmed/not-checked semantics.
- [ ] Preserve single-shot/no-replay behavior.
- [ ] Ensure tray state never invents receiver state.
- [ ] Validate tray behavior on the real GNOME desktop.
- [ ] Validate tray-issued commands on a real Chromecast only after command semantics are approved.
- [ ] Structure native integration so equivalent Windows tray support can reuse it later.

Exit criteria:

- [ ] Tray can open the application and quit it predictably.
- [ ] Window-close behavior is documented and tested.
- [ ] Tray controls use the same backend semantics as the main UI.
- [ ] GNOME integration is physically validated.

---

## Phase 5 - Physical Chromecast/Google TV validation

Read-only hardware validation is substantially complete. Command validation is not.

### Already established

- [x] Discovery on real hardware.
- [x] Stable-id selection.
- [x] Receiver status.
- [x] Rediscovery and status after rediscovery.
- [x] Repeated close.
- [x] Desktop UI read-only end-to-end path.

### Playback command session

One hardware session sent a single Pause command. The physical playback stopped and the UI showed Paused, but the application command result was `UNCONFIRMED`. A later read-only state check happened after the media session had disappeared and cannot be attributed to that Pause. This is evidence of a real effect, but it is not sufficient to mark the command contract physically validated.

- [ ] Recreate a fresh seekable media session under controlled conditions.
- [ ] Re-test Pause and record both physical effect and application confirmation result.
- [ ] Re-test Play after Pause.
- [ ] Re-test Seek to a clearly observable position.
- [ ] Test Stop last because the receiver may drop the media session.
- [ ] Determine whether Pause's observed `UNCONFIRMED` result is reproducible and why.
- [ ] Decide Stop wording/confirmation behavior from observed receiver behavior.
- [ ] Never replay an ambiguous command automatically.

### Volume/mute hardware validation

- [ ] Test a conservative volume increase on a real receiver.
- [ ] Confirm +10-point UI protection behaves as intended with real reported state.
- [ ] Test volume decrease.
- [ ] Test mute.
- [ ] Test unmute.
- [ ] Observe receiver volume quantization/stepping and evaluate tolerance.
- [ ] Determine actual `volume_control_type` behavior on the receiver.

Private identifiers remain in local/private validation notes only.

---

## Phase 6 - MCP adapter

Not started.

- [ ] Define MCP tools over the same shared `ControlService` behavior.
- [ ] Discovery/list-device tool.
- [ ] Status tool.
- [ ] Play/pause/stop/seek tools.
- [ ] Volume/mute tools.
- [ ] Add load-media only if product scope approves it.
- [ ] Preserve sent-vs-confirmed semantics in MCP responses.
- [ ] Preserve error codes and delivery ambiguity.
- [ ] No automatic replay.
- [ ] Decide whether volume +10 protection belongs in the service/MCP policy rather than only the desktop UI.
- [ ] Add deterministic MCP tests with fake transport.
- [ ] Validate MCP and GUI do not diverge in behavior.

Exit criteria:

- [ ] MCP and GUI share one authoritative control implementation.
- [ ] No Cast business logic is duplicated in the MCP layer.
- [ ] Safety/confirmation semantics match the desktop application.

---

## Phase 7 - Packaging and distributable desktop applications

### Linux/Debian

- [x] A debug `.deb` was built, installed, launched from `/usr/bin/control-tv` and uninstalled successfully during PR #4 validation.
- [ ] Remove dependency on the developer repository `.venv`.
- [ ] Choose and implement a distributable Python-runtime strategy.
- [ ] Ensure PyChromecast and required Python dependencies ship reproducibly.
- [ ] Produce an installable `.deb` that works on a clean target without the source checkout.
- [ ] Validate install, launch, discovery, control and uninstall on a clean Debian/Ubuntu-family system.
- [ ] Verify desktop entry, icon and tray integration in packaged form.

### Windows

- [ ] Decide Python-runtime packaging for Windows.
- [ ] Produce Windows installer/package.
- [ ] Validate launch without a development checkout.
- [ ] Validate discovery and controls on real Windows hardware.
- [ ] Add native tray integration using the shared desktop-native structure.
- [ ] Validate install/uninstall/upgrade behavior.

### Tauri configuration/security

- [ ] Replace development CSP settings with a production policy before release.
- [ ] Verify packaged application permissions/capabilities are minimal.
- [ ] Align application version and package metadata.
- [ ] Verify GPL-3.0 license metadata is correct in all package formats.

---

## Phase 7b - Android application and home-screen widget

Blocked on an architecture decision. Do not start implementation until this is resolved.

### Architecture decision

- [ ] Decide how the Python/PyChromecast runtime is provided on Android.
- [ ] Decide how Android reuses the authoritative shared control core without rewriting Cast semantics.
- [ ] Document lifecycle, networking, background-execution and packaging implications.
- [ ] Confirm the chosen design can support both the full Android application and a home-screen widget.

### Android application

- [ ] Build Android target/application.
- [ ] Produce installable APK.
- [ ] Device discovery and stable-id selection.
- [ ] Receiver/media status.
- [ ] Play/pause/stop/seek where supported.
- [ ] Volume/mute where supported.
- [ ] Preserve sent-vs-confirmed and no-replay semantics.
- [ ] Handle Android lifecycle/background restrictions.
- [ ] Validate on real Android hardware.

### Home-screen widget

- [ ] Widget shows selected device.
- [ ] Widget shows only real reported state.
- [ ] Play/pause control.
- [ ] Stop control where meaningful.
- [ ] Mute/unmute control.
- [ ] Volume control only if interaction is safe and understandable.
- [ ] Clear state when no device is selected.
- [ ] Clear state when selected device is unavailable.
- [ ] No invented confirmation after commands.
- [ ] Widget refresh/state synchronization strategy.
- [ ] Validate widget on real Android hardware.

---

## Phase 8 - CI/CD, security and release engineering

### CI/CD

- [x] Python lint/type/test/coverage gates exist.
- [x] Rust/frontend Tauri-shell CI job exists.
- [x] Dependency compatibility check exists.
- [ ] Add packaging CI after distributable runtime strategy is implemented.
- [ ] Add Windows build validation.
- [ ] Add Android build validation after architecture decision.
- [ ] Decide release artifact signing strategy.
- [ ] Add release checks that prevent publishing development-only runtime assumptions.

### Security

- [x] `SECURITY.md` exists.
- [ ] Enable private vulnerability reporting on GitHub.
- [ ] Verify reporting flow.
- [ ] Review Tauri CSP before stable release.
- [ ] Review bridge input validation before exposing MCP or other external callers.
- [ ] Review dependency/update policy before stable release.
- [ ] At first stable release, replace development-only support wording in `SECURITY.md` with an explicit supported-version matrix.
- [ ] Define policy for older releases.
- [ ] Revisit `SECURITY.md` whenever support lifecycle changes.

### Versioning and license metadata

- [ ] Choose the first public release version.
- [ ] Keep Python, Tauri, desktop package and future Android versions coherent.
- [ ] Ensure GPL-3.0 metadata is present in all distributable artifacts.

---

## Phase 9 - Release readiness

A stable release is not ready until all required platform-specific items below are complete.

- [ ] Physical playback validation completed and documented.
- [ ] Physical volume/mute validation completed and documented.
- [ ] Stop confirmation/wording decision made from hardware evidence.
- [ ] Fixed-volume receiver behavior modeled correctly.
- [ ] Volume tolerance decision made from hardware evidence.
- [ ] Service-level input hardening complete.
- [ ] Distributable Python runtime implemented.
- [ ] Linux package works without repository `.venv`.
- [ ] Production CSP configured.
- [ ] Version/license metadata finalized.
- [ ] Private vulnerability reporting enabled and tested.
- [ ] `SECURITY.md` stable-release support matrix updated.
- [ ] Accessibility validation scope documented; screen-reader/touch validation performed where required.
- [ ] Windows release validated if Windows is part of that release.
- [ ] Android/APK validated if Android is part of that release.
- [ ] MCP validated if MCP is part of that release.

---

## Known open technical items

The unchecked items below are known issues or decisions, not regressions silently considered complete.

- [ ] Distributable Python runtime for packaged desktop applications.
- [ ] Production Tauri CSP.
- [ ] Discovery timeout boolean/overflow validation.
- [ ] Seek huge-number overflow validation.
- [ ] `ControlService.set_muted` boolean validation.
- [ ] `ControlService.set_volume` boolean rejection.
- [ ] Receiver `volume_control_type` propagation.
- [ ] Real-hardware evaluation of 0.01 volume tolerance.
- [ ] Assistive-technology slider release edge case.
- [ ] `load_media` exposure/product decision.
- [ ] Automatic/periodic status refresh strategy.
- [ ] Stable media-session identity if same-`content_id` replacement becomes a real problem.
- [ ] Stop wording/confirmation after physical validation.
- [ ] Version synchronization and license metadata.
- [x] Volume/seek slider deliberate-focus move bug fixed in PR #14 and automation-validated.

---

## Immediate execution order

This is the recommended next sequence based on dependencies, not a claim that later phases cannot be developed in parallel.

1. [ ] Review and adopt this rebuilt development plan.
2. [ ] Resume controlled physical playback validation with a fresh seekable media session: Pause -> Play -> Seek -> Stop last.
3. [ ] Investigate the real Pause effect returning `UNCONFIRMED` if reproduced.
4. [ ] Perform conservative real-hardware volume/mute validation and evaluate volume tolerance/control type.
5. [ ] Close the small service-input hardening issues found during audits.
6. [ ] Decide whether `load_media` belongs in the first desktop release.
7. [ ] Implement GNOME tray/native desktop integration.
8. [ ] Choose the distributable Python-runtime strategy and produce a self-contained Linux package.
9. [ ] Implement MCP on top of the shared service if it belongs in the first release scope.
10. [ ] Decide Android runtime/core-sharing architecture before Android or widget implementation.
11. [ ] Implement Windows and Android packaging according to release scope.
12. [ ] Complete security, versioning, packaging and release-readiness gates.

## Review requirement for this reconstruction

Before this file replaces `DEVELOPMENT_PLAN.md`:

- [ ] Compare it against current `main` `DEVELOPMENT_PLAN.md`.
- [ ] Compare it against PR #1 through PR #15 descriptions and review findings.
- [ ] Compare it against surviving feature branches where they contain unmerged evidence or constraints.
- [ ] Confirm no still-relevant requirement was lost from the pre-PR-15 local plan.
- [ ] Confirm every `[x]` has evidence at the level claimed by its wording.
- [ ] Confirm real-hardware observations are not upgraded from observed effect to confirmed command result.
- [ ] Confirm no private network/device data is present.
- [ ] Replace the old plan only after this review; do not keep two authoritative plans.
