# Development plan

## Goal

Build a lightweight standalone Chromecast / Google TV controller for Android, Windows and Debian/Ubuntu. Manual control works independently. An MCP adapter exposes the same control capabilities to an external assistant. Python is part of the implementation, with `pychromecast` available for Chromecast discovery/control. Tauri 2 provides the cross-platform application shell; Rust/native components are used where they bring a concrete benefit.

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
- [ ] **Physical validation still required:** exercise discovery, replacement cleanup, stale-connection recovery, command acknowledgement and observed state on a real Chromecast/Google TV.

## Current implementation status - shared control service

- [x] Reproducible Python 3.12 environment is pinned with uv, `.python-version`, and committed `uv.lock`; setup fails when the lockfile is stale or uv is unavailable.
- [x] Shared control confirmation reports only device state actually observed; missing, contradictory, and disconnected states remain explicitly unconfirmed in deterministic tests.
- [x] Real-hardware evidence is required before any hardware/platform checkbox can be completed.
- [x] Coverage (95% minimum) and installed-dependency quality commands are implemented and validated; deterministic command tests cover orchestration and fail-fast behavior, and the defensive verification invariant is explicit.
- [x] **P2 review follow-up:** `CastTransport.get_status` now takes an explicit `timeout`; `ControlService._verify` passes only the confirmation budget actually remaining before every read, and never treats a status that arrives after the budget expired as confirmation. The PyChromecast adapter's `get_status` (connection, bounded same-UUID recovery, and the receiver-status round trip) now honors that same per-call budget instead of its own fixed instance timeouts. Deterministic tests cover a blocked/hung read, the exact `timeout` handed to each poll, total elapsed time never exceeding the budget, a match confirmed just before the deadline, and a match arriving just after it (never confirmed).
- [x] Read-only physical validation completed for discovery -> UUID selection -> connected receiver status, repeated discovery -> same UUID -> status, and repeated close; no media or receiver command was sent.

## Current implementation status - Tauri application shell

- [x] Tauri 2 shell scaffolded (`src-tauri/`, Rust) with a minimal frontend (`ui/`, vanilla TypeScript + Vite); no frontend framework or UI library is used yet, matching the size of a one-page skeleton.
- [x] The shell owns no Cast/control logic: `src-tauri/src/lib.rs` only spawns `src/control_tv/bridge.py` as a long-lived child process and forwards line-delimited JSON requests/responses over its stdin/stdout, matched by request id. `ControlService` is not reimplemented in Rust or TypeScript.
- [x] Only three read-only methods are exposed so far (`ping`, `discover_devices`, `get_status`); every other `TvControl` capability is added to the bridge and to a Tauri command as its own view needs it, not in advance.
- [x] A failed bridge spawn does not crash the application: the Tauri command returns an explicit "Python control backend unavailable" error instead, surfaced as plain text in the UI.
- [x] Real, installed-package validation on this Debian/Ubuntu-family desktop (Ubuntu 22.04): `cargo tauri build --debug --bundles deb` produced a real `.deb`; it was installed with `dpkg -i`, launched from `/usr/bin/control-tv` (not the raw build output), showed "Control backend ready", and was cleanly uninstalled afterward (`dpkg -r`). `cargo tauri dev` was also run and screenshotted before the packaged validation.
- [x] Real end-to-end proof the mechanism reaches the shared control layer: clicking "Discover devices" in the running application performed a real LAN discovery through `ControlService.discover_devices` and returned real Chromecast devices present on the operator's network (no device names/addresses are recorded in this public file; see the local handoff). No control command (play/pause/volume/etc.) was sent to any real device - only the passive, read-only discovery and status paths exist in the UI so far.
- [x] Rust unit tests cover response parsing/error-translation as pure functions, plus one test that spawns the real `python -m control_tv.bridge` process and pings it (`cargo test`, run as part of `scripts/dev.py rust-check`); it skips (not fails) when `.venv` does not exist yet, which the CI job avoids by running Python setup first.
- [x] **P1 review follow-up (PR #4):** `bridge_ping`/`bridge_discover_devices` are async Tauri commands; the blocking bridge call runs on a dedicated worker thread (`tauri::async_runtime::spawn_blocking`), never on the async/main thread, and is wrapped in a bounded `tokio::time::timeout` so a slow or fully stuck bridge process reports an error instead of hanging the command forever (the underlying worker thread can still remain blocked - a documented limitation, not a correctness issue, since responses stay matched by id). Deterministic Rust tests cover a successful async call, a process that exits without responding, and a process that never responds at all (times out at the configured bound, not after). Re-verified manually in `cargo tauri dev`: the window kept repainting ("Discovering…") while a real discovery call was in flight.
- [x] **Device selection and read-only status view (implemented and automation-validated):** a discovered device is selected by its stable id (never its display name; two devices may share a name); the selection lives in UI state only, so the bridge stays stateless. `get_status` (bridge) -> `bridge_get_status` (Tauri) -> `ControlService.get_status`, with no Cast logic added in Rust or TypeScript. Failures now reach the UI as `{code, message}` (control-layer codes plus `backend_unavailable`, `bridge_timeout`, `bridge_transport`) so an unavailable device, a timeout, an unknown device and an unavailable backend are told apart; an unexpected Python exception becomes an `internal_error` response instead of killing the bridge. The UI shows no-selection, searching, empty, loading, failure, disconnected, partial (each unreported field worded as not reported, never zero/off) and nothing-playing states, in plain language with raw error text only in a collapsed "Details" disclosure (no internal component names in the normal view); at most one bridge request is in flight from the UI (no selection, refresh or discovery is offered while a read or discovery runs, because the bridge is single-flight and each request's timeout starts before its turn), and a late answer never overwrites the current read. Tests: Python bridge, Rust (`cargo test`) and 58 TypeScript model tests (`npm test` in `ui/`, run by `dev.py ui-check`).
- [x] The visual design follows the local UI skills (`ui-desktop-minimal`, `ui-ux-accessibility`, `ui-responsive-app`, `ui-design-review`, `ui-component-design`; installed outside this repository) and remains open to owner direction: a light, single-surface remote rather than a dashboard, the validated `assets/logo.svg` reused unchanged (served through Vite's `publicDir`), one consistent inline SVG icon set, no decorative gradients, shadows or extra cards, the selected device as one row that expands into the device list, and what is playing as the main context. No playback control is drawn until playback commands exist.
- [x] UI consolidation review (`ui-design-review`, 20 states at 900px and 320px on a fake backend, plus the real window): the structure needed no rethink; localized defects were fixed - a dropped selection was silent (the notice was never drawn), loading looked like disabled and was unreadable, live regions re-announced an unchanged status after every discovery, Escape did not close the device list, focus was lost after Try again, the Details target was under 44px, and a 480px breakpoint was replaced by fluid CSS. Measured: no horizontal overflow at 320, 390, 481, 768 and 1280px across ten content-stress states; every colour pair meets WCAG AA (body 15:1, muted 5.9:1, accent 5.06:1, error 6.6:1). Keyboard paths were exercised on the real window and in a browser; nothing was run with a screen reader, so accessibility is not claimed as complete.
- [x] Real desktop run (Ubuntu 22.04, `cargo tauri dev`): real discovery, selection by click, change of selection and a second discovery that kept the selection were observed. No control command was sent.
- [x] The adapter defect found while validating PR #8 (discovery stopped PyChromecast's zeroconf before the cached devices connected, so every real status read timed out) was fixed by PR #9. The merged application was then revalidated end to end on real hardware (Ubuntu 22.04, `cargo tauri dev`, read-only) on 2026-09-26: UI -> Tauri -> bridge -> `ControlService` -> PyChromecast for discovery, selection by stable id, status, refresh, a rediscovery that kept the selection and the status, and a change of selection to a second device; the window stayed responsive (it was resized while a discovery was running). No control command was sent.
- [ ] Windows and Android are not built, installed or launched; nothing here validates them.
- [ ] Packaging a real Python runtime for a distributed build (not this developer's own `.venv`) is not implemented; `resolve_python()` is explicitly a development-only placeholder (see its docstring) and is Phase 7 work.

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

Current confirmation-synchronization iteration:
- [x] **Implemented:** bind seek confirmation to the `content_id` observed before command delivery, so different or unidentified media can never satisfy a position-only confirmation;
- [x] deterministic targeted tests cover matching media, replaced media, missing media identity, contradictory positions and the unchanged global confirmation deadline;
- [x] complete local lint, format, strict typing, test, 98.01% coverage and dependency gates pass; hosted CI remains a separate PR requirement;
- [x] **Implemented:** make the pre-command seek status read and post-command confirmation share one bounded confirmation deadline, and treat empty or whitespace-only seek media identities as absent evidence;
- [x] deterministic timing and identity tests pass; removing the shared deadline makes all four focused double-budget mutation tests fail;
- [x] complete local Ruff, format, strict mypy, 314-test suite, 97.69% coverage and dependency checks pass; hosted CI pending;
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
- [x] play/load supported media;
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
- [ ] playback controls;
- [ ] volume/mute controls;
- [x] clear unavailable/error states for the control-backend boundary itself (bridge process unavailable, discovery failure are both surfaced in the UI as plain text; status-read failures - device unavailable, timeout, unknown device, backend unavailable or not responding - are told apart by error code; playback-command error states do not exist yet because no playback commands are wired);
- [ ] responsive desktop/Android layout (fluid single-column layout with 44px touch targets, no horizontal overflow from 320px to 1280px in a browser harness with a fake backend, and observed in the real window between 520px and 900px; nothing was run on Android or with a touch screen);
- [x] choose JavaScript/TypeScript and any UI tooling from concrete implementation needs (vanilla TypeScript + Vite: no frontend framework is justified yet by a single-page skeleton).

Exit criteria:
- [ ] application controls a TV manually without ChatGPT or MCP;
- [ ] frontend presentation remains separated from Cast transport/control logic.

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
- [ ] require applicable CI checks before a pull request is considered merge-ready.

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
