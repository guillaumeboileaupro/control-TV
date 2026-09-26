# Development plan

## Goal

Build a lightweight standalone Chromecast / Google TV controller for Android, Windows and Debian/Ubuntu. Manual control must work independently of ChatGPT or MCP. A future MCP adapter exposes the same authoritative control capabilities to an external assistant.

Python and the Python Chromecast ecosystem remain part of the implementation, with `pychromecast` behind a focused transport adapter. Tauri 2 provides the cross-platform application shell and packaging layer. Rust/native components are added only where the shell or a platform integration requires them.

This plan is the project source of truth for completed work, validation boundaries, known limitations and the remaining roadmap. It reconstructs the useful history accumulated across the feature branches and the successive `DEVELOPMENT_PLAN.md` commits while preserving the current audited state.

## Progress rule

Two kinds of evidence are kept separate everywhere in this plan:

- **automation-validated** means deterministic tests, CI, mutation checks where used, or the real application driven against a fake TV / fake transport;
- **real-hardware validated** means the behavior was actually observed on a real Chromecast/Google TV or on the stated target platform, with the evidence recorded.

Rules:

- [x] `[x]` is the historical checkbox convention for completed items; do not mechanically rewrite it as `[X]`.
- [x] A checked implementation line never implies that the matching physical-device validation is complete.
- [x] A command being sent is distinct from the resulting state being confirmed.
- [x] `UNCONFIRMED` is never presented as confirmed success.
- [x] Ambiguous delivery or confirmation never causes an automatic command replay.
- [x] UI state follows observations reported by the TV; the UI does not invent receiver state.
- [x] Private device names, UUIDs, IP/MAC addresses and media titles stay out of committed public documentation.

## Architecture constraints

- [x] Python and the Python Chromecast ecosystem are part of the architecture, including `pychromecast` for applicable Cast capabilities.
- [x] Tauri 2 is the cross-platform application shell and packaging layer.
- [x] The current desktop UI is vanilla TypeScript + Vite.
- [x] GUI and future MCP integration share the same authoritative control/domain behavior.
- [x] Cast business logic is not duplicated in Rust or TypeScript.
- [x] Media/service resolution remains separated from low-level Cast transport where practical.
- [x] Manual application control operates independently of ChatGPT/MCP.
- [x] Build output, platform intermediates and temporary files are controlled and disposable.
- [x] Project cleanup is limited to verified project-owned generated output; shared/global caches remain separate.
- [ ] Add native/Rust components only where a measured or platform requirement justifies them.

## Current baseline

The latest merged desktop/control slice is PR #14, followed by the documentation reconciliation in PR #15.

- [x] Python suite: 453 tests passing at 97.66% coverage on the final PR #14 validation.
- [x] Python quality gates include Ruff, formatting, `mypy --strict` and `uv pip check`.
- [x] Rust suite: 39 tests passing on the final PR #14 validation.
- [x] Frontend suite: 242 tests passing after the volume/seek focus-return correction.
- [x] Hosted CI covers Python quality/test/coverage and the Tauri/Rust/frontend gates.
- [x] PR #12 backend audit is merged.
- [x] PR #13 playback UI is merged.
- [x] PR #14 volume/mute UI, +10 raise protection and focus correction are merged.
- [x] PR #15 documentation reconciliation is merged.

Historical test counts below are retained when they are evidence for a particular development slice. They are not the current global totals.

---

## Phase 0 - Repository and development discipline

Deliverables:

- [x] repository hygiene and generated-file exclusions;
- [x] documented architecture and development plan;
- [x] reproducible local development commands;
- [x] explicit `clean`, `dist-clean` and disk-usage inspection commands;
- [x] cleanup coverage extended to verified Tauri, Rust, frontend and Android output paths;
- [x] small, reviewable iterations and Conventional Commit / pull-request workflow;
- [x] reproducible Python 3.12 environment pinned with `uv`, `.python-version` and committed `uv.lock`;
- [x] setup fails when the lockfile is stale or required tooling is unavailable.

Community standards:

- [x] GNU GPL v3 license;
- [x] `SECURITY.md`;
- [x] `CONTRIBUTING.md`;
- [x] bug-report issue template;
- [x] feature-request issue template;
- [x] pull-request template;
- [ ] Code of Conduct intentionally deferred until it is useful for the project/community.

Exit criteria:

- [x] project-owned versus shared caches are clearly distinguished;
- [x] architecture is documented consistently across project context files;
- [x] quality commands are reproducible;
- [x] documentation distinguishes automation evidence from physical evidence.

---

## Phase 1 - Shared control foundation

Deliverables:

- [x] shared control/domain interfaces usable independently of the GUI;
- [x] Python project/package structure and typed device/connection models;
- [x] typed receiver/media status;
- [x] explicit errors and operation results;
- [x] selected Python Chromecast modules isolated behind a focused adapter;
- [x] deterministic unit tests for domain and shared control behavior;
- [x] explicit confirmation and unconfirmed observations;
- [x] GUI/MCP concerns absent from the domain/shared control layer.

Confirmation contract:

- [x] missing, contradictory and disconnected observations remain explicitly unconfirmed;
- [x] `CastTransport.get_status` accepts an explicit timeout;
- [x] `ControlService._verify` passes only the confirmation budget actually remaining before each read;
- [x] status arriving after the deadline cannot confirm a command;
- [x] PyChromecast connection, bounded recovery and status round-trip honor the caller's remaining deadline;
- [x] zero confirmation budget cannot create an independent confirmation window;
- [x] deterministic tests cover blocked reads, exact timeout propagation and deadline boundaries.

Known service-input hardening still open:

- [ ] reject boolean `timeoutSeconds` in discovery at the service/bridge boundary;
- [ ] reject huge discovery timeout numbers as `invalid_argument` rather than `internal_error`;
- [ ] reject huge seek values as `invalid_argument` rather than `internal_error`;
- [ ] make `ControlService.set_muted` itself enforce a boolean;
- [ ] make `ControlService.set_volume` itself reject Python booleans as levels;
- [ ] keep these guarantees at the authoritative service boundary so future MCP/native clients cannot bypass them.

---

## Phase 2 - Chromecast discovery, lifecycle and read-only status

Discovery and lifecycle:

- [x] LAN discovery through PyChromecast;
- [x] stable device identity uses UUID/stable id rather than display name;
- [x] duplicate display names remain distinguishable;
- [x] bounded discovery timeout;
- [x] receiver/device status retrieval;
- [x] discovery/zeroconf context remains alive for cached Chromecast instances;
- [x] repeated discovery atomically replaces the owned snapshot;
- [x] superseded instances are disconnected before replacement;
- [x] empty discovery snapshots are stopped immediately;
- [x] cleanup tolerates connection threads that have not started;
- [x] repeated `close()` is safe;
- [x] superseded-instance cleanup uses the remaining status deadline;
- [x] one bounded same-UUID rediscovery is permitted before command delivery when a cached connection is stale;
- [x] recovery never replays a command after invocation begins;
- [x] `adjusted_current_time` is exposed for actively playing media while paused media preserves the last reported position.

Historical validation:

- [x] 289-test/read-only hardware slice validated repeated discovery, same UUID, status reads and repeated close;
- [x] review fixes raised the corresponding Python slice to 291 tests with 97.76% coverage and caught the targeted mutations;
- [x] adapter defect found during PR #8 hardware validation was fixed by PR #9: discovery no longer destroys the zeroconf context required by cached devices.

Real-hardware read-only validation:

- [x] compatible device discovered on the LAN;
- [x] device selected by stable id;
- [x] connected receiver status read;
- [x] repeated discovery found the same UUID and status remained readable;
- [x] repeated close completed cleanly;
- [x] real desktop UI -> Tauri -> bridge -> `ControlService` -> PyChromecast path validated read-only;
- [x] changing selection to another real device validated read-only;
- [x] window remained responsive during real discovery/status work.

Still required on real hardware:

- [ ] exercise stale-connection recovery while executing a real command;
- [ ] validate command acknowledgement semantics;
- [ ] validate the resulting state after real commands.

---

## Phase 3 - Command semantics and playback hardening

General command contract:

- [x] command delivery is distinct from state confirmation;
- [x] explicit `confirmed`, `unconfirmed` and `not_checked` confirmation states;
- [x] exactly one command attempt after invocation begins;
- [x] no automatic replay after ambiguous delivery;
- [x] receiver-declared rejection remains distinct from transport unavailability;
- [x] failed transport calls remain distinct from delivered-but-unconfirmed commands.

Load media:

- [x] `ControlService.load_media` implemented;
- [x] transport load support implemented;
- [x] LOAD confirmation is bound to the requested media identity/URL;
- [x] `MEDIA_STATUS` is the successful terminal LOAD response;
- [x] `LOAD_FAILED` is treated as receiver rejection;
- [x] invalid URLs/content types/titles are rejected before the Cast call;
- [ ] expose `load_media` through the Python bridge;
- [ ] expose `load_media` through Tauri;
- [ ] expose `load_media` in the UI;
- [ ] decide whether loading media is required for the first desktop release.

Play / pause / stop:

- [x] play implemented;
- [x] pause implemented;
- [x] stop implemented;
- [x] confirmation cannot be satisfied by a different media item;
- [x] missing/empty/whitespace media identity is not usable confirmation evidence;
- [x] pre-command identity snapshot and post-command verification share one confirmation deadline;
- [x] delivery timeout, unavailable/rejected delivery, contradictory state, late evidence and post-send media disappearance are covered deterministically.

Seek:

- [x] seek implemented where the receiver reports support;
- [x] seek confirmation is bound to the pre-command `content_id`;
- [x] different or unidentified media cannot satisfy position confirmation;
- [x] pre-command status and post-command confirmation share one deadline;
- [x] zero confirmation budget rejects before command delivery;
- [x] late identity cannot confirm a command;
- [x] capability guard is preserved at the deadline;
- [ ] distinguish replacement media sessions that reuse exactly the same `content_id`, potentially by carrying a stable media-session identifier.

Playback contract test branches:

- [x] dedicated playback-command-contract work covered success and failure paths;
- [x] failed playback attempts are tracked explicitly rather than disappearing from the command contract;
- [x] command hardening preserves the no-replay invariant.

Stop-specific open question:

- [x] PR #12 audited the backend and confirmed that stop does not invent `IDLE` when the media session disappears;
- [ ] validate Stop on a real receiver and decide whether the resulting sent-but-unconfirmed wording is acceptable when the receiver drops the media session.

---

## Phase 4 - Tauri desktop application

### 4a - Application shell and bridge

- [x] Tauri 2 shell scaffolded in `src-tauri/`;
- [x] minimal vanilla TypeScript + Vite frontend in `ui/`;
- [x] shell owns no Cast business logic;
- [x] Rust spawns `src/control_tv/bridge.py` as a long-lived child process;
- [x] bridge uses line-delimited JSON over stdin/stdout with request ids;
- [x] `ControlService` is not reimplemented in Rust or TypeScript;
- [x] bridge exposes `ping`, `discover_devices`, `get_status`, `play`, `pause`, `stop`, `seek`, `set_volume` and `set_muted`;
- [x] failed bridge spawn returns an explicit backend-unavailable error instead of crashing the application;
- [x] blocking bridge calls run through a worker thread rather than the async/main thread;
- [x] Tauri command timeout bounds a slow or stuck bridge request;
- [x] deterministic Rust tests cover success, child exit and no-response timeout;
- [x] real dev-mode window remained responsive during real discovery.

Current bridge limitation:

- [ ] replace the current one-request-at-a-time stdio behavior if future concurrency requirements justify it;
- [ ] decide whether a timed-out underlying worker/bridge operation needs cancellable process-level handling rather than merely bounded UI waiting.

### 4b - Device selection and status UI

- [x] discovered device selected by stable id, never display name;
- [x] selection remains UI state and the bridge stays stateless;
- [x] `get_status` path is bridge -> Tauri -> `ControlService` without duplicated Cast logic;
- [x] structured failures distinguish control errors, backend unavailable, bridge timeout and bridge transport failure;
- [x] unexpected Python exceptions become `internal_error` responses rather than killing the bridge;
- [x] UI covers no-selection, searching, empty, loading, failure, disconnected, partial and nothing-playing states;
- [x] unreported values are shown as not reported, never invented as zero/off;
- [x] raw internal details are kept behind the Details disclosure;
- [x] UI permits at most one bridge request in flight;
- [x] late answers cannot overwrite the current read;
- [x] initial model slice had 58 TypeScript tests;
- [x] real desktop read-only run validated discovery, selection, selection change and rediscovery retaining selection.

Status-refresh work still open:

- [ ] add an appropriate status refresh strategy so external TV/remote changes appear without requiring manual refresh;
- [ ] preserve request ordering and stale-response protection when refresh becomes automatic.

### 4c - UI design and accessibility baseline

- [x] light single-surface remote design rather than a dashboard;
- [x] existing `assets/logo.svg` reused unchanged;
- [x] consistent inline SVG icon set;
- [x] selected device presented as one row expanding into the device list;
- [x] playback controls appear only when the TV reports applicable media/capabilities;
- [x] UI design review covered real and fake states;
- [x] dropped-selection notice, loading readability, repeated live-region announcements, Escape behavior, retry focus, Details target size and fluid breakpoint issues were corrected;
- [x] no horizontal overflow measured at 320, 390, 481, 768 and 1280 px across stress states;
- [x] measured colour pairs meet WCAG AA for the reviewed palette;
- [x] keyboard paths exercised in the real window/browser;
- [ ] perform dedicated screen-reader validation before claiming accessibility completion.

### 4d - Playback controls UI

- [x] play/pause toggle reflects the opposite of observed state;
- [x] Stop control implemented;
- [x] seek slider appears only for seekable media with known duration and position;
- [x] seek composition is a local draft;
- [x] one seek is sent after the control settles, never a command burst;
- [x] displayed playback position changes only through TV-reported status;
- [x] command in flight blocks competing requests;
- [x] rapid triple click sends one command;
- [x] sent-but-unconfirmed command keeps the last reported state and offers Check state rather than resending;
- [x] timeout wording preserves ambiguity about whether the command arrived;
- [x] playback slice validated 367 Python tests at 97.61%, 28 Rust tests and 115 TypeScript tests at that point;
- [x] real Tauri application exercised against 15 scriptable fake TVs outside the repository;
- [x] fake-TV validation covered confirmed pause/play/seek, pending, ignored, late-action, refused, unreachable, send-timeout, live media, unknown length, missing capability, buffering, idle, nothing-playing and Stop dropping its media session;
- [x] no horizontal overflow across the tested playback states and widths.

### 4e - Volume and mute

- [x] bridge forwards absolute `set_volume` and `set_muted` commands;
- [x] volume bridge input is a number from 0 to 1, not a boolean;
- [x] mute bridge input is a JSON boolean and represents an absolute state, not a toggle at the service boundary;
- [x] same sent-versus-confirmed contract as playback controls;
- [x] mute button and volume slider shown for a connected TV whether or not media is playing;
- [x] slider edits a local draft while the user is interacting;
- [x] one command is sent after the gesture settles;
- [x] at most one request is in flight;
- [x] shown volume remains the TV-reported value;
- [x] unconfirmed volume reports what the TV actually reports and is never resent automatically;
- [x] rapid mute clicks collapse to one command;
- [x] fake-TV real-application validation covered drag, quick key presses, held key, repeated mute click and ignored commands without command storms;
- [x] real read-only status displayed a real receiver's reported volume and mute state without sending a command;
- [ ] physically validate volume and mute commands on a real adjustable receiver.

Volume-slam protection:

- [x] one gesture may raise volume by at most 10 percentage points above the last TV-reported level;
- [x] lowering is unrestricted, including directly to 0%;
- [x] the TV-reported value before the gesture is the reference;
- [x] after confirmation/new status, the new reported level becomes the next reference;
- [x] Home goes to 0%;
- [x] End goes to reference +10, never above 100%;
- [x] cap is applied to draft behavior and again when building the command;
- [x] pointer/key release ends a gesture even when WebKit suppresses `change` because the value was clamped;
- [x] no confirmation dialog, override gesture, extra timer or special mode;
- [x] limit hint is shown while the draft is capped;
- [x] fake-TV real-app validation covered repeated +10 steps, lowering to zero, 93 -> 100 and no command when already at 100;
- [x] phone-width review found no horizontal overflow; hint may occupy a second line while capped.

Volume/mute known issues:

- [ ] expose PyChromecast `volume_control_type` in `ReceiverStatus` so fixed-volume receivers do not look adjustable;
- [ ] evaluate the current 0.01 volume confirmation tolerance on stepped-volume hardware;
- [ ] decide how quantized receiver levels should be confirmed without inventing success;
- [ ] service-level `set_muted` boolean validation remains to be hardened as noted in Phase 1;
- [ ] assistive-technology value changes that produce neither key nor pointer release can leave a capped draft unsent until a later release/refresh; no command is sent unexpectedly;
- [ ] the +10 protection currently belongs to the UI and does not automatically protect future MCP/native clients.

### 4f - Focus behavior after commands

- [x] PR #14 review found that volume could pull focus back after the user deliberately moved elsewhere while waiting for the network;
- [x] shared focus-intent mechanism fixes the issue for volume and seek;
- [x] focus is restored only when its loss came from temporary disabling of the slider;
- [x] deliberate focus movement to another control is preserved;
- [x] tests cover success, `UNCONFIRMED`, failure and independence of volume/seek behavior;
- [x] final frontend suite after this correction is 242 tests.

### 4g - Desktop native integration / tray

This is the next desktop-native feature slice after the current controller behavior is stable.

- [ ] add a native system-tray/status icon for control-TV;
- [ ] reuse the control-TV icon appropriately for the tray;
- [ ] provide `Open control-TV`;
- [ ] provide `Quit`;
- [ ] decide and document the distinction between closing the main window and quitting the application;
- [ ] provide play/pause from the tray when meaningful;
- [ ] provide Stop from the tray when meaningful;
- [ ] provide mute/unmute from the tray;
- [ ] evaluate whether volume control belongs directly in the tray UX;
- [ ] reflect selected-device/current status without inventing state;
- [ ] preserve confirmed/unconfirmed semantics;
- [ ] do not duplicate Cast business logic in Rust;
- [ ] validate tray lifecycle and controls on the real GNOME desktop;
- [ ] validate any tray-issued Cast commands separately on real hardware;
- [ ] keep the native integration architecture reusable for Windows where practical.

Exit criteria for Phase 4:

- [x] frontend remains separated from Cast/control business logic;
- [x] playback, seek, volume and mute are automation-validated through the real app against fake TVs;
- [ ] required real-hardware command behavior is validated and recorded;
- [ ] tray/native desktop integration is complete if it is included in the first desktop release scope.

---

## Phase 5 - Real-hardware command validation

Hardware validation is deliberately separate from automated completion.

Read-only evidence already established:

- [x] discovery;
- [x] stable-id selection;
- [x] receiver status;
- [x] rediscovery preserving the same device identity;
- [x] selection change;
- [x] real reported volume/mute display;
- [x] desktop responsiveness during read-only network operations.

Playback command session already attempted:

- [x] exactly one real Pause command was sent during the controlled hardware session;
- [x] the physical media playback was observed to pause;
- [x] the application result for that Pause was `UNCONFIRMED`;
- [x] only a later read-only Check state was performed; no replay was sent;
- [x] hours later the media session had disappeared and the receiver was idle, which is too late to attribute to Pause;
- [ ] therefore Pause is **not** considered contractually hardware-validated yet.

Still required in a fresh controlled seekable-media session:

- [ ] reproduce Pause and record both physical effect and application confirmation result;
- [ ] validate Play;
- [ ] validate Seek;
- [ ] validate Stop last, because it may destroy the media session;
- [ ] judge Stop wording when the session disappears;
- [ ] validate volume increase/decrease on an actually adjustable receiver;
- [ ] validate mute and unmute;
- [ ] evaluate stepped-volume behavior and the 0.01 tolerance;
- [ ] evaluate stale-connection recovery during a real command;
- [ ] record evidence without committing private network/device/media identifiers.

No unchecked hardware item may be promoted to complete from fake-TV evidence alone.

---

## Phase 6 - MCP adapter

The MCP layer is a future client of the shared control service, not a second implementation.

Deliverables:

- [ ] define MCP tool schemas for discovery, status and supported commands;
- [ ] map MCP inputs to the same authoritative `ControlService` validation;
- [ ] expose stable device identifiers safely;
- [ ] expose sent/confirmed/unconfirmed results without flattening ambiguity;
- [ ] never auto-replay an ambiguous command;
- [ ] keep MCP optional so manual application control works independently;
- [ ] decide whether `load_media` is part of the MCP surface;
- [ ] decide whether the UI-only +10 volume protection needs an equivalent MCP policy;
- [ ] deterministic MCP tests with fake transport;
- [ ] real-hardware validation for MCP-issued commands only after the desktop/control contract is accepted.

Exit criteria:

- [ ] GUI and MCP demonstrably share the same authoritative control behavior;
- [ ] no Cast business logic is duplicated in the MCP adapter;
- [ ] MCP errors and confirmation semantics remain explicit.

---

## Phase 7 - Desktop packaging and distribution

### Linux / Debian-Ubuntu

Already demonstrated:

- [x] Tauri debug `.deb` built with `npx --prefix ui tauri build --debug --bundles deb`;
- [x] package installed with `dpkg -i`;
- [x] installed `/usr/bin/control-tv` launched successfully;
- [x] package cleanly uninstalled after validation.

Current blocker:

- [ ] the current `.deb` resolves Python through the developer repository `.venv`, so it is not distributable;
- [ ] design and implement a self-contained Python runtime/backend packaging strategy;
- [ ] verify packaged PyChromecast and dependencies without a development checkout;
- [ ] validate install, launch, discovery/control and uninstall on a clean Debian/Ubuntu target;
- [ ] ensure cleanup tooling never removes user/global caches outside project-owned output.

### Windows

- [ ] define Windows packaging target and installer strategy;
- [ ] integrate the self-contained Python/backend runtime strategy;
- [ ] validate Tauri application startup and bridge lifecycle on Windows;
- [ ] validate Chromecast discovery/status on Windows;
- [ ] validate control commands on Windows with the same confirmation contract;
- [ ] reuse native/tray architecture where practical;
- [ ] produce installable release artifact.

### Tauri hardening

- [ ] replace the current permissive/unfinished CSP configuration with an explicit production Content Security Policy;
- [ ] review production capabilities/permissions before release;
- [ ] verify no development-only paths or assumptions remain in release bundles.

---

## Phase 7b - Android application and home-screen widget

Android is not just a packaging checkbox because the current authoritative controller depends on Python/PyChromecast.

Architecture decision first:

- [ ] decide how the authoritative Python/PyChromecast control core is delivered or reused on Android;
- [ ] compare embedding Python, introducing a local service/runtime, or another architecture that preserves one authoritative behavior;
- [ ] document the chosen boundary before implementing the Android client;
- [ ] do not silently rewrite Cast behavior in a second independent implementation.

Android application:

- [ ] create Android application shell;
- [ ] reuse shared device/status/command semantics;
- [ ] discovery and stable-id device selection;
- [ ] status view;
- [ ] play/pause;
- [ ] stop;
- [ ] seek where appropriate;
- [ ] mute/unmute;
- [ ] volume;
- [ ] correct behavior when no device is selected;
- [ ] correct behavior when the selected device becomes unavailable;
- [ ] preserve sent/confirmed/unconfirmed semantics;
- [ ] produce installable APK;
- [ ] validate on a real Android device;
- [ ] validate real Chromecast interaction separately.

Home-screen widget:

- [ ] add an Android home-screen widget after the Android control architecture is settled;
- [ ] show selected device/current state without inventing values;
- [ ] play/pause action;
- [ ] Stop action if appropriate for the widget size/UX;
- [ ] mute/unmute action;
- [ ] volume control if Android widget interaction constraints make it usable;
- [ ] useful no-device/unavailable states;
- [ ] widget actions use the same authoritative command semantics as the app;
- [ ] validate widget lifecycle, refresh and actions on a real Android device.

---

## Phase 8 - CI/CD, security and release engineering

CI/CD:

- [x] Python lint/type/test/coverage gates in hosted CI;
- [x] Rust/frontend/Tauri shell gates in hosted CI;
- [x] PR workflow exercises the current quality gates;
- [ ] add platform build matrix as Linux/Windows/Android release targets become real;
- [ ] add packaging smoke tests where practical;
- [ ] add release artifact generation/signing strategy.

Security:

- [x] `SECURITY.md` exists;
- [x] public documentation avoids committing private receiver/network identifiers;
- [ ] enable GitHub Private Vulnerability Reporting;
- [ ] verify the private reporting path works after enabling it;
- [ ] update `SECURITY.md` when the first stable release defines supported versions;
- [ ] define the policy for older supported/unsupported versions;
- [ ] review the security policy whenever the support cycle changes;
- [ ] review Tauri CSP/capabilities for production;
- [ ] review dependency and supply-chain update process before stable release.

Versioning and metadata:

- [ ] establish one authoritative application version across Python/Tauri/frontend/package metadata;
- [ ] remove inconsistent placeholder versions before release;
- [ ] ensure package metadata consistently declares GPL-3.0 licensing;
- [ ] define release/tag/version bump procedure;
- [ ] create release notes/changelog strategy.

---

## Phase 9 - Release readiness

Functional gates:

- [ ] required desktop commands validated on real hardware;
- [ ] Stop semantics accepted after physical validation;
- [ ] volume-control capability and stepped-volume behavior resolved;
- [ ] required status-refresh behavior implemented;
- [ ] `load_media` release scope decided;
- [ ] tray release scope decided and, if included, validated;
- [ ] no known service-input overflow/type-validation issue remains at a public API boundary.

Distribution gates:

- [ ] Linux package works without the developer checkout or `.venv`;
- [ ] Windows package/install path validated;
- [ ] Android architecture settled before Android release work;
- [ ] APK validated on real Android hardware when Android enters release scope.

Quality/security gates:

- [ ] all required CI jobs green on the release commit;
- [ ] production CSP/capabilities reviewed;
- [ ] Private Vulnerability Reporting enabled and tested;
- [ ] `SECURITY.md` reflects supported release versions;
- [ ] version and license metadata consistent;
- [ ] no private validation data in committed artifacts;
- [ ] documentation and hardware checklist match the released behavior.

---

## Known open technical items

Unchecked items below are intentionally unresolved unless another phase explicitly closes them.

- [ ] distributable/self-contained Python runtime for packaged desktop applications;
- [ ] production Tauri CSP/capability hardening;
- [ ] huge discovery timeout -> `invalid_argument` rather than `internal_error`;
- [ ] huge seek value -> `invalid_argument` rather than `internal_error`;
- [ ] strict service-level boolean validation for mute and boolean rejection for volume;
- [ ] expose receiver `volume_control_type`;
- [ ] validate/adjust volume confirmation tolerance for stepped receivers;
- [ ] assistive-technology slider edge case when no pointer/key release event is produced;
- [x] volume/seek deliberate-focus-return bug fixed in PR #14;
- [ ] `load_media` not exposed through bridge/Tauri/UI;
- [ ] automatic/appropriate status refresh;
- [ ] stable media-session identity if same-`content_id` replacement proves relevant;
- [ ] Stop wording after real-hardware validation;
- [ ] unified version metadata;
- [ ] consistent GPL-3.0 package metadata;
- [ ] Linux package independence from developer `.venv`;
- [ ] Windows distribution;
- [ ] Android Python/control architecture;
- [ ] GNOME/native tray;
- [ ] MCP adapter;
- [ ] Private Vulnerability Reporting.

---

## Immediate execution order

The order below reflects the current project state rather than the historical phase numbering alone.

1. [ ] Finish controlled real-hardware playback validation with a fresh seekable media session: Pause, Play, Seek, Stop last.
2. [ ] Validate real volume/mute behavior on an adjustable receiver and resolve capability/tolerance findings.
3. [ ] Harden the remaining authoritative service input-validation issues discovered by the audits.
4. [ ] Decide and implement status refresh behavior.
5. [ ] Decide first-desktop-release scope for `load_media` and tray integration.
6. [ ] Implement and validate GNOME/native tray if included in scope.
7. [ ] Make the Linux package self-contained and distributable.
8. [ ] Harden production Tauri CSP/capabilities, versioning, license metadata and security reporting.
9. [ ] Add Windows packaging/validation.
10. [ ] Implement MCP as a thin client of the shared control service.
11. [ ] Make the Android runtime/control architecture decision before Android application work.
12. [ ] Implement Android application and then the home-screen widget.

## Historical evidence that must not be lost

The following project-history facts remain useful even as implementation totals evolve:

- PR #8 exposed a real discovery/lifecycle defect that PR #9 fixed; the merged read-only application path was revalidated on hardware afterward.
- The shared-service hardening introduced explicit confirmation deadlines and prevented late observations from becoming false confirmation.
- Playback command-contract branches established the one-command/no-replay behavior and explicit failed-attempt handling.
- PR #12 deliberately changed no backend behavior after auditing Stop semantics; physical Stop wording remains a product-validation question.
- PR #13 introduced the playback UI and its fake-TV real-application validation.
- PR #14 introduced volume/mute, the +10 raise limit, fake-TV anti-storm validation and the shared focus-intent correction for volume and seek.
- A later controlled real-hardware session sent one Pause command: playback physically paused, but the application returned `UNCONFIRMED`. This is evidence of an effect, not completion of the confirmation contract.
- No real Play, Seek, Stop, volume or mute command has yet been accepted as physically validated in this plan.

Keep future updates equally explicit about what was implemented, what was automation-validated and what was actually observed on target hardware.