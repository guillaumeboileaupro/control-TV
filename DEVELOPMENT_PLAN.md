# Development plan

## Goal

Control the TV in two ways that share one engine (scope restored on 2026-10-08, issue #34; see "Product scope" below and `docs/REQUIREMENTS_TRACEABILITY.md`):
- **by voice, in the existing ChatGPT app:** the owner speaks in ChatGPT's voice mode ("lance-moi ce documentaire sur la TV") and ChatGPT carries the request out through control-TV's tools (MCP) -> the shared control service -> the TV, including starting the requested content **on the TV**;
- **with control-TV, a standalone graphical TV remote** for Debian/Ubuntu and Android (personal use, sideloaded APK: no Google Play publication objective; Windows postponed): discover and select TVs, show the TV's applications and launch them on the TV, power on and off, the TV's audible volume and mute, playback and navigation. It works without ChatGPT, MCP or any AI API.

Python is part of the implementation, with `pychromecast` for Chromecast discovery/control; other TV endpoints (power, audible sound, navigation, applications) are added behind the same service only where they are proven on the owner's hardware. Tauri 2 provides the cross-platform application shell; Rust/native components are used where they bring a concrete benefit.

Two kinds of evidence are kept apart everywhere in this plan (see "Progress rule" at the end): **automation-validated** means deterministic tests, CI, or the real application driven against a fake TV / fake transport; **real-hardware validated** means the behavior was observed on a real Chromecast/Google TV or on the real target platform, with the evidence recorded. A checked implementation line never implies the matching hardware line.

## Product scope (issue #34, owner correction of 2026-10-08)

The original goal, from the 2026-09-24/25 voice discussion, was lost in this file from the first commit, which planned only a Chromecast controller with an optional MCP adapter; its only trace was Phase 5's "natural content targets". The owner's correction of 2026-10-08 on issue #34 is authoritative and supersedes the issue body, its earlier comments and both assistant audits where they conflict. `docs/REQUIREMENTS_TRACEABILITY.md` traces every requirement (R1-R20) to what exists, its automated and real-hardware evidence and what is missing, and records the hardware observations, the feasibility gates (G1-G6) and the proposed pull requests.

- Voice exists only in the ChatGPT app (its voice mode), which calls control-TV's tools; whether ChatGPT's voice mode can call custom MCP tools on the owner's ChatGPT surface is **not established** and is verified before it is promised (gate G1).
- Content asked for in ChatGPT plays on the TV through tools and a compatible provider or protocol; content resolution and launch are backend/MCP operations only.
- Evidence levels: not sent, unknown, sent, receiver-confirmed, physical effect observed. Receiver-confirmed is never presented as physical; the Cast logical volume and mute are distinct from the TV's audible output (with `volume_control_type=fixed` the Cast volume does not control the TV's sound).
- Capabilities are per device and per endpoint and are never assumed for all TVs.

**Not requested** (superseded proposals, not roadmap items): a microphone, speech-to-text engine or local voice assistant in control-TV; a local natural-language intent parser; a search interface or search results in control-TV; a video player in control-TV; ChatGPT connecting directly to the TV.

## Architecture constraints

- [x] Python and the Python Chromecast ecosystem are part of the architecture, including `pychromecast` for applicable Cast capabilities.
- [x] Tauri 2 is the cross-platform application shell and packaging layer.
- [x] JavaScript or TypeScript may be used for the UI according to implementation needs.
- [x] GUI and MCP share the same authoritative control/domain behavior.
- [x] Media/service resolution remains separated from low-level Cast transport where practical.
- [x] Manual application control operates independently of ChatGPT/MCP.
- [x] Build output, platform intermediates and temporary files are controlled and disposable.
- [x] Project cleanup is limited to verified project-owned generated output; shared/global caches remain separate.

## Current state and critical path

State of `main` at `ded6e9c` (merge of PR #32, the desktop delivery wording fix, 2026-10-07; it contains everything listed below, including PR #31 `499bfb2`, PR #30 `3bcb516`, PR #29 `1c71530` and PR #26 `eba73f5`). CI on `main` `ded6e9c`: push run `37590130462`, all four jobs green. Only merged work is described as part of `main`; an open pull request is listed as in progress and its items stay `[ ]` until it is merged. In progress, not merged: the Android home-screen widget (PR #33, branch `feat/android-home-widget`, open, not to be merged without the owner's authorization) and this roadmap reconciliation (branch `docs/roadmap-reconciliation`).

Product priority (owner, issue #34, 2026-10-08): the central acceptance path is a request spoken in ChatGPT's voice mode carried out on the TV through control-TV's tools, and control-TV as a graphical remote for the TV's applications, power, audible sound, playback and navigation. Agreed next steps (owner convergence, 2026-10-08, ordered after Codex's review of PR #35): this documentation reconciliation (item L); then first, because gate G1 decides the central path, a read-only ChatGPT text and voice tool-call feasibility test (item O, first part), before any substantial work on content tools; in parallel, the capability and evidence model (M) and the read-only feasibility on the owner's TVs for power, audible sound, navigation and applications (P); then content launched on the TV from tools (N), the rest of ChatGPT reach (O) and those controls (Q). No TV command is sent by any feasibility test. The Android widget (PR #33) stays open and separate. Earlier order (owner decision, 2026-10-06, replacing the order of 2026-10-04; PR #30 and PR #31 are merged), kept as history: **1. MCP control-TV adapter (done: PR #31) -> 2. Android home-screen widget -> 3. Linux tray and secondary improvements -> 4. Windows later.** Android is personal and sideloaded only: there is no Google Play publication objective, so no Play Console, store listing, store metadata or Play-specific release work is planned. Windows is postponed by product decision and is not a current priority: it stays documented (Phase 7, "Windows desktop") with all its open limitations and is not developed until the owner puts it back.

Critical path:
- [x] **A. Bridge requests never sent after their timeout (PR #23, merged):** see "Bridge request claim" under the Tauri shell status.
- [x] **B. Stop by session end, fixed-volume refusal and one application version (PR #24, merged, automation-validated):** see the Stop, "Volume control type" and "One application version" entries; Stop, volume and mute are still not validated on real hardware.
- [x] **C. Receiver volume, mute and standby defaults (PR #27 and PR #28, merged, automation-validated):** see the two "Receiver ... default(s)" entries under "Known open items"; not validated on real hardware.
- [x] **D. Autonomous Python bridge sidecar and Tauri packaging for Linux (PR #26, merged, automation-validated and installed locally):** frozen bridge on CPython 3.12.15, release-only resolution of the bundled bridge, strict isolated smoke, release `.deb` with remapped build paths, fail-closed scans, inventory, checksums, notices and the `linux-release` CI job; public distribution still waits for the license blockers (Phase 7).
- [x] **E. Autonomous `.deb` really installed and validated** on Ubuntu 22.04 without the checkout, the `.venv` or a system Python (PR #26 content): installed, the GUI application launched, its bundled bridge pinged through it, purged (local, commit `fba82cc`, whose code is what PR #26 merged); the `linux-release` CI job installs the package, pings the installed bridge directly and purges it on every run (no GUI). Not a published release: the Debian/Ubuntu target stays open in Phase 7 while the license blockers remain.
- [x] **G. Bridge recovery (PR #29, merged, automation-validated):** see "Bridge recovery" under "Known open items"; the Windows limitation stays open.
- [x] **H. Android APK MVP (Phase 7b), complete at its defined level:** PR #30 merged into `main` (`3bcb516`, 2026-10-06). On a physical arm64 phone the spike gates A-H all passed (install, launch, embedded CPython, `control_tv` imported and its ping answered `python 3.12.12; control_tv 0.1.0 imported; embedded ping ok, controlTvVersion=0.1.0`, multicast lock held, real discovery), and with the APK of `a2ff3bb` the owner saw the Control-TV launcher icon and the page clear of the status and navigation bars. Further Android hardware validation is separate work and stays open (Phase 7b, "Android hardware validation still open"): receiver status, the control commands, display cutout and rotation, broader responsive and touch use.
- [x] **J. MCP control-TV adapter (Phase 6), complete at its defined level:** PR #31 merged into `main` (`499bfb2`, 2026-10-07): a local stdio MCP server over the shared control layer, automation-validated and validated read-only with a real MCP client (Codex CLI) on the real local network, no control command sent. MCP commands against a real receiver stay open (Phase 6). This is **not** ChatGPT control: the ChatGPT app cannot start a local stdio server, and ChatGPT's reach (item O) is open.
- [ ] **I. Android home-screen widget (Phase 7b):** implemented on PR #33 (open, not merged; its phone results are recorded on that branch and summarized in `docs/REQUIREMENTS_TRACEABILITY.md`).
- [ ] **K. Linux tray (Phase 4b) and secondary improvements:** after the items below unless the owner reprioritizes.
- [ ] **L. Roadmap reconciliation (issue #34, documentation only):** this plan, `README.md`, `ARCHITECTURE.md` and `docs/REQUIREMENTS_TRACEABILITY.md` restored to the original scope; reviewed independently by Codex; merged only with the owner's authorization.
- [ ] **M. Capability and evidence model (proposed P1):** per-device capabilities and the evidence levels, with the Cast logical volume and mute told apart from the TV's audible output, in tool answers and GUI wording.
- [ ] **N. Content on the TV from tools (proposed P3, Phase 5):** `load_media` and a provider launch exposed as MCP tools behind a resolver boundary, with observed-playback verification; no search UI or player in control-TV.
- [ ] **O. ChatGPT reach (S0 then P4, Phase 6, gate G1):** first, right after item L, a read-only feasibility test (S0 prepared on PR #36, not merged; provisional verdict INDETERMINATE, protocol in `docs/CHATGPT_VOICE_FEASIBILITY.md`; empirical result pending): a read-only control-TV tool called from ChatGPT text, then from ChatGPT voice mode, on the owner's ChatGPT app, with no TV command; then a transport the ChatGPT app can use for real, its security review, and the command tools.
- [ ] **P. TV endpoints feasibility (proposed S1, Phase 5b, gates G3-G5):** read-only identification of each owner TV's platform, audio path and control endpoints.
- [ ] **Q. Power, audible sound, navigation and applications (proposed P5-P9, Phase 5b):** in the GUI (Linux, then Android) and as tools, per proven capability.
- [ ] **F. Windows packaging and validation (Phase 7): postponed by product decision**, not a current priority; no Windows limitation is lifted by this.

Media/content resolution is no longer complementary: it is item N (Phase 5), limited to backend/MCP operations.

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

- [x] Reproducible Python 3.12 environment is pinned with uv, `.python-version`, and committed `uv.lock`; setup fails when the lockfile is stale or uv is unavailable. Release bridges embed exactly CPython 3.12.15 (`packaging/release-python-version`, PR #26), built with uv 0.12.23; development keeps `.python-version` 3.12.
- [x] Shared control confirmation reports only device state actually observed; missing, contradictory, and disconnected states remain explicitly unconfirmed in deterministic tests.
- [x] Real-hardware evidence is required before any hardware/platform checkbox can be completed.
- [x] Coverage (95% minimum) and installed-dependency quality commands are implemented and validated; deterministic command tests cover orchestration and fail-fast behavior, and the defensive verification invariant is explicit.
- [x] **P2 review follow-up:** `CastTransport.get_status` now takes an explicit `timeout`; `ControlService._verify` passes only the confirmation budget actually remaining before every read, and never treats a status that arrives after the budget expired as confirmation. The PyChromecast adapter's `get_status` (connection, bounded same-UUID recovery, and the receiver-status round trip) now honors that same per-call budget instead of its own fixed instance timeouts. Deterministic tests cover a blocked/hung read, the exact `timeout` handed to each poll, total elapsed time never exceeding the budget, a match confirmed just before the deadline, and a match arriving just after it (never confirmed).
- [x] **P2 review follow-up:** a zero confirmation budget rejects seek before any status read or command instead of bypassing the capability guard or creating an independent timeout;
- [x] Read-only physical validation completed for discovery -> UUID selection -> connected receiver status, repeated discovery -> same UUID -> status, and repeated close; no media or receiver command was sent.

## Current implementation status - Tauri application shell

- [x] Tauri 2 shell scaffolded (`src-tauri/`, Rust) with a minimal frontend (`ui/`, vanilla TypeScript + Vite); no frontend framework or UI library is used yet, matching the size of a one-page skeleton.
- [x] The shell owns no Cast/control logic: `src-tauri/src/lib.rs` only spawns the Python bridge as a long-lived child process (a debug build runs `python -m control_tv.bridge` from the repository `.venv`; since PR #26 a release build runs only the bundled frozen bridge, see Phase 7) and forwards line-delimited JSON requests/responses over its stdin/stdout, matched by request id. `ControlService` is not reimplemented in Rust or TypeScript.
- [x] Nine bridge methods are exposed so far: three read-only (`ping`, `discover_devices`, `get_status`) and six commands (`play`, `pause`, `stop`, `seek`, `set_volume`, `set_muted`), each a plain forward to the same-named `ControlService` method; every other `TvControl` capability is added to the bridge and to a Tauri command as its own view needs it, not in advance.
- [x] A failed bridge spawn does not crash the application: the Tauri command returns an explicit "Python control backend unavailable" error instead, surfaced as plain text in the UI.
- [x] Real, installed-package validation on this Debian/Ubuntu-family desktop (Ubuntu 22.04): a Tauri CLI debug build with the `deb` bundle (today `npx --prefix ui tauri build --debug --bundles deb` from the repository root) produced a real `.deb`; it was installed with `dpkg -i`, launched from `/usr/bin/control-tv` (not the raw build output), showed "Control backend ready", and was cleanly uninstalled afterward (`dpkg -r`). The Tauri dev mode was also run and screenshotted before the packaged validation. This validated the Tauri install/launch mechanism only: that `.deb` worked because it started the bridge from this developer's repository `.venv` (see `resolve_python()` below), so it is **not** a validated distributable package (Phase 7 tracks the autonomous one).
- [x] Real end-to-end proof the mechanism reaches the shared control layer: clicking "Discover devices" in the running application performed a real LAN discovery through `ControlService.discover_devices` and returned real Chromecast devices present on the operator's network (no device names/addresses are recorded in this public file; see the local handoff). The command controls were first exercised against fake TVs; the later real-hardware Pause observation is recorded separately below and does not count as confirmed hardware validation.
- [x] Rust unit tests cover response parsing/error-translation as pure functions, plus one test that spawns the real `python -m control_tv.bridge` process and pings it (`cargo test`, run as part of `scripts/dev.py rust-check`); it skips (not fails) when `.venv` does not exist yet, which the CI job avoids by running Python setup first.
- [x] **P1 review follow-up (PR #4):** `bridge_ping`/`bridge_discover_devices` are async Tauri commands; the blocking bridge call runs on a dedicated worker thread (`tauri::async_runtime::spawn_blocking`), never on the async/main thread, and is wrapped in a bounded `tokio::time::timeout` so a slow or fully stuck bridge process reports an error instead of hanging the command forever. (The worker of a request already written can stay blocked reading the reply; since PR #23 a request that times out before being written is never written, see "Bridge request claim" below.) Deterministic Rust tests cover a successful async call, a process that exits without responding, and a process that never responds at all (times out at the configured bound, not after). Re-verified manually in the Tauri dev mode: the window kept repainting ("Discovering…") while a real discovery call was in flight.
- [x] **Bridge request claim (PR #23, merged, automation-validated, no real device involved):** before PR #23, a request whose caller had timed out while it waited for the single-flight bridge could still be written later, delivering a command nobody was waiting for. Each request now has an atomic claim (`PENDING`, `STARTED`, `ABANDONED`) settled exactly once by a compare-and-swap in `call_bridge` (`src-tauri/src/lib.rs`). The worker takes `STARTED` only after it holds the bridge and before anything reaches stdin; a timeout that wins first sets `ABANDONED`, and an abandoned request is never written. That case returns `bridge_busy`, which really means not sent. If the worker already took `STARTED`, the timeout returns `bridge_timeout`, deliberately ambiguous because the write may have started. There is no retry, no replay and no double write; `ping`, including the startup ping, follows the same rule. The UI says "Nothing was sent" for `bridge_busy` and does not report a startup `bridge_busy` as a failed background service. Rust tests cover a request abandoned while it waits, several abandoned requests followed by one healthy request sent exactly once, a startup ping abandoned behind a long request, and a timeout after `STARTED`. Remaining limits are listed under "Known open items" (bridge recovery).
- [x] **Device selection and read-only status view (implemented and automation-validated):** a discovered device is selected by its stable id (never its display name; two devices may share a name); the selection lives in UI state only, so the bridge stays stateless. `get_status` (bridge) -> `bridge_get_status` (Tauri) -> `ControlService.get_status`, with no Cast logic added in Rust or TypeScript. Failures now reach the UI as `{code, message}` (control-layer codes plus `backend_unavailable`, `bridge_timeout`, `bridge_transport`, and `bridge_busy` since PR #23) so an unavailable device, a timeout, an unknown device and an unavailable backend are told apart; an unexpected Python exception becomes an `internal_error` response instead of killing the bridge. The UI shows no-selection, searching, empty, loading, failure, disconnected, partial (each unreported field worded as not reported, never zero/off) and nothing-playing states, in plain language with raw error text only in a collapsed "Details" disclosure (no internal component names in the normal view); at most one bridge request is in flight from the UI (no selection, refresh or discovery is offered while a read or discovery runs, because the bridge is single-flight and each request's timeout starts before its turn), and a late answer never overwrites the current read. Tests: Python bridge, Rust (`cargo test`) and 58 TypeScript model tests (`npm test` in `ui/`, run by `dev.py ui-check`).
- [x] The visual design follows the local UI skills (`ui-desktop-minimal`, `ui-ux-accessibility`, `ui-responsive-app`, `ui-design-review`, `ui-component-design`; installed outside this repository) and remains open to owner direction: a light, single-surface remote rather than a dashboard, the validated `assets/logo.svg` reused unchanged (served through Vite's `publicDir`), one consistent inline SVG icon set, no decorative gradients, shadows or extra cards, the selected device as one row that expands into the device list, and what is playing as the main context. Playback controls are drawn only for media the TV reports on a connected device, and only what the observed state offers.
- [x] UI consolidation review (`ui-design-review`, 20 states at 900px and 320px on a fake backend, plus the real window): the structure needed no rethink; localized defects were fixed - a dropped selection was silent (the notice was never drawn), loading looked like disabled and was unreadable, live regions re-announced an unchanged status after every discovery, Escape did not close the device list, focus was lost after Try again, the Details target was under 44px, and a 480px breakpoint was replaced by fluid CSS. Measured: no horizontal overflow at 320, 390, 481, 768 and 1280px across ten content-stress states; every colour pair meets WCAG AA (body 15:1, muted 5.9:1, accent 5.06:1, error 6.6:1). Keyboard paths were exercised on the real window and in a browser; nothing was run with a screen reader, so accessibility is not claimed as complete.
- [x] Real desktop run (Ubuntu 22.04, Tauri dev mode): real discovery, selection by click, change of selection and a second discovery that kept the selection were observed. No control command was sent.
- [x] The adapter defect found while validating PR #8 (discovery stopped PyChromecast's zeroconf before the cached devices connected, so every real status read timed out) was fixed by PR #9. The merged application was then revalidated end to end on real hardware (Ubuntu 22.04, Tauri dev mode, read-only) on 2026-09-26: UI -> Tauri -> bridge -> `ControlService` -> PyChromecast for discovery, selection by stable id, status, refresh, a rediscovery that kept the selection and the status, and a change of selection to a second device; the window stayed responsive (it was resized while a discovery was running). No control command was sent.
- [x] **Playback controls (play, pause, stop, seek) implemented and automation-validated, no real device involved:** the bridge forwards `play`/`pause`/`stop`/`seek` (`deviceId`, plus a numeric `positionSeconds` for seek) to the existing `ControlService` methods and serializes the `CommandResult`; `ok: true` on a command means it was *sent*, and `confirmation` (`confirmed` / `unconfirmed` / `not_checked`) says whether the TV then showed the result, so unconfirmed is never reported as success. Four async Tauri commands (`bridge_play`/`_pause`/`_stop`/`_seek`) reuse `call_bridge` (worker thread plus a 60s bound covering the control layer's own windows), never retry, and relay control-layer error codes untouched; a single-threaded-runtime test proves a slow command does not block the async thread (it fails when the bridge call is run inline, recreating the PR #4 P1). The UI offers one play/pause toggle that always offers the opposite of the observed state (pause while playing or buffering, play while paused), a quieter Stop, and a seek slider only when the TV reports seekable media with a known length and position; a seek being composed is shown as a draft ("Go to 4:09") and a single seek is sent after the control settles, never a burst; the position shown changes only through a status the TV reported. A command in flight blocks every other request (a rapid triple click sends one command). A sent-but-unconfirmed command keeps the last status the TV reported, is worded as not confirmed, and offers Check state instead of resending; a timeout says the command may or may not have arrived. Tests: 51 new bridge tests (367 Python tests, 97.61% coverage), 28 Rust tests (20 before), 115 TypeScript tests (58 before) including mutation checks of the no-double-command, unconfirmed-is-not-success and no-simulated-position rules.
- [x] **Playback controls exercised in the real application against a fake TV:** the real Tauri shell, Rust commands, bridge and `ControlService` were run with only the Cast transport replaced by a scratch double (a fleet of 15 scriptable fake TVs, kept outside the repository, run on a private X display so it could not touch the desktop). Observed: confirmed pause/play/seek, a pending state that fades the other controls, a TV that ignores commands (unconfirmed after the 5s window, exactly one command delivered, Check state), a TV that acts after the window (unconfirmed, then Paused after Check state), refused, unreachable and send-timeout failures with their plain wording, live media (no seek), unknown length, capability not reported, buffering, idle and nothing-playing states, and Stop on a TV that drops its media session (see the Stop entry below). The states were also checked at 480px in the real window and at 320, 390, 481, 768 and 1280px in a browser harness (no horizontal overflow in 50 width and state combinations).
- [x] **Stop confirmation (PR #24, merged, automation-validated, no real device involved):** Stop has its own predicate. It is confirmed by IDLE on the same identified media, or by a fresh read reporting no media session, but only when the media was identified before the command (usable content id) and no read of the attempt reported a contradiction; another content id, session or `currentItemId` is terminal for the attempt, so a replacement followed by an end stays `UNCONFIRMED`. A disconnected receiver is not an ended media session. No IDLE is fabricated (the observed media stays absent), exactly one stop is sent, and there is no retry or replay; play and pause are unchanged. Before the media-status freshness fix (PR #19) PyChromecast's cache could even confirm a Stop from a partial IDLE status; only the receiver's own reply counts now.
- [ ] **Stop outcome on a real receiver is unknown (Stop was never sent to real hardware):** what a real receiver reports after Stop is still to be observed, after an explicit go-ahead.
- [x] **Volume and mute implemented and automation-validated, no real device involved:** the bridge forwards `set_volume` (`level`, a number from 0 to 1, never a boolean) and `set_muted` (`muted`, a JSON boolean: an absolute state, so mute is `true` and unmute is `false`, never a toggle) to the existing `ControlService` methods, with the same sent-versus-confirmed answer as the playback commands; a refused value (a boolean level, `0`/`1`/`"true"` as a mute state, NaN, out of range) fails as `invalid_argument` before any transport call. Two async Tauri commands (`bridge_set_volume`, `bridge_set_muted`) reuse `call_bridge` (worker thread, 60s bound, no retry, error codes relayed untouched), and a real-bridge test proves the parameter names match. The UI shows a mute button and a volume slider under the playback controls for any connected device, playing or not. The slider works on a local draft and sends one command once it has settled (300 ms after a pointer release, 800 ms after a key press, so a held key's repeat pause does not split it into two), never one per pixel or key repeat; at most one command is ever in flight, so a second gesture cannot queue behind the first; a level equal to the one the TV reports is not sent. The level shown is the one the TV reported, except while a level is being composed ("Set volume to 60%") or is on its way ("Setting volume to 60%…"), and mute asks for the opposite of the state the TV reported without keeping a state of its own. A volume the TV did not show is worded as not confirmed and says what the TV reports instead ("Volume sent, but the TV reports 47%, not 50%"), offers Check state and is never resent; a volume or mute state the bridge reports as unknown offers no control and says so, never zero or unmuted (since PR #27 an omitted receiver volume or mute state is not reported, rather than PyChromecast's 100% or unmuted: see "Receiver volume and mute defaults"). Tests: 59 new bridge tests (453 Python tests, 97.66% coverage), 39 Rust tests (28 before), 203 TypeScript tests at that point (115 before; 238 with the raise limit below, 242 with the focus-return fix) including mutation checks of the no-double-command, one-command-per-gesture, opposite-of-observed, unknown-is-not-zero and unconfirmed-is-not-success rules.
- [x] **Volume and mute exercised in the real application against a fake TV:** the real Tauri shell, Rust commands, bridge and `ControlService` were run with only the Cast transport replaced by a scratch double (a fleet of 15 scriptable fake TVs, kept outside the repository, on a private X display). Observed: a 34-move mouse drag and eight quick key presses each sent exactly one command, a held key sent one, a triple click on mute sent one, extra clicks and drags during a slow command sent nothing, a TV that ignores commands stayed at one command 9s later (unconfirmed, the level it reports, Check state), a stepped-volume TV settled on 47% for a 49% request (worded as not confirmed), a TV that acts after the window showed the new level after Check state, and refused, unreachable and send-timeout failures were worded in plain language; muted, no-media, volume-not-reported, mute-not-reported and nothing-reported TVs were checked, and so were the keyboard order and focus, the 480px window, and 320, 390, 481, 768 and 1280px in a browser harness (no horizontal overflow in 50 width and state combinations).
- [x] **Volume and mute display path exercised read-only on a real device (2026-09-26, no command sent; the displayed values are not proven to have been reported by the receiver):** the application discovered one real device, selected it by its stable id and read its status: it reported volume 100% and not muted with nothing playing, the sound row drew "Volume 100%" with the slider at its end, and no playback controls were drawn (nothing to control). Nothing was pressed after the selection. Whether that device's volume was adjustable (a receiver can report a fixed volume of 100%) cannot be determined retrospectively: `volume_control_type` was not captured during that historical observation. The current implementation now carries it in `ReceiverStatus`, but that does not add the missing evidence to the 2026-09-26 result, so physical validation remains required. The 100% and "not muted" may also have been PyChromecast defaults rather than reported values; since PR #27 an omitted value is shown as not reported (see "Receiver volume and mute defaults").
- [x] **Volume raise limit (volume-slam protection) implemented and automation-validated, owner decision:** one gesture can raise the volume by at most 10 points above the level the TV last reported; lowering is never limited (0% is one gesture); the reference is only the reported level (never a draft or a command in flight), so once the TV reports the new level it becomes the reference of the next gesture. Home goes to 0%, End to the reference + 10 (never above 100%); at the limit the line under the slider reads "Set volume to 55% - raising is limited to 10% at a time". The limit is applied to the draft, to what the slider shows and again when the command is built (`ui/src/sound.ts`); there is no dialog, override, extra timer, automatic retry or invented value, and one gesture still sends at most one command. A gesture also ends on the pointer or key release, not only on `change`, because WebKit stops reporting `change` once the value is clamped (found in the real window). UI protection only: `ControlService`, the bridge and Rust are unchanged, so a future MCP client is not covered by it. Tests: 238 TypeScript tests at that point (35 for the limit and its release triggers, with 15 mutation checks; 242 now); exercised in the real application against fake TVs only.
- [ ] **Volume and mute are not validated on a real Chromecast:** in the desktop window's real-hardware campaign on `main`, no volume or mute command was sent. Separately, on PR #33 (the Android home-screen widget, open and not merged into `main`), mute and unmute were sent from a phone to a real TV: the receiver's mute state was confirmed, the Cast volume is `fixed`, and no audible change was observed (see `docs/REQUIREMENTS_TRACEABILITY.md`, "Hardware observations"). This waits for the physical validation of the playback commands, its review and an explicit go-ahead. Things to watch there are listed under "Known open items" below (volume tolerance, volume control type, and whether the receiver reports level and mute at all, which since PR #27 shows as "not reported" when it does not).
- [ ] **Playback hardware validation remains incomplete (observations recorded in `docs/CAST_HARDWARE_VALIDATION.md`):**
  - 2026-09-26: one real Pause was sent; physical effect observed; `UNCONFIRMED`.
  - 2026-09-29, `main` `9f083e8`, one YouTube session with an empty `contentId`:
    - Pause: SENT once, application result `UNCONFIRMED` by design (no usable media identity), physical effect observed.
    - Play: SENT once, `UNCONFIRMED` for the same reason, physical effect observed.
    - Seek +30 s: SENT once, `UNCONFIRMED`, physical effect **not** observed. The receiver temporarily reported a position compatible with the target; the session was later replaced (19 -> 20) and the position realigned. **Seek is not hardware-validated.**
  - That Seek shows that a future `CONFIRMED` means "the receiver reported the expected state", never a guarantee of the physical effect.
  - Stop, volume and mute were not sent in this desktop campaign on `main`. Separately, on PR #33 (the Android home-screen widget, open and not merged into `main`), mute and unmute were sent from a phone to a real TV: the receiver's mute state was confirmed, the Cast volume is `fixed`, and no audible change was observed (see `docs/REQUIREMENTS_TRACEABILITY.md`, "Hardware observations").
  - No physical playback-validation checkbox is completed: a command is validated only when its physical effect and a `CONFIRMED` result for identified media are both recorded.
- [ ] Windows is not built, installed or launched (postponed). Android: the MVP's debug arm64 APK passed its gates A-H on a physical phone (Phase 7b, PR #30, merged); receiver status and the control commands are not validated on Android (separate open work).
- [x] **Distributable Python runtime (PR #26, merged):** the development-only `resolve_python()` is replaced by the debug/release resolution of `BridgeProgram`; a release build starts only the bundled frozen bridge, with no fallback to another Python (Phase 7).

## Known open items (audit of 2026-09-26)

First verified in the code on 2026-09-26 and kept current since (last synchronized with `main` `1c71530`). Unchecked items are not fixed on `main`; a checked item was fixed afterwards and says where. None of them is a hardware finding.

- [x] **Receiver volume and mute defaults (PR #27, merged, automation-validated; not validated on real hardware):** PyChromecast's `CastStatus` substitutes `volume.level` 1.0 and `volume.muted` False when the receiver omits them, and `ReceiverStatus.volume_level`/`muted` previously came from `CastStatus`. Before PR #27 an omitted level therefore read as 100% and an omitted mute state as "not muted", and both could confirm a request nobody saw (`set_volume(1.0)`, `set_muted(False)`); a boolean level read as 0 or 100%, and an out-of-range, non-finite or non-numeric level failed the whole status read. The merged implementation reads both from the same fresh receiver status reply as `volume_control_type`: a level is an explicit JSON number within 0-1 (0 and 1 included, returned as a float), anything else (absent, null, boolean, string, out of range, NaN, infinite, a non-object `volume`, no reply) is unknown; the mute state is an explicit JSON boolean, anything else (absent, null, 0/1, string) is unknown. `ReceiverStatus` itself refuses a boolean or non-finite level and a non-boolean mute state. Unknown values reach the bridge as `null` and the UI as "Volume not reported" / "Mute state not reported" with no control. Confirmation: an unknown level or mute state is never a match, so these requests stay `UNCONFIRMED`; one command is sent, nothing is retried or replayed, the shared deadline and the fixed-volume pre-read are unchanged, and play, pause, stop and seek are untouched. Tests: adapter matrix over the raw reply with PyChromecast's defaults cached, service-plus-adapter confirmation for 1.0, 0.0, mute and unmute, domain validation, a UI test for 0% and "Not muted"; three mutations (cached values, boolean level, domain mute check) are each caught. Real hardware: not validated (no command sent).
- [x] **Receiver standby default (PR #28, merged, automation-validated; not validated on real hardware):** the Cast receiver status carries standby as `status.isStandBy`. PyChromecast 14.0.10 (`ReceiverController._parse_status`) substitutes True when it is omitted on a video device (cast type "cast"; None for audio devices and groups), and `ReceiverStatus.standby` came from that parsed `CastStatus` before PR #28. A video receiver that omitted `isStandBy` could therefore be shown with an "In standby" fact in the window (`renderFacts`, shown only for `standby === true`), and a non-boolean value reached the bridge unchecked. No other effect: `ControlService`, the confirmations, availability and every control decision ignore standby, so no false `CONFIRMED` or wrong command can come from it. PR #28 reads it from the same fresh receiver status reply as volume, mute and the volume control type: an explicit JSON boolean is kept, anything else (absent, null, 0/1, string, object, list, a non-object status, no reply) is unknown, whatever PyChromecast's parsed status holds; `ReceiverStatus` refuses a non-boolean standby. The bridge already sends `null` and the window then shows nothing. Tests: PyChromecast's own default demonstrated, an adapter matrix against a cached True, False and None, domain validation; two mutations (cached value, domain check) are caught. Whether real receivers omit `isStandBy` is not observed (no private capture holds a receiver status), and nothing was sent to hardware. `isActiveInput` is not used by control-TV.
- [x] **Bridge recovery (P3, critical-path item G; fixed by PR #29, merged in `1c71530`):** before PR #29, on `main` there was no mechanism to restart a stuck or dead Python bridge. A bridge that stays stuck on one request makes every later request end as `bridge_busy` (not sent) until the application restarts. Each request abandoned behind it keeps one blocking-pool thread waiting for the bridge lock until that lock is released, and a request already `STARTED` keeps its worker blocked on the reply read. A `bridge_busy` returned by the worker itself (it lost the claim after acquiring the lock) is never seen by the caller, whose timeout already returned. Confirmed as the remaining limits by the PR #23 review. The window's startup "background service isn't available" notice is also never cleared on `main`.
- [x] **Bridge recovery (PR #29, merged in `1c71530`, automation-validated):** the process is replaced, never a request. `BridgeSlot` keeps the launcher, which resolves the PR #26 `BridgeProgram` on every launch (a release build can only start its bundled bridge, debug the `.venv`), and `PythonBridge::spawn` builds the command from that program, in its own process group on Unix. A still-pending request that finds the process exited starts a new one before anything is written; a `bridge_transport` failure or a poisoned lock retires (kills and reaps) the process; a request that times out after its claim kills the process it was written to (`written_to`), which frees the stuck worker; one launch at a time, under the bridge lock. The #23 claim is unchanged: `bridge_busy` still means not sent, `bridge_timeout` and `bridge_transport` maybe delivered; no retry, replay or double send. The window's startup notice ends once a request started after it was recorded is answered by the service (a result or a service error); `backend_unavailable`, `bridge_transport`, `bridge_timeout`, `bridge_busy` and unexpected errors never end it, nor does a late answer to an earlier request, and each request's own outcome is shown as it was. Tests on the merged branch: Rust 60 (9 recovery: died before a request, died during one, broken pipe, timeout after the claim, abandoned request never written, concurrent requests after a crash, poisoned lock, start failing then succeeding, start failing), UI 271 (8 for the notice), Python 1116, `release-deb` (strict smoke, fail-closed scans) and the release binary starting its bundled bridge in its own process group. Open: on Windows only the direct child process is killed (no Job Object or process-group equivalent yet), so a descendant holding the pipes can survive; Windows recovery is not validated in CI; a crash loop starts one process per request (no back-off).
- [ ] **Tauri content security policy:** `tauri.conf.json` sets `"csp": null` (with `withGlobalTauri: true`); define a restrictive CSP before a release.
- [x] **Bridge overflow handling (PR #20, fixed and automation-validated):** a JSON integer too large for a float in `seek.positionSeconds` or `discover_devices.timeoutSeconds` returned `internal_error`; the bridge now forwards the number and `ControlService` answers `invalid_argument` before any status read or transport call.
- [x] **Boolean and number inputs at the service (PR #20, fixed and automation-validated):** `ControlService` itself now accepts only a real, finite number (never a bool, a string or an integer too large for a float) for the discovery timeout, the seek position and the volume level, and only a real bool for `set_muted`; anything else is `invalid_argument` before any status read or transport call, so every caller (GUI bridge, a future MCP adapter) inherits the same contract. No maximum seek position is imposed: the domain defines none, so a large finite position is the receiver's to judge.
- [x] **Volume control type (PR #24, merged, automation-validated, no real device involved):** `ReceiverStatus.volume_control_type` accepts only `None` or a `VolumeControlType` (`attenuation`, `fixed`, `master`) and is read from the raw receiver reply, because PyChromecast substitutes `attenuation` when `controlType` is omitted. An explicit `fixed` makes `set_volume` fail as `unsupported_operation` before anything is sent; an unknown type (omitted, null, unrecognized), a failed read or a read after the deadline is never treated as fixed, and `attenuation`/`master` are allowed. The bridge carries `volumeControlType`; the UI offers no slider for an explicit `fixed`, keeping the level readable with a note. Mute is unaffected. Not validated on real hardware.
- [ ] **Volume confirmation tolerance:** `volume_tolerance` is 0.01, so a receiver that settles on its own volume steps answers `unconfirmed` although it acted; decide after the real-hardware volume validation.
- [x] **Slider focus return (PR #14 review P2, fixed in `1bed6a9`, automation-validated):** after a command, focus went back to the seek or volume slider even when the user had deliberately moved focus elsewhere during the command; focus is now returned only if no other control was focused meanwhile (`createFocusReturn` in `ui/src/interaction.ts`, 4 new tests, 242 TypeScript tests). Not re-checked in the real window here.
- [ ] **Assistive-technology slider change:** a value change made with neither pointer nor key events and clamped by the raise limit stays an unsent draft until the next release or refresh (safe, nothing is sent); not tested with a screen reader.
- [ ] **Load media from tools:** `ControlService.load_media` exists and is tested, but it is not exposed through the bridge, MCP, Tauri or the UI. Decided by the owner's scope of 2026-10-08: content is launched on the TV from ChatGPT through MCP tools (item N, Phase 5), not from a search or player in the window; the window launches the TV's applications instead (item Q).
- [x] **Media status freshness (PR #19, fixed and automation-validated, no real device involved):** `PyChromecastTransport.get_status` refreshed only the receiver status and read the media state from PyChromecast's cached `MediaStatus`. PyChromecast 14.0.10 merges every MEDIA_STATUS into that cache: it ignores an empty status list (a session that ended) and keeps any field a message omits, including the old `content_id`. A status read could therefore report a session that had ended, carry an old content id into a new state or session, and let such inherited data confirm a command (a Stop answered with a partial IDLE status was confirmed). Each status read now asks the receiver for its media status within the same deadline and parses only that reply; a field that reply omits stays unknown instead of taking PyChromecast's default (an omitted `currentTime` is not a position of 0, and an omitted `supportedMediaCommands` is not "seek unsupported"; review of PR #19). The request never launches an application: an application without the media namespace has no media session. No reply within the budget is a timeout, never a cached status; a request that cannot be sent is an unavailable device. Deterministic tests drive PyChromecast's real `MediaController` and `MediaStatus.update`; reverting to the cached read or to the app-launching request makes them fail. Hardware results obtained before this fix, including the recorded Pause, were read through the cached media state.
- [x] **Structurally invalid media status replies (PR #20, fixed and automation-validated, no real device involved):** the fresh MEDIA_STATUS reply is checked before PyChromecast parses it. A reply that is not shaped like a media status (no status list, an entry that is not an object) fails the read as `device_unavailable`. Inside a well-shaped entry, a field whose value cannot be what the Cast protocol defines (a non-string content id, content type, player state or title; a negative, non-finite, boolean or non-numeric position, duration or playback rate; a command mask that is not a non-negative integer; metadata or a media block that is not an object) is reported as unknown, exactly like an omitted or null field, never as a plausible value (0, false, an empty string) and never as a crash; the same checks apply to media read from `extendedStatus`, and an unmapped media-channel volume that is not an object is ignored. Previously such values crashed the read, surfaced a receiver error as `invalid_argument`, or reached the domain as a number, an infinite duration or a non-string content id that then crashed the service's identity check.
- [ ] **Status refresh:** the status is read on selection and on demand only; there is no periodic refresh, so the shown position and state can be stale (also needed by the tray, Phase 4b).
- [x] **One application version (PR #24, merged, automation-validated):** Python (`pyproject.toml`, `control_tv.__version__`), Cargo, `tauri.conf.json`, `ui/package.json` and both `package-lock.json` entries declare 0.1.0; `python3 scripts/dev.py version-check` compares the seven declarations and runs first in `dev.py check`, so CI fails on drift. The frozen bridge built on the packaging branch reports 0.1.0 in `ping` (checked by its smoke). No release is published.
- [ ] **License metadata:** no package or application manifest declares the project license (GPL-3.0, `LICENSE`), and "only" versus "or later" is not stated; the packaged runtime's third-party licenses are tracked in Phase 7 and `docs/PACKAGING_LICENSES.md`.
- [x] **Window wording overclaimed delivery (found 2026-10-06 while fixing the same issue in MCP, PR #31; fixed by PR #32, merged in `ded6e9c`; automation-validated):** after a command, the window worded `device_unavailable` as "The command wasn't sent" and `command_rejected` as "It received the command but didn't accept it" (`ui/src/playback.ts`), although the shared layer proves neither: the PyChromecast adapter maps an `OSError` raised while writing to the socket to `device_unavailable`, and every other PyChromecast error, including `UnsupportedNamespace` raised before any write, to `command_rejected`. Both now say the command may or may not have reached the TV and to check the current state before sending it again (`device_unavailable`: "Lost contact with this device", plus the network advice; `command_rejected`: "The command was refused", by the TV or the connection to it). Recovery stays Check state (a status read, never a resend); nothing is retried. Only the window's wording changed: the shared transport contract, MCP and Android are unchanged, and no hardware command was sent. Tests: UI 274 (3 new: the wording for both codes and every command, no command left pending or resent, the sound commands), and reverting the `device_unavailable` wording fails 3 of them. A status read failing with `device_unavailable` keeps its own wording ("Try again" is safe for a read).
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
- [x] **Media session identity (PR #21, implemented and automation-validated, no real device involved):** `MediaStatus` carries the receiver's `mediaSessionId` as an optional `media_session_id` (a non-negative integer; the adapter reports a missing, null, boolean or otherwise unusable value as unknown, and the bridge JSON is unchanged). Cast and PyChromecast semantics: LOAD creates a media session and QUEUE_INSERT/QUEUE_UPDATE stay inside it, so a session can hold several items and the session id never identifies a media item on its own. Rule for play, pause, stop and seek: the non-blank content id captured before the command stays required and must be the same afterwards; when a session id was reported before the command, the confirming read must report the same one (a different session is a new playback of the same content, a missing one is no evidence); a session unknown before the command leaves the content id as the only proof, as before. The session can therefore only turn a confirmation into UNCONFIRMED, never create one: an empty or missing content id stays UNCONFIRMED whatever the session, and nothing is resent. Review follow-ups (PR #21, automation-validated): an explicit identity contradiction reported by any read during a confirmation attempt, either another usable content id (a queue can change item inside one session) or another session, makes that attempt unconfirmable, so a later read back in the original identity can no longer confirm play, pause, stop or seek: A -> B -> A and X -> Y -> X are both covered, together with the simultaneous change. A read that reports no usable content id or no session is rejected on its own but contradicts nothing (A -> absent -> A and X -> absent -> X may still confirm), and each command starts a new, independent check. The receiver's `currentItemId` (the active item of the session's queue, which PyChromecast 14.0.10 does not parse and the adapter reads from the checked reply as a non-negative integer, otherwise unknown) is part of the same guard when it was reported before the command, since two items of one queue can share the content id and the session: an explicitly different item is terminal for the attempt (item 101 -> 102 -> 101 cannot confirm play, pause, stop or seek), a missing item only rejects that read, an item unknown before the command adds no requirement, and it never identifies the media without a content id (automation-validated only; the bridge JSON is unchanged).
- [ ] **Real-hardware check of a receiver application that reports an empty content id** (the motivating case was a YouTube session): with the rule above, play, pause, stop and seek stay UNCONFIRMED by design, with no per-application logic. What a real receiver reports for `mediaSessionId` across play, pause, seek, stop and a queue change is not validated and waits for a TV and an explicit go-ahead.
- [x] **Media details (PR #22, merged, implemented and automation-validated, no new hardware command):** `MediaStatus` now carries `artist` (a non-blank string or unknown), `stream_type` (`buffered`/`live`), `metadata_type` (generic, movie, tv_show, music_track, photo, audiobook_chapter) and `supports_pause`, read from the checked fresh reply rather than from PyChromecast's defaults: an omitted, null, blank, wrongly typed or unrecognized value is unknown. They reach the UI through the bridge JSON (`artist`, `streamType`, `metadataType`, `supportsPause`; an older backend without them reads as unknown) and the Tauri boundary, which relays them untouched. The window shows the artist as a quiet line under the title (nothing when absent), `Live` instead of a length for a live stream, and no Pause, with a note saying why, when the receiver explicitly reports that the media cannot be paused. The fields come from a real YouTube reply (title and artist present, generic metadata, buffered stream, empty content id); repeat mode, queue items and custom data are deliberately not carried.
- [x] **CONFIRMED meaning pinned (automation-validated):** `CONFIRMED` stays "the receiver reported the expected state for the same identified media", never an independent proof of the physical effect (see the Seek finding above). Tests pin a seek confirmed from the receiver's report alone, and no confirmation when the target is reported from a replaced session, after a session round trip, or with an empty content id.
- [x] **Hardware evidence tooling (PR #22, development only, automation-validated):** `scripts/cast_observe.py` records privately a fresh media `GET_STATUS` reply. It writes only under the git-ignored `.ai-private/hardware/` (modes 700/600). It refuses a location that git tracks or that is outside the repository, and a run name that leaves the private root (`..` traversal or an absolute path). What it records is the reply with its `requestId` and type, every media and receiver message broadcast during a window, and the derived domain status. It sends no control command (a static test forbids every command call and allows only a media `GET_STATUS`), retries nothing and is never imported by the product.
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
- [x] receiver/media state validated on a real device through the UI (read-only, 2026-09-26, after PR #9): the connected state, volume, mute state and "nothing playing" were read and refreshed for two devices (the volume and mute values then came from PyChromecast's parsed status and may have been its defaults; see "Receiver volume and mute defaults"); playing media, playback position and a paused/buffering state were not observed on real hardware because nothing was playing;
- [x] playback controls (play/pause toggle, stop, seek) implemented, automation-validated and exercised in the real application against a fake TV;
- [ ] playback controls validated on a real Chromecast (2026-09-29: Pause and Play had a physical effect but stayed `UNCONFIRMED` because the YouTube session reported no usable content id; Seek had no physical effect although the receiver temporarily reported a position compatible with the target; Stop, volume and mute never sent; this criterion remains open);
- [x] volume/mute controls implemented, automation-validated and exercised in the real application against a fake TV, including the 10-point raise limit per gesture (these are the Cast receiver's **logical** volume and mute; the TV's audible output is item Q);
- [ ] volume/mute validated on a real Chromecast (waits for the playback-command physical validation and an explicit go-ahead; no real-hardware result is recorded for the window; the only one is from PR #33, open and not merged: mute and unmute receiver-confirmed with a `fixed` Cast volume and no audible change);
- [x] clear unavailable/error states for the control-backend boundary itself (bridge process unavailable, discovery failure are both surfaced in the UI as plain text; status-read failures - device unavailable, timeout, unknown device, backend unavailable or not responding - are told apart by error code; playback-command outcomes - not confirmed, refused, unreachable, timed out (delivery ambiguous), busy (nothing sent, PR #23), backend unavailable - are worded in plain language and never shown as success);
- [ ] responsive desktop/Android layout (fluid single-column layout with 44px touch targets and controls up to 56px; no horizontal overflow from 320px to 1280px across the status states and the playback-control states in a browser harness with a fake backend, and observed in the real window between 480px and 900px; the Android spike's app rendered on one physical phone (2026-10-06), where its top was under the status bar; fixed on PR #30 by applying the window insets, and with the APK of `a2ff3bb` the page was seen clear of the status and navigation bars; display-cutout and rotation behaviour were not observed separately on the phone, and the layout and touch use were not otherwise reviewed there);
- [x] choose JavaScript/TypeScript and any UI tooling from concrete implementation needs (vanilla TypeScript + Vite: no frontend framework is justified yet by a single-page skeleton).
- [ ] the Cast volume and mute worded as the receiver's (logical) controls, and a fixed-volume receiver never presented as controlling the TV's sound (item M);
- [ ] the TV's applications shown and launched on the TV, per capability (item Q, R11-R12);
- [ ] power on and off, the TV's audible volume and mute, and navigation, per capability (item Q, R7-R10);

Not part of the window (owner, 2026-10-08): a microphone, a voice assistant, a search interface or a video player.

Exit criteria:
- [ ] application controls a TV manually without ChatGPT or MCP (needs a recorded real-hardware command result; with the restored scope this covers the TV's applications, power, audible sound, playback and navigation per capability, items M and Q);
- [x] frontend presentation remains separated from Cast transport/control logic (verified in the code on 2026-09-26: TypeScript and Rust hold no Cast logic; every command goes through the bridge to `ControlService`, and only the adapter imports PyChromecast).

## Phase 4b - Desktop native integration

Critical-path item K (owner order of 2026-10-06): after the MCP adapter and the Android widget.

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

## Phase 5 - Content on the TV from ChatGPT requests (resolution and launch)

Critical-path item N (owner, 2026-10-08). Purpose: a video or documentary asked for in ChatGPT starts **on the TV**. Resolution and launch are backend/MCP operations only: control-TV has no search interface, search results or video player (owner, 2026-10-08). ChatGPT may already supply a provider link or identifier; a backend search is added only if gate G2 decides it is needed.

Deliverables:
- [ ] resolver boundary separate from Cast transport (no Cast code in the resolver, no resolver code in the transport);
- [ ] explicit support for selected content/service sources, with their restrictions (gate G2: official provider API, quota, key and terms, or a link supplied by ChatGPT);
- [ ] metadata and playable-target validation;
- [ ] clear unsupported-content behavior;
- [ ] `load_media` and a provider launch (for example the Cast YouTube receiver) exposed as MCP tools through the same `bridge.dispatch` and `ControlService` (today `load_media` is not exposed anywhere);
- [ ] a verification rule for receivers that report no content id (a YouTube session reported an empty `contentId` on 2026-09-29): never receiver-confirmed without observed evidence; one send, no replay.

Exit criteria:
- [ ] a content target from a ChatGPT request converts into explicit actions on the TV through the shared control layer;
- [ ] service-specific resolution remains decoupled from low-level Cast transport;
- [ ] content launched on a real TV, recorded at each evidence level, with the owner's go-ahead.

## Phase 5b - TV control beyond Cast (power, audible sound, navigation, applications)

Critical-path items P and Q (owner, 2026-10-08; R7-R12 in `docs/REQUIREMENTS_TRACEABILITY.md`). Cast covers playback and the receiver's logical volume and mute; it has no installed-application inventory, and it provides no universal or guaranteed path to the TV's power or audible volume: whether one exists depends on the hardware (for example HDMI-CEC, which a Cast launch can trigger on some setups, a sound bar, or another endpoint of the TV) and is detected and validated per device. On the owner's setup, hardware already shows a Cast mute receiver-confirmed with no audible change (`volume_control_type=fixed`, PR #33 phone tests). Every capability is per device and per endpoint and is claimed only where it is observed on the owner's hardware.

Feasibility (gates G3-G5, read-only, owner go-ahead):
- [ ] each owner TV's platform (Google TV/Android TV, a Cast device on HDMI, a vendor platform), audio path (TV speakers, sound bar, HDMI-CEC) and available control endpoints identified and recorded (no device names or addresses);
- [ ] power on from off and standby per endpoint, including whether HDMI-CEC through a Cast launch works on the owner's setup (gate G4);
- [ ] whether any endpoint lists the installed applications (gate G5); otherwise a launchable catalog only, said as such;
- [ ] licenses of any new library checked (gate G6).

Deliverables (after feasibility):
- [ ] one non-Cast endpoint adapter behind the shared service (pairing and secret storage where needed), capability-detected per device; Cast-only devices report the operations as unsupported;
- [ ] power on and off in the service, bridge, MCP and GUI;
- [ ] the TV's audible volume and mute in the service, bridge, MCP and GUI, distinct from the Cast logical volume and mute;
- [ ] navigation keys and input sources where available;
- [ ] the TV's applications listed (installed where an endpoint allows it, otherwise launchable) and launched on the TV from the GUI and MCP;
- [ ] Linux GUI first, then Android parity.

Exit criteria:
- [ ] each control recorded per device and endpoint at the evidence levels, physical effect included, with the owner's go-ahead for every command;
- [ ] no capability claimed for a TV or endpoint where it was not observed.

## Phase 6 - MCP adapter

Critical-path item J (owner decision 2026-10-06), done: PR #31 merged into `main` (`499bfb2`, 2026-10-07). Automation-validated, and validated with a real MCP client read-only (discovery and one status, below); no control command has been sent through MCP.

Transport decision (verified 2026-10-06 against the sources below; recheck before relying on them later):
- the MCP specification (revisions 2025-06-18 and 2026-07-28) defines two standard transports, stdio and Streamable HTTP, and says clients SHOULD support stdio whenever possible; a local Streamable HTTP server must validate `Origin`, bind to localhost and should authenticate;
- Codex (CLI, desktop app, IDE extension; `learn.chatgpt.com/docs/extend/mcp`, where `developers.openai.com/codex/mcp` redirects) supports stdio servers (`[mcp_servers.<name>]` with `command`/`args`/`env`/`cwd` in `config.toml`, default tool timeout 60 s) and Streamable HTTP servers;
- Claude Desktop supports local stdio servers (`claude_desktop_config.json`), on macOS and Windows only (no Linux build);
- ChatGPT web does not read local client configuration and cannot start a local stdio server: it reaches an MCP server only through a public HTTPS endpoint or OpenAI's Secure MCP Tunnel (`developers.openai.com/apps-sdk/deploy/connect-chatgpt`). The tunnel (`github.com/openai/tunnel-client`) is an outbound-only daemon that can front a local stdio server (`--mcp-command`), but it needs OpenAI tunnel keys, the requests and answers transit OpenAI's service, and which ChatGPT plans may use it is not documented;
- **decision:** a local stdio server only, the smallest transport usable today by Codex (and Claude Desktop where it exists); the tools are transport-neutral (`build_server` takes any transport), so a localhost Streamable HTTP binding or the Secure MCP Tunnel can be added later. No internet-facing server is added; ChatGPT web access is out of scope for PR #31 (owner decision, below) and would need its own security review.

Implementation (PR #31, on `main`):
- [x] `src/control_tv/mcp_server.py`, started by the client as `<checkout>/.venv/bin/python -m control_tv.mcp_server`; the official MCP Python SDK `mcp==1.30.0` (released 2026-09-07), low-level `Server`, stdio transport; logs on stderr only (stdout carries the protocol);
- [x] one process-wide `ControlService(PyChromecastTransport())`, and every tool goes through the window bridge's own `control_tv.bridge.dispatch`: the same parameter validation (booleans are never numbers, `muted` is a JSON boolean), the same `ControlService` calls, confirmations, media-identity guards and shared deadline, the same error codes; the SDK's own schema check is disabled so the shared layer stays the only validator, and the adapter only refuses unknown tools and unexpected argument names (`invalid_argument`). No Cast, validation, confirmation, discovery or identity logic is in the adapter, and it does not depend on Tauri, Rust, the Android plugin or the UI;
- [x] eight tools, addressed by the stable `deviceId`: `discover_devices` (optional `timeoutSeconds`) and `get_status` (read-only, idempotent) and `play`, `pause`, `stop`, `seek` (`positionSeconds`), `set_volume` (absolute `level` 0-1), `set_muted` (absolute boolean, never a toggle); commands are not advertised as idempotent, so a client has no hint to retry them;
- [x] outcomes: a sent command answers `delivery: sent` with `confirmation` `confirmed` (worded as done), `unconfirmed` (sent but not shown in time: "do not resend automatically; call get_status") or `not_checked`; errors keep the shared codes with a delivery that is never stronger than the shared layer proves: `invalid_argument`, `device_not_found` ("run discover_devices"), `ambiguous_target`, `unsupported_operation`, `unsupported_media`, `discovery_failed` -> `not_sent` (refused before any send); `timeout`, `device_unavailable`, `command_rejected` and `internal_error` -> `unknown` ("may or may not have reached the TV; do not resend it automatically; call get_status"). `device_unavailable` is ambiguous because the PyChromecast adapter maps an `OSError` while writing to the socket to it (part of the command may be out), and `command_rejected` because the adapter maps every other PyChromecast error to it, including `UnsupportedNamespace` raised before anything is written; the shared error contract is unchanged (Codex review of `cf43c42`, both P2, fixed on PR #31). Nothing is retried or replayed anywhere;
- [x] privacy: no `host`/`port`, no media `contentId`, and every quoted value in a command `detail` (content ids appear there when the media changed) is hidden; error messages are fixed per code, because the shared layer's text can carry library exception text, addresses or device ids (`invalid_argument` keeps its message, which is about the caller's own arguments); an unexpected failure returns `internal_error` without its text; the log names only the tool and the outcome code. Device names, ids, media titles and the receiver application are visible to the MCP client and its model;
- [x] concurrency: the shared layer has no locks and is single-flight, so tool calls run one at a time (one lock, the call on a worker thread). A call cancelled while it waits never runs; a call cancelled while it runs completes (the worker is not abandoned and keeps the lock) and its answer is lost, so a cancelled command may have been sent: the client must check with `get_status`;
- [x] dependency isolation: `mcp` is an optional extra (`control-tv[mcp]`), installed by `dev.py setup` (`uv sync --locked --extra mcp`) for development; the bridge freeze (`bridge-build`) and the Android preparation (`android-python`) sync their own exact environments without it, the Android wheel set follows only control-tv's runtime dependencies, and `control_tv.bridge` / `control_tv.embedded` never import it (tests);
- [x] tests: 48 MCP tests (tool list and schemas, annotations, every input boundary including booleans as numbers, NaN, ranges, unknown arguments and tools, each outcome and error code with its delivery, one transport call per command and no retry, unconfirmed and not_checked wording, privacy of addresses, content ids, details and exception text, serialization and cancellation, an in-memory MCP client session, the real `python -m control_tv.mcp_server` over stdio with `initialize`, `list_tools` and a `get_status` of an undiscovered device answered `device_not_found` without network), 3 of them through PyChromecast's real send path (a real `SocketClient` and `MediaController` under the real `PyChromecastTransport`, `ControlService` and bridge dispatch, only the socket and the app namespaces simulated): an `OSError` raised by the socket write after the command bytes were handed to it ends as `device_unavailable` with `delivery: unknown`, an `UnsupportedNamespace` raised before any write ends as `command_rejected` with `delivery: unknown` and no claim of receipt, and each MCP call writes at most once with no replay; plus an Android packaging test proving the APK never embeds `mcp` or its dependencies;
- [x] CI: the reviewed head `cf43c42`, run `37489094641`, all four jobs successful: quality 1186 passed, 9 skipped (PyInstaller-only tests), coverage 98.02%; Tauri shell Rust 66, UI 271; Linux release packaging tests 72; Android successful;
- [x] CI after the review's convergence: head `ca9154e`, run `37495645763`, all four jobs successful: quality 1189 passed, 9 skipped, coverage 98.02%; Tauri shell Rust 66, UI 271; Linux release packaging tests 72; Android successful. The intermediate pushes `d7a4db5` and `35dc169` failed the quality job (the first carried the regression tests without the adapter change, the second an import order a stale local Ruff cache had accepted); nothing was merged from them.

Product decisions (owner, 2026-10-06):
- [x] `contentId` stays hidden from MCP;
- [x] the window's 10-point volume raise limit does not apply to MCP: `set_volume` stays an absolute level 0-1;
- [x] ChatGPT web access (OpenAI's Secure MCP Tunnel or a public HTTPS endpoint) is out of scope for PR #31, which is the local stdio foundation; a later tranche would need its own security review.

Limitations and findings:
- [ ] the MCP process has its own `ControlService` and discovery cache, separate from the window's bridge: a `deviceId` must be discovered in the MCP process first (`device_not_found` otherwise), and both processes can talk to the same receiver;
- [ ] the shared error contract stays coarser than delivery: `device_unavailable` and `command_rejected` mix failures before and after a write, so MCP reports both as `unknown` (and the window, since `fix/desktop-delivery-wording`, says the command may or may not have reached the TV); a finer adapter taxonomy is possible later;
- [ ] not packaged: the release `.deb` and the Android app do not contain the MCP server; it runs from a development checkout;
- [x] **real MCP client, read-only (2026-10-06, head `786e428`, recorded separately from the automated tests; on `main` since PR #31):** the documented entry point `.venv/bin/python -m control_tv.mcp_server`, started over stdio. Protocol: the MCP SDK's reference stdio client (`mcp` 1.30.0) completed `initialize` (server `control-tv` 0.1.0, protocol `2025-11-25`) and `tools/list`: exactly the eight tools, with closed input schemas, read-only and idempotent hints only on `discover_devices` and `get_status`. Real client: Codex CLI 0.153.0 (`codex exec`, read-only sandbox, ephemeral session, the server configured only through per-run overrides with `enabled_tools = ["discover_devices", "get_status"]`, so the command tools were never offered to the model) called `discover_devices` (3 receivers found on the real local network, each with `id`, `friendlyName`, `kind`, `modelName` only) and then `get_status` once for the first discovered device (connected; receiver fields reported; no media session at that moment, so `contentId` filtering on live media was not exercised by this run, only by the automated tests). Privacy checks on both results: no IP address, host, port, `contentId`, path, traceback or `Errno`. The server's own log shows exactly one `tools/list` and two tool calls (`discover_devices: ok`, `get_status: ok`); Codex's event log shows only those two MCP calls and no shell or other tool use. **Zero control commands were sent.** Device names, ids and the status reached Codex's model, as documented. No device data is recorded here;
- [ ] commands through MCP against a real receiver (one at a time, `play`/`pause`/`stop`/`seek`/`set_volume`/`set_muted`): not done; only with the owner's explicit go-ahead.

Deliverables:
- [x] small typed MCP tool surface over shared control/domain capabilities (PR #31);
- [x] discovery/status and media-control operations (PR #31; media-control commands not yet run against a real receiver);
- [x] actionable tool errors (PR #31);
- [x] local/security boundary documented (README, `ARCHITECTURE.md`, this phase);
- [x] standalone application remains independent of an embedded AI API client (unchanged: the MCP server is a separate optional process, and the application calls no AI API).

Exit criteria:
- [x] MCP and GUI invoke the same authoritative behavior (same `bridge.dispatch` and `ControlService`);
- [x] tool/schema tests are separated from real-device validation (the read-only real-client run is recorded separately; real-device commands not done);
- [x] MCP lifecycle/state interactions are explicit and testable.

ChatGPT reach (critical-path item O, owner 2026-10-08; gate G1): the voice path is the ChatGPT app's own voice mode calling control-TV's tools. Not established: whether that voice mode can call custom MCP tools on the owner's ChatGPT surface (OpenAI documents voice and custom MCP tools separately, not their combination on that surface), which transport it accepts (a remote HTTPS endpoint or OpenAI's Secure MCP Tunnel, with credentials, and traffic transiting OpenAI), and whether a command can run without an extra confirmation step. G1 decides the central product path, so it is tested first.
- [ ] **read-only feasibility test (S0), right after item L and before substantial work on content tools (item N):** prepared on branch `feat/chatgpt-voice-feasibility` (PR #36, not merged), aligned with Codex's independent audit of 2026-10-08 (issue #34): a separate, fail-closed diagnostic MCP server, `python -m control_tv.mcp_diagnostic`, whose `tools/list` holds exactly one read-only tool without arguments (no discovery, status or TV command tool; any other tool or argument refused without echo); it needs no TV and no discovery, loads no Cast layer, bridge or service, opens no socket, and answers only readiness, version, call number, UTC time and a fixed scope; its log holds one line per call (number, time, outcome); `--self-check` runs the local MCP test; 13 tests. Connection only through OpenAI's Secure MCP Tunnel (outbound-only; runtime key with Tunnels Read + Use; the tunnel client's own unauthenticated health server kept on a Unix socket or loopback, never remote). Protocol in `docs/CHATGPT_VOICE_FEASIBILITY.md`: local self-check, preflight, A text (ChatGPT on the web), B acceptance, C native Voice (ChatGPT desktop app, macOS or Windows: Voice is documented only there, and custom MCP servers only on the web, not mobile), D evidence; GO / CONDITIONAL GO / NO-GO / INDETERMINATE rules; only a matching server log line proves a call. Provisional verdict (official documentation, 2026-10-08): **INDETERMINATE** for a custom MCP tool invoked from native Voice; the owner's Linux desktop can run A but not C. Codex's S0 effort: 1.5-4 person-days, calendar 1-5 business days or unbounded if the account lacks the features. Empirical result: not yet run;
- [ ] a transport the ChatGPT app can use for real, with its own security review and the owner's decision;
- [ ] tools for content on the TV (item N) and, per capability, power, audible sound, navigation and applications (item Q);
- [ ] commands from ChatGPT against a real TV, one at a time, each with the owner's go-ahead.

Next actions: the read-only ChatGPT feasibility test (S0) right after item L; commands through MCP or ChatGPT against a real receiver only with an explicit go-ahead.

## Estimates

Corrected-scope baseline (Codex, adopted after the review of PR #35, 2026-10-08), in **focused effort** (person-weeks of engineering work, reviews included):

- **Core Linux desktop: 5-9 person-weeks.** The capability and evidence model, the read-only ChatGPT feasibility test and then ChatGPT reach, content started on the TV from tools (YouTube first), the desktop hardware validation, the TV endpoint feasibility and, for one TV family, the window's power, audible sound, navigation and applications.
- **Corrected full roadmap: 14-24 person-weeks.** The core, plus Android GUI parity, the completion of the Android widget (PR #33), the Linux tray and release hardening (personal APK signing, license blockers).

Assumptions: one TV family; YouTube as the first provider; no Windows and no Play Store; ChatGPT voice mode proven able to call control-TV's tools (gate G1). The gates are uncertainty modifiers: if G1 fails, the central ChatGPT path needs another route and the estimate is revisited; a TV without a usable endpoint for power, audible sound or applications (G3-G5) shrinks that work to reporting "unsupported" rather than adding it; another TV family or provider adds work.

**Elapsed time is not the same as effort and is not estimated as a fixed figure:** it also depends on review rounds, the owner's go-aheads for every hardware command, the availability of the TVs and the phone for physical tests, platform access for ChatGPT (credentials, transport), and how much two agents work in parallel. It is expected to exceed the focused effort. The phase-1 ranges of both audits are superseded: they included in-app voice and search work that is not requested. Per-PR estimates follow once the gates are answered.

## Phase 7 - Cross-platform packaging

Targets:
- [ ] Android `.apk`;
- [ ] Windows `.exe` / appropriate installer artifact;
- [ ] Debian/Ubuntu `.deb`.

Deliverables:
- [ ] target-specific Tauri configuration (Linux: `src-tauri/tauri.release.conf.json`, PR #26; Windows and Android open);
- [ ] package the required Python runtime/components appropriately for each target (Linux: the frozen bridge, PR #26; Windows and Android open);
- [ ] icons/metadata/version consistency;
- [ ] reproducible release commands (Linux: `dev.py release-deb`, PR #26; Windows and Android open);
- [ ] CI builds where useful (Linux: `linux-release`, PR #26; Android: the `android` job, on `main` since PR #30; Windows open);
- [ ] documented signing/sideloading status (Android: personal sideloading only, no Play Store; the APK is debug-signed today).

Exit criteria:
- [ ] package build and installation/launch validation are tracked separately;
- [ ] each claimed platform is installed and launched on that platform before validation is recorded.

### Autonomous Python bridge sidecar - Linux desktop (critical-path items D and E)

Retained design (owner decision, 2026-10-03), merged with PR #26 into `main` (`eba73f5`). The `.deb` validated on 2026-09-25 (see the Tauri shell status) ran the bridge from the developer's `.venv`: it is **not** a validated distributable package; the release `.deb` below is.

Release command: `python3 scripts/dev.py release-deb` runs `bridge-build`, the strict smoke (`build_bridge.py smoke --strict`, never the informative `bridge-smoke`) and `packaging/release_linux.py`; it leaves `dist/control-tv_<version>_amd64.deb`, `dist/python-bridge.inventory.tsv` and `dist/SHA256SUMS`.

Results (2026-10-04, Ubuntu 22.04; no Cast discovery, no hardware command; local runs on PR #26 commits, CI on PR #26 and on `main`):
- **Frozen bridge:** `--onedir`, 42 MB, CPython **3.12.15** exactly (`packaging/release-python-version`; `bridge-build` creates the build environment on it and the build refuses any other interpreter), PyInstaller 6.22.3 and hooks-contrib 2026.8, all PyInstaller state under `packaging/.pyinstaller/`, never `--clean`, setuptools excluded. Its `ping` reports `controlTvVersion` **0.1.0**, and the smoke now fails on any other version.
- **Isolated smoke:** a copy outside the checkout, an empty environment, a user/mount namespace in which the checkout and the build Python are empty, each checked from inside the namespace before the bridge starts. The release smoke (`smoke --strict`, run by `release-deb` and CI) fails when that isolation cannot be set up or a hidden directory is still visible; the informative `bridge-smoke` says when it ran without isolation: `ping` `ready` 0.1.0, `get_status` of an undiscovered device `device_not_found` without network, exit 0 on end of input. The development bridge fails in the same namespace (control).
- **Real `.deb` installation, validated on this machine** (first run on commit `e50bd33`, a CPython 3.12.13 build made before the release CPython was locked): the release `.deb` (18 MB) was installed with `dpkg -i` (676 files; `/usr/bin/control-tv`, `/usr/lib/control-tv/python-bridge/control-tv-bridge` and its `libpython3.12.so.1.0` all mode 755). The installed `/usr/bin/control-tv` was launched on a private Xvfb display inside a user/mount namespace where the worktree, the main checkout (with its `.venv`) and uv's Python store were empty and `/usr/bin/python3*` unusable, with an empty environment (`env -i`), a working directory outside the checkout, and `PYTHONPATH`/`PYTHONHOME` set as traps. It started `/usr/lib/control-tv/python-bridge/control-tv-bridge` (whose environment had neither variable while the application's had both); a system-call trace showed the application's worker thread writing `{"id":1,"method":"ping","params":{}}` to the bridge and the installed bridge answering `{"id": 1, "ok": true, "result": {"status": "ready", "controlTvVersion": "0.1.0"}}`, the only request of the session; the window showed the normal start screen. Nothing was clicked. `dpkg -P` then removed the package and every installed path.
- **Repeated after the Codex review of `a96ba81`** (2026-10-04, CPython 3.12.15 build, last on `fba82cc` after the rebase on `main` `4eef244`; the previously validated functional head `a5d8e69` and the later documentation-only commits change no code, packaging, workflow or test): `release-deb` (strict smoke, fail-closed scans, inventory, `SHA256SUMS`), real installation, the installed application launched in the same namespace with the release CPython also hidden, the application's `ping` answered `ready` 0.1.0 by `/usr/lib/control-tv/python-bridge/control-tv-bridge` (no `PYTHONPATH`/`PYTHONHOME`), the only request, nothing clicked, then `dpkg -P` removing every installed path.
- **Missing bundle:** a release build without its `python-bridge/` started no bridge, logged "the bundled Python control bridge is missing" and showed "The app's background service isn't available" although `.venv` and `python3` were present.
- **Inventory and checksums:** `dist/python-bridge.inventory.tsv` starts with the bridge, CPython and PyInstaller versions (`# control-tv-bridge 0.1.0, CPython 3.12.15, PyInstaller 6.22.3`) and lists every bundle file sorted by path with SHA-256, size and octal mode (symlinks as links); the build refuses a non-executable bridge. `dist/SHA256SUMS` (`sha256sum` format) covers the `.deb` and the inventory. Content checksums are not bit-reproducible across builds yet (not measured).
- **Tests (PR #26 head before its merge, rebased on `main` `4eef244`):** Python 1116 collected: in the CI quality job (no PyInstaller) 1107 passed and 9 skipped, the expected archive-scanner tests, with coverage 98.43%; locally with PyInstaller required (`CONTROL_TV_REQUIRE_PYINSTALLER=1`) 1116 passed, none skipped; in `linux-release` the 72 packaging tests pass with PyInstaller required. Rust 51 (debug/release resolution, no fallback, missing bundle -> `backend_unavailable`, `PYTHONPATH`/`PYTHONHOME` withheld); UI 263; `cargo clippy --release -D warnings` locally and in the `linux-release` CI job.
- **CI on `main`:** the push run `37211384282` on `eba73f5` is green on all three jobs, `linux-release` included (strict smoke, fail-closed scans, inventory and `SHA256SUMS`, installation, direct ping of the installed bridge, purge, artifact; no GUI).

Findings (2026-10-04):
- [x] **Build-machine path in the frozen bridge (PR #26, fixed):** PyInstaller froze `_sysconfigdata__linux_x86_64-linux-gnu`, where uv writes the build machine's Python install prefix (home directory, so the builder's user name) in 21 of 984 build variables. Impact: privacy and reproducibility; runtime: none found (no bundled dependency reads `sysconfig`). Fixed by excluding setuptools (which alone pulled `sysconfig` in), plus a defensive `/install`-prefixed copy of the module and a build that fails if any file or decompressed archive member names the checkout, the home directory or the build Python prefix.
- [x] **setuptools frozen without need (PR #26, fixed):** 132 of 525 frozen modules; excluded, 354 modules, 42 MB, smoke unchanged.
- [x] **Build-machine paths in the Rust release binary (PR #26, fixed):** the release binary named the builder's `~/.cargo/registry` 195 times (with the real home directory and user name). They were compile-time source paths of dependencies, used only in panic and log location messages, never to open files; they made the binary differ per builder. `release_linux.py` builds with stable `--remap-path-prefix` flags (rustup home -> `/rustup`, Cargo home -> `/cargo`, checkout -> `/control-tv`, passed in `CARGO_ENCODED_RUSTFLAGS`); the rebuilt binary has 0 home-directory and 0 user-name strings and 196 neutral `/cargo/registry/src/<index>/<crate>-<version>/...` paths, and the release refuses a package whose unpacked files (archives included) name the checkout, the home directory, Cargo or rustup. Cargo's `trim-paths` is not used (unstable in Cargo 1.98).
- [ ] **glibc baseline (open):** the bundle copies the build system's `libgcc_s`/`libstdc++` (needed by protobuf's C extension), so it needs the build host's glibc or newer (2.35 here: Ubuntu 22.04, Debian 12 or newer). The release CI job is pinned to `ubuntu-22.04` for that reason; supported distributions are not decided.
- [x] **Archive scan was fail-open (Codex review of `a96ba81`, P2; PR #26, fixed):** the expected bridge executable must open as a PyInstaller archive with a PYZ, and any opening or extraction failure, or a PYZ entry without data other than a namespace package, fails the build; members are read one at a time. Tests: a clean archive, a build path inside a frozen module, an unreadable executable, a missing executable, an unextractable member, a partly readable PYZ whose unreadable module holds the path, a module without data versus a namespace package, an archive without PYZ, and the license inventory on an unreadable archive (run with PyInstaller required in `linux-release`).
- [x] **Smoke isolation could fall back silently (Codex review, P2; PR #26, fixed):** the release smoke fails when the namespace cannot be set up or a hidden directory is still visible; tests cover a missing namespace, a directory left visible (real shell check) and the strict-only release path.
- [x] **CPython patch not locked (Codex review, P2; PR #26, fixed):** releases embed exactly CPython 3.12.15 (`packaging/release-python-version`, `bridge-build` with `uv sync --python 3.12.15`, a build check, uv 0.12.23 pinned in every CI job, the inventory header, the notices and `docs/PACKAGING_LICENSES.md`). Development keeps `.python-version` 3.12; a local release build needs a uv that knows 3.12.15.

Note: `bridge-build` rebuilds `.venv` on the release CPython with the `packaging` group; `setup` removes the group again.

Design (all merged with PR #26):
- [x] the Python runtime is embedded in the application, never taken from the user's system (`libpython3.12.so.1.0` in the bundle; the bridge executable links only `libc`, `libdl`, `libpthread` and `libz` from the system);
- [x] PyInstaller 6.22.3 with pyinstaller-hooks-contrib 2026.8, pinned in a dedicated dependency group and in `uv.lock`;
- [x] a `--onedir` build whose entry point runs `control_tv.bridge`, bundled as the `python-bridge/` Tauri resource;
- [x] release builds start only `resource_dir()/python-bridge/control-tv-bridge[.exe]`; debug builds keep `.venv` + `python -m control_tv.bridge`;
- [x] no release fallback to `.venv`, `python`, `python3`, `PATH` or `PYTHONPATH`, and `PYTHONPATH`/`PYTHONHOME` withheld from the bridge: a missing sidecar is reported as an unavailable backend (tested and observed);
- [x] PyInstaller configuration, work and cache directories are project-local and covered by `clean`/`dist-clean`; never a clean option that can remove a shared or global cache;
- [ ] `THIRD_PARTY_NOTICES` and license files shipped with the sidecar (shipped, generated from the distributions actually frozen, but incomplete: see the license blockers);
- [x] an inventory of the sidecar's files with SHA-256 checksums, and the Linux executable mode checked;
- [x] a Tauri `.deb` built from a release build that includes the sidecar, with SHA-256.

Real `.deb` validation (local, Ubuntu 22.04, on PR #26 commits; see the results above):
- [x] release build;
- [x] real installation;
- [x] launch of the installed application;
- [x] with the checkout/repository unavailable and the `.venv` unavailable;
- [x] no system Python needed by the bridge;
- [x] bridge ping through the installed application;
- [x] uninstallation;
- [x] cleanup and disk measurement recorded.

The Debian/Ubuntu target in "Targets" stays open: no release is published, and public distribution waits for the license blockers.

### Windows desktop (critical-path item F)

Postponed by product decision (2026-10-04): not developed until the owner puts it back on the critical path. Not started. The release resolution already names `control-tv-bridge.exe` (unit-tested only).
- [ ] sidecar built natively on Windows (PyInstaller cannot cross-build);
- [ ] application `.exe`;
- [ ] installer format decided (MSI or NSIS) and built;
- [ ] installation, launch, bridge ping through the installed application and uninstallation on Windows;
- [ ] build-path scan, inventory and checksums on Windows;
- [ ] no dependency on a checkout or a developer Python;
- [ ] bridge recovery stops the whole process tree on Windows and is validated there (PR #29, merged, kills only the direct child on Windows: no Job Object or process-group equivalent yet, so a descendant holding the pipes can survive; not covered by CI).

Android: see Phase 7b (spike gates passed on a physical phone; PR #30 merged).

### Third-party licenses of the packaged runtime

Engineering inventory in `docs/PACKAGING_LICENSES.md`; the package ships `python-bridge/licenses/` and `/usr/share/doc/control-tv/{copyright,rust-licenses/}` (PR #26). No legal validation has taken place.
- [x] inventory of what is actually embedded, from the frozen distributions and the linked crates (PR #26: CPython 3.12.15, 11 Python distributions including typing_extensions, the PyInstaller bootloader, the GCC runtime, 216 Rust crates and the Rust standard library);
- [ ] **BLOCKER (public distribution):** license texts of the libraries python-build-standalone links statically into libpython (OpenSSL, SQLite, zlib, libedit, libffi, xz, bzip2, ncurses, mpdecimal, expat, libuuid, HACL*) are not shipped; uv's install does not contain them;
- [ ] **BLOCKER (public distribution):** zeroconf is LGPL-2.1-or-later and its pure-Python modules sit inside the archive appended to the executable; how its source and replaceability obligations are met (rebuild from the public repository under control-TV's GPL-3.0, or loose replaceable modules, plus a source offer) needs an owner decision, with legal advice if needed;
- [ ] **BLOCKER (public distribution):** nine Rust crates publish no license file (`alloc-stdlib`, `dlopen2`, `libappindicator-sys`, `selectors`, `unic-char-property`, `unic-char-range`, `unic-common`, `unic-ucd-ident`, `unic-ucd-version`);
- [ ] a written source offer for the GPL/LGPL/MPL components (control-TV, zeroconf, certifi, four MPL-2.0 crates, PyInstaller bootloader, GCC runtime);
- [ ] no legal validation has taken place; this inventory is an engineering check, not legal advice.

## Phase 7b - Android application and home-screen widget

Critical path (owner order of 2026-10-06): the Android APK MVP (item H, PR #30, merged); the widget (item I) next, now that the MCP adapter (item J, PR #31) is merged. Android is personal and sideloaded only (`adb install` or a sideloaded APK signed with a personal key): no Google Play publication, Play Console, store listing or store metadata is planned.

Architecture (owner decision 2026-10-04; **the spike passed its gates A-H on a physical phone on 2026-10-06**, PR #30 merged in `3bcb516`; see `ARCHITECTURE.md`, "Android"):
- [x] decision recorded: Tauri 2 Android shell + CPython embedded through Chaquopy 17.0.0 (Python 3.12) + the unchanged `control_tv` package (`bridge.handle_line`, `ControlService`, `PyChromecastTransport`) + zeroconf discovery; one authoritative control engine shared with the desktop bridge;
- [x] target: `arm64-v8a` phones only (Chaquopy's Python 3.12 has no 32-bit ARM or x86 build; the Tauri `arm`/`x86`/`x86_64` flavors are disabled), `minSdk` 24, `targetSdk` 36, application id `io.github.guillaumeboileaupro.controltv`;
- [x] embedded Python packages: the control layer's locked runtime dependencies only, as pure-Python wheels hash-checked against `uv.lock` (`dev.py android-python`), installed by Chaquopy offline (`--no-index`); protobuf runs its pure-Python implementation; zeroconf, which publishes no pure wheel, is built from its locked sdist without its optional Cython extensions and retagged `py3-none-any` (checked free of compiled modules). Since the Codex review of `aad8979` (P2-2) the two wheels built locally (zeroconf, control-tv) are built offline (`pip wheel --isolated --no-index --no-deps --no-build-isolation --no-cache-dir`) with build backends locked in the `android` group (setuptools 84.0.0, poetry-core 2.5.0; checked against `uv.lock` before building) and a fixed `SOURCE_DATE_EPOCH`; the only network access is the hash-checked `uv sync --locked --group android` and the hash-checked download of the locked files. Measured: two consecutive preparations on the same machine gave byte-identical wheels; reproducibility across machines or toolchains is not measured;
- [x] in-process bridge: a Kotlin `ControlBridgePlugin` runs `control_tv.embedded.handle(line)` on one worker thread (never on the Android main thread), once per request, never retried; the Rust shell keeps the PR #23 claim and timeouts around each call (`bridge_busy` not sent, `bridge_timeout`/`bridge_transport` ambiguous, no replay). `backend_unavailable` (certainly not sent) covers every failure before `handle` is entered: Python not starting and, since the Codex review of `aad8979` (P2-1), `control_tv.embedded` failing to import (`EmbeddedHandler`); a failure while or after `handle` runs stays the ambiguous `bridge_transport`;
- [x] multicast lock (`CHANGE_WIFI_MULTICAST_STATE`) held while the app is in the foreground, released in the background; `ACCESS_NETWORK_STATE`, `ACCESS_WIFI_STATE` declared;
- [x] startup diagnostic (PR #30, after the first phone run): once, when the plugin loads, the bridge worker runs `control_tv.embedded.startup_diagnostic()`, which sends a `ping` through the same `handle` path and logs one line under the logcat tag `control-tv` (`python 3.12.x; control_tv 0.1.0 imported; embedded ping ok, controlTvVersion=0.1.0`, or the error code only on failure). It names versions only (no device, address, identifier or media), never touches the network or a Cast device and is never retried; tested in Python (3) and Kotlin (2), and seen on the physical phone on 2026-10-06: `python 3.12.12; control_tv 0.1.0 imported; embedded ping ok, controlTvVersion=0.1.0`.

Known limitations of this architecture:
- [ ] **in-process bridge cannot be recovered:** Python runs inside the app process, so the PR #29 recovery (kill and relaunch the bridge process) does not exist on Android; a request stuck in Python keeps the single worker busy and later requests end as `bridge_busy` until the app is restarted;
- [ ] APK size: the debug APK is about 157 MB, mostly the unstripped debug Rust library (about 128 MB); a release build (stripped, `--remap-path-prefix`, signing) is not done;
- [ ] the debug Rust library contains Cargo registry source paths of the build machine (debug information, not a runtime dependency); a release build must remap or strip them as `release-deb` does;
- [ ] third-party licenses of the embedded runtime (Chaquopy's CPython, zeroconf LGPL, the pure wheels) are not inventoried for Android.

Fallback if the spike fails (decided before the result, not applied): stop and report the blocker to the owner; options to evaluate then are a Kotlin-native Cast transport behind the same bridge protocol or a different embedding of Python. No blocker is worked around silently.

STOP conditions of the spike: Chaquopy cannot embed the dependencies; `control_tv` does not import; pychromecast/zeroconf incompatible; protobuf blocks; multicast prevents discovery; the APK depends on an external runtime; a large rewrite of the Cast engine becomes necessary. None was hit while building (2026-10-04) nor on the physical phone (2026-10-06): the embedded runtime started, `control_tv` imported and answered its ping, zeroconf discovery worked through the multicast lock and protobuf did not block it.

Android validation checklist (spike, PR #30, merged in `3bcb516`; a box is checked only for what was really observed; a build is not an installation, an emulator is not a phone). Phone results come from one physical arm64 Android phone (not an emulator), within the spike scope (`arm64-v8a`, `minSdk` 24), observed by the owner on 2026-10-06: A-D, G and H with the APK built from `9ee916c` (SHA-256 `0122796e...c1417`), E and F with an instrumented APK of PR #30 (built at `3790ffd` or later):
- [x] APK built: debug, arm64-v8a, `minSdk` 24, version 0.1.0 (versionCode 1000), debug-signed, locally with `python3 scripts/dev.py android-apk` (also built by the `android` CI job added on PR #30, see Phase 8); no checkout, `.venv` or host Python inside it: Python comes from Chaquopy's assets and the embedded packages are the 11 wheels above;
- [x] installed on a real phone (`adb install`);
- [x] application launched and rendered on the phone;
- [x] embedded CPython 3.12 started (logcat: the Chaquopy libraries and `libpython3.12.so` loaded);
- [x] `control_tv` imported (logcat, tag `control-tv`: `python 3.12.12; control_tv 0.1.0 imported; embedded ping ok, controlTvVersion=0.1.0`);
- [x] ping answered with `controlTvVersion` 0.1.0 (same line: the ping went through the same `handle` path as every request);
- [x] MulticastLock held while in the foreground (logcat: `multicast lock acquired (held=true)`);
- [x] real discovery: the app's discovery action listed the TVs of the real local network; no receiver name, address or identifier is recorded here or in the repository;
- [ ] real status of a receiver (not attempted);
- [ ] real Play / Pause / Stop / Seek / Volume / Mute: not part of the spike; **no Cast control command was sent** during this validation.

Codex review of `aad8979` (2026-10-06) and its disposition (PR #30, merged):
- [x] **P2-1 (fixed):** a failure to import `control_tv.embedded` after Python started was reported as the ambiguous `bridge_transport`; it is now `backend_unavailable` (not sent). Kotlin tests: start failure, import failure, failure inside `handle` (still ambiguous, `handle` run once), normal relay, and the relay reporting an unavailable bridge once;
- [x] **P2-2 (fixed, with the measured limit above):** isolated `uv build` could resolve build backends from the network at build time; the builds are now offline with locked backends;
- [x] **P2-3 (fixed on PR #30):** Android CI job `android`, green on run `37472885400` (`45b4aaa`), see Phase 8; it does not replace physical-phone validation and does not exercise E/F;
- [x] **P3-1 (fixed):** the README no longer says Android is untouched or has no build;
- [x] **P3-2 (fixed):** the whitespace and end-of-file issues reported by `git diff --check` in three generated Gradle/Kotlin files;
- [ ] **P3-3 (open):** the CPython prefix warnings below, kept as a finding (cause not established).

Findings of the phone run:
- [x] **Wrong launcher icon (owner, physical phone):** the app showed Tauri's default icon; `tauri android init` had generated default resources and the logo had only been rendered for the desktop targets. Fixed on PR #30 (automation-validated, and the Control-TV icon seen on the phone's launcher with the APK of `a2ff3bb`): `packaging/android_icon.py` generates the icon from `assets/logo.svg` without redrawing it (the logo's single polygon and its gradient, scaled and centred in the adaptive icon's 66dp safe circle): an adaptive icon for API 26+ (`mipmap-anydpi-v26/ic_launcher[_round].xml`, vector foreground `drawable/ic_launcher_foreground.xml`, white background `@color/ic_launcher_background`) and the same composition as PNGs for API 24-25 (`mipmap-<density>/ic_launcher[_round].png`, rendered with Inkscape); the default icon resources are removed. Tests: the logo parsed exactly, the logo inside the safe circle, every point and gradient colour kept, committed resources equal to the generator's output, manifest references;
- [x] **Content under the status bar (owner, physical phone):** the top of the page was drawn under the Android status bar. Cause: `MainActivity` draws edge to edge (and Android 15+ enforces it for this `targetSdk` 36) but nothing applied the window insets. Fixed on PR #30 (automation-validated, and with the APK of `a2ff3bb` the page seen clear of the status bar and of the navigation bar on the phone; display-cutout and rotation behaviour were not observed separately on the phone): the activity's content container, which holds the WebView, is padded by the insets the system reports (system bars and display cutout, the larger per side; no fixed size), reapplied when they change, and consumed so the page sees no inset and no CSS `env(safe-area-inset-*)` padding can double it; the bars are transparent with dark icons over a white window background matching the light-only page, in night mode too. The desktop layout is unchanged (Android-only native code; no CSS change). Kotlin tests: status and navigation bars, a cutout larger than the status bar, a side cutout in landscape, no inset;
- [x] **visual validation on the phone (owner, 2026-10-06, APK of `a2ff3bb`, SHA-256 `7f3c6515...3bf9a2`):** PASS for the Control-TV launcher icon, the page clear of the status bar and the page clear of the navigation bar; not covered: display-cutout and rotation behaviour were not observed separately on the phone, and the broader responsive layout and touch use are not fully validated on hardware;
- [ ] CPython logged `Could not find platform independent libraries <prefix>` and `Could not find platform dependent libraries <exec_prefix>` at startup; they did not prevent the start or the discovery, but their cause (Chaquopy's embedded `sys.prefix` layout) is not understood yet and stays tracked;
- the in-process bridge does not inherit the desktop PR #29 process kill/relaunch recovery: see "Known limitations" above (not exercised on the phone).

Android hardware validation still open (after the MVP; no Cast control command has been sent from the Android app on `main`; the widget of PR #33, open and not merged, sent mute and unmute from the phone: receiver-confirmed, `fixed` Cast volume, no audible change; see `docs/REQUIREMENTS_TRACEABILITY.md`, "Hardware observations"):
- [ ] receiver status read on the phone against a real receiver;
- [ ] Play / Pause / Stop / Seek / Volume / Mute from the phone, one at a time, each only with an explicit go-ahead;
- [ ] display cutout and rotation behaviour (not observed separately);
- [ ] broader responsive layout and touch use on the phone.

Android application:
- [x] the Android application reuses the shared control core as decided above, with no duplicated Cast logic (on `main` since PR #30; its discovery path ran on a physical phone);
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

- [x] verify the Tauri 2 build (hosted run `36135704584`, job "Tauri shell (Rust and frontend)", 3m37s, all steps passed, including a real debug `.deb` bundle, which still depends on the developer `.venv` and is not a distributable package);
- [x] verify the frontend build (`tsc && vite build`, part of the same hosted run);
- [x] verify Python/Tauri integration (`cargo test` in that job spawns the real `control_tv.bridge` process and pings it);
- [x] add Linux build checks (the job above);
- [ ] add Windows build checks;
- [x] add Android build checks: job `android` (PR #30, on `main`): Android clippy, `android-apk` (wheel preparation and the arm64 debug APK), the Kotlin JVM tests, the APK SHA-256 and a 7-day build artifact; green on PR #30 run `37472885400` (`45b4aaa`, about 6 minutes) and on `main` `3bcb516` (push run `37486772988`); the CI APK differs from local builds, APK reproducibility is not claimed; build-level evidence only, no device or emulator;
- [x] keep CI build success distinct from real Chromecast/TV hardware validation (this CI job never touches a Cast device; it built/packaged/pinged the bridge process only).

### Continuous delivery and packaging

- [x] build the Linux release sidecar in CI: job `linux-release` (PR #26, on `main`), `ubuntu-22.04`, uv 0.12.23, CPython 3.12.15: `cargo clippy --release`, `release-deb` with the strict smoke (the checkout and the build Python verified empty in the namespace, `ping` 0.1.0), the fail-closed scans, Rust notices (9 crates still without a license file), inventory and `SHA256SUMS`; the packaging tests with PyInstaller required (72, none skipped); installation of the package, a direct ping of the installed bridge from outside the checkout, purge; the `control-tv-linux-release` artifact (kept 14 days). Green on `main` `eba73f5` (push run `37211384282`); before the merge, last on PR #26 run `37208616790` (`0a61a14`). It does not launch the GUI application;
- [x] smoke-test the sidecar outside the checkout (ping without the repository or `.venv`), with strict isolation (`linux-release`);
- [x] check the sidecar's file inventory, license notices and SHA-256 checksums in CI (`linux-release`; the notices are incomplete, see the license blockers);
- [x] build the Debian/Ubuntu package in CI (Tauri release `.deb` with the sidecar, `linux-release`; the separate `Tauri shell` job still builds a debug `.deb`, which depends on `.venv` and is not a distributable package);
- [ ] install, launch, ping and uninstall the `.deb` in CI (`linux-release` installs, pings the installed bridge directly and purges; launching the GUI application in CI is not done);
- [x] upload the packaged artifacts (`linux-release`, kept 14 days);
- [ ] build and test the Windows sidecar and application natively in CI;
- [ ] build the Windows installer/application artifact;
- [ ] build the Android APK (the `android` job on `main` builds and uploads the debug arm64 APK; a personal release-signed APK is not built);
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

Before a slice is declared READY FOR CODEX REVIEW or READY FOR MERGE (owner rule, 2026-10-03):
- `DEVELOPMENT_PLAN.md` is synchronized in the same iteration, including "Current state and critical path";
- new findings are recorded, even when not fixed;
- completed items are checked only with evidence at the required level, and unvalidated items stay open;
- tests, CI and hardware results are kept distinct, and an unmerged pull request is never described as part of `main`;
- obsolete or contradictory text is corrected rather than appended to;
- disk cleanup is done and its result recorded.

A slice whose `DEVELOPMENT_PLAN.md` is not synchronized is INCOMPLETE.

## Progress rule

Every actionable development item uses a Markdown checkbox. `[x]` means the work and relevant validation are complete; future work remains `[ ]`. Update this plan continuously in the same iteration that changes project state, including newly discovered work, review findings and blockers. Do not defer plan synchronization until the end of an iteration.

A deliverable or exit criterion whose wording refers to real hardware, a real device or a real platform is checked `[x]` only after that validation actually ran on that hardware/platform, with the evidence recorded in the handoff. Passing automated tests against a fake/simulated transport, fake TV, fake clock or emulator is real, valuable engineering progress, but it is never by itself sufficient to check such an item; simulated validation is never substituted for or presented as hardware validation. When a deliverable bundles implementation with hardware validation (for example "receiver/media state synchronization"), split it in this file into two lines - one for the implementation, checked once it is built and covered by deterministic tests, and one for real-device validation, checked only once that validation actually happened - rather than checking the combined line early.
