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
- [ ] extend `clean` / `dist-clean` / disk-usage coverage to Tauri, Rust and Android output once that tooling exists, validated against real generated output;
- [x] small, reviewable iterations and Conventional Commit / pull-request workflow.

Exit criteria:
- [x] project-owned versus shared caches are clearly distinguished;
- [x] architecture is documented consistently across project context files;

## Phase 1 - Shared control foundation

Deliverables:
- [ ] define shared control/domain interfaces used by GUI and MCP;
- [ ] Python project/package structure and typed device/connection models;
- [ ] explicit errors and operation results;
- [ ] integrate the selected Python Chromecast modules behind a focused adapter;
- [ ] add native/Rust components only where a measured or platform requirement justifies them;
- [ ] focused deterministic unit tests.

Exit criteria:
- [ ] control/domain behavior is testable independently of the UI;
- [ ] GUI/MCP concerns are absent from low-level Cast integration;
- [ ] tests cover meaningful deterministic behavior.

## Phase 2 - Cast discovery and connection

Deliverables:
- [ ] LAN discovery using the selected Chromecast integration;
- [ ] stable device selection separate from display names;
- [ ] bounded discovery/connection timeouts;
- [ ] connection lifecycle and recovery from unavailable devices;
- [ ] receiver/device status retrieval.

Exit criteria:
- [ ] at least one real compatible device can be discovered and addressed when hardware validation is available;
- [ ] failures are represented explicitly rather than as false success.

## Phase 3 - Media controls

Deliverables:
- [ ] play/load supported media;
- [ ] pause/resume and stop;
- [ ] seek where supported;
- [ ] volume and mute;
- [ ] receiver/media state synchronization;
- [ ] validation of URLs, content types, ranges and application identifiers.

Exit criteria:
- [ ] supported operations have explicit results/errors;
- [ ] real-device validation distinguishes a sent command from a confirmed resulting state.

## Phase 4 - Lightweight Tauri UI

Deliverables:
- [ ] device discovery/selection view;
- [ ] current receiver/media state;
- [ ] playback controls;
- [ ] volume/mute controls;
- [ ] clear unavailable/error states;
- [ ] responsive desktop/Android layout;
- [ ] choose JavaScript/TypeScript and any UI tooling from concrete implementation needs.

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

## Phase 8 - Release readiness

Verify:
- [ ] manual control without AI/MCP;
- [ ] discovery/reconnection and error recovery;
- [ ] Cast operations on real hardware;
- [ ] MCP adapter independently;
- [ ] Android, Windows and Debian/Ubuntu packages;
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

Every actionable development item uses a Markdown checkbox. `[x]` means the work and relevant validation are complete; future work remains `[ ]`. Update this plan in the iteration that changes project state.