# Requirements traceability

Reconciliation of the original product scope (issue #34). This file traces each requirement to what exists, how it is validated and what is missing; `DEVELOPMENT_PLAN.md` holds the actionable items and their checkboxes, and stays the source of truth for progress. Nothing here marks work as done.

Authority, most recent first: the owner's correction of 2026-10-08 07:29 UTC on issue #34 (it supersedes the issue body, the earlier comments and both assistant audits where they conflict); the owner's convergence comment and additions of the same day; the issue body; the repository documents since 2026-09-25.

## Product scope

The original goal, from the 2026-09-24/25 voice discussion, was lost in the repository documents from the first commit, which described only a Chromecast controller with an optional MCP adapter. Restored:

- **Voice is ChatGPT's own.** The owner speaks in the existing ChatGPT app's voice mode ("lance-moi ce documentaire sur la TV"); ChatGPT carries the request out through control-TV's tools, without needless dialogue or confirmation: ChatGPT voice -> integration/MCP tools -> shared control-TV service -> TV. Whether ChatGPT's voice mode can call custom MCP tools on the owner's ChatGPT surface is not established and must be shown empirically before it is promised.
- **control-TV is a graphical remote.** On Linux and Android: discover and select TVs, show the TV's applications and launch them on the TV, power on and off, the TV's audible volume and mute, playback (play, pause, stop, seek) and navigation. It works without ChatGPT, MCP or any AI API.
- **Content plays on the TV.** A documentary or video asked for in ChatGPT starts on the TV through tools and a compatible provider or protocol. Content resolution and launch exist only as backend/MCP operations for that purpose.

Two entry points, one engine:

```text
ChatGPT app (voice or text) -> integration / MCP tools --+
                                                          +-> shared control-TV service -> TV endpoints
control-TV GUI (Linux, Android) -> Tauri -> bridge -------+   (Cast today; other endpoints only where proven)
```

ChatGPT never talks to the TV directly: every action goes through the shared service.

### Not requested (superseded proposals)

These appeared in the issue body or in the assistant audits and are **not** requested (owner correction, 2026-10-08). They are not roadmap items:

- a microphone, speech-to-text engine or local voice assistant in control-TV (Linux or Android);
- a local natural-language intent parser for control-TV;
- a natural-language or video search interface, or search results, in control-TV;
- a video player or embedded playback in control-TV;
- any description of ChatGPT connecting directly to the TV.

## Evidence levels

A command outcome is reported at the highest level actually established, never higher:

| Level | Meaning | Who establishes it |
|---|---|---|
| Not sent | refused before any send (`invalid_argument`, `device_not_found`, `unsupported_operation`, ...) | shared service |
| Unknown | may or may not have reached the TV (`timeout`, `device_unavailable`, `command_rejected`, `internal_error`) | shared service |
| Sent | the transport call returned with no delivery error mapped; this does not say the receiver answered, acted or confirmed anything | shared service (`ok`, `confirmation` = `unconfirmed` or `not_checked`) |
| Receiver-confirmed | a later status read from the receiver reported the requested state, for the same identified media where media is involved; the only level based on a receiver report | shared service (`confirmation` = `confirmed`) |
| Physical effect observed | a person saw or heard the effect on the TV | hardware validation record only |

Receiver-confirmed is not physical: a Cast receiver can report a state that the TV's picture or sound does not show (Seek on 2026-09-29; mute with a fixed volume on the PR #33 phone tests). Volume and mute are tracked per endpoint: the **Cast logical** volume and mute of the receiver versus the **audible** output of the TV or sound bar. With `volume_control_type=fixed`, the Cast volume does not control the TV's sound, and a Cast mute may only set the receiver's own flag.

Capabilities are per device and per endpoint: supported, unsupported, or unknown (not probed). A capability is never assumed for all TVs.

## Requirements

Statuses: **active**, **rewritten** (kept with the corrected scope), **superseded** (not requested). IDs are those of the phase-1 audit, kept for traceability.

| ID | Requirement | Status | Exists today | Automated validation | Real-hardware evidence | Missing | Proposed PR |
|---|---|---|---|---|---|---|---|
| R1 | ChatGPT voice (and text) -> control-TV tools -> shared service -> TV, without needless confirmation dialogue | active | Local stdio MCP server, 8 Cast tools (PR #31) | 48 MCP tests | A real MCP client (Codex CLI), read-only: discovery and one status; no command through MCP; ChatGPT not connected | Proof that ChatGPT voice mode calls the tools (G1, tested first), a transport the ChatGPT app can reach, its security review, tool annotations that let a command run in one call where the platform allows, tools for R4, R7-R12 | S0, then P4 (and the tools of P3, P6-P8) |
| R2 | In-app voice (microphone, speech-to-text, intent) | **superseded** | - | - | - | not requested | none |
| R3 | Content resolution for a request made in ChatGPT (backend/MCP only; ChatGPT may already supply a provider link or identifier) | rewritten | Nothing | - | - | A resolver boundary that turns a provider reference (and, only if decided, a backend search) into a target the TV can play; provider restrictions (G2) | P3 |
| R4 | Launch the requested content ON THE TV, verify the observed playback, never replay on ambiguity | rewritten | `ControlService.load_media(URL)` with URL-bound confirmation; not exposed anywhere | service and adapter tests | none; a YouTube session reported an empty `contentId`, so play/pause on it cannot be receiver-confirmed by design | MCP exposure, a provider launch (e.g. the Cast YouTube receiver), a verification rule for receivers that report no content id | P3 |
| R5 | Play, pause, stop, seek from the GUI and ChatGPT | active | Window, MCP, widget (PR #33, open) | Python, Rust, UI, MCP, Kotlin | Pause and Play: physical effect, `unconfirmed`; Seek: no physical effect; Stop: never sent; widget play/pause: receiver-reported state changed, result `unconfirmed` | A receiver-confirmed command on identified media; Stop; commands through MCP | P2 |
| R6 | Cast logical volume and mute (infrastructure) | active | `set_volume`, `set_muted`; `fixed` refuses `set_volume` | yes | widget (PR #33): mute sent and not audible; then mute/unmute receiver-confirmed with `fixed` volume, not audible | Labelled as Cast logical in GUI and tools; the `SET_VOLUME` reply is not inspected (open finding on PR #33) | P1 |
| R7 | The TV's audible volume +/- and mute, distinct from Cast | active | none | - | disproved for Cast on the owner's setup (fixed volume) | An endpoint that reaches the TV or sound bar (G3), capability detection, honest "unsupported" | S1, P5, P6 |
| R8 | Power on (distinct from waking a Cast receiver) | active | none (`standby` is read-only) | adapter tests for reading `standby` | none | Endpoint per device (G4) | S1, P5, P6 |
| R9 | Power off / standby | active | none | - | none | as R8 | S1, P5, P6 |
| R10 | Navigation (directional keys, back, home) and input sources where available | active | none | - | none | Endpoint (G3) | S1, P5, P7 |
| R11 | Discover the TV's installed applications where an endpoint allows it; say so when it does not | active | only the running receiver's `appId` | - | none | Endpoint able to list applications (G5); Cast offers no inventory | S1, P8 |
| R12 | Show the TV's applications in the GUI and launch them on the TV; a known Cast app id is not an installed-app inventory | active | none | - | none | Launchable catalog per endpoint, launch, failures shown per device | P8, P9 |
| R13 | Discovery, selection by stable id, status, recovery | active (foundation done) | yes | extensive | desktop read-only discovery and status; Android discovery; widget picker and status (PR #33) | Hardware stale-connection recovery | P2 |
| R14 | GUI works without ChatGPT, MCP or any AI API | active (holds) | yes | yes | - | - | - |
| R15 | Evidence levels above; no replay | active | sent vs receiver-confirmed in code; physical effect only in records | extensive | real logs show receiver-confirmed without audible effect | The levels and the logical/audible distinction in tools and GUI wording | P1 |
| R16 | Linux desktop and Android sideload APK; Windows deferred | active | release `.deb` validated on Ubuntu 22.04; APK MVP | CI | `.deb` installed and launched; APK on a phone | Release-signed APK; license blockers before public distribution | - |
| R17 | One shared Python service; other TV endpoints are adapters behind it; no second Cast engine | active (holds) | yes | static guards | - | Must hold for every new endpoint and the resolver | all |
| R18 | GUI for power, audible sound, application catalog and launch, playback and navigation; minimal and accessible; no search, microphone or player | rewritten | playback and Cast sound only | UI tests | desktop read-only; phone layout partly | The listed controls, per capability | P6-P9 |
| R19 | Hardware validation matrix per device, endpoint and evidence level | active | `docs/CAST_HARDWARE_VALIDATION.md`, plan records | - | partial (below) | One matrix covering every endpoint | P1, P2 |
| R20 | Process: cleanup, private agent files, Conventional Commits, isolated PRs | active (holds) | yes | - | - | - | - |

## Hardware observations

Facts only; none is turned into a validation it does not support. Device names, addresses and identifiers are never recorded.

| Date | Surface, build | Operation | Highest level reached | Physical effect | Note |
|---|---|---|---|---|---|
| 2026-09-25/26 | desktop window | discovery, selection, status (read-only) | - | - | real devices found and read |
| 2026-09-26 | desktop window | Pause x1 | Sent (`unconfirmed`) | yes | |
| 2026-09-29 | desktop, `main` `9f083e8`, YouTube session | Pause x1, Play x1 | Sent (`unconfirmed`, no usable content id) | yes | |
| 2026-09-29 | same | Seek +30 s x1 | Sent (`unconfirmed`) | **no** | the receiver briefly reported a matching position |
| 2026-10-06 | Android APK (PR #30) | discovery | - | - | real receivers listed |
| 2026-10-06 | MCP, real Codex CLI client | discovery, one status | - | - | 3 receivers; zero commands |
| 2026-10-07 | widget, PR #33 builds (open) | Refresh, TV picker, update redraw | - | - | read-only paths PASS; 3, later 2, TVs discovered |
| 2026-10-07 | widget, PR #33 `1ee07cf` | Mute x1 | Sent | **no** (TV not muted) | a second Mute and a Refresh during it were turned down, nothing sent |
| after 2026-10-07 (owner, issue #34) | widget, PR #33 builds | Mute/unmute (repeated) | Receiver-confirmed | **no** audible change | `volume_control_type=fixed`: the receiver's own flag only |
| after 2026-10-07 (owner, issue #34) | widget, PR #33 builds | Play/Pause | Sent (`unconfirmed`) | the receiver-reported state changed | |
| after 2026-10-07 (owner, issue #34) | widget, PR #33 builds | status of the chosen TV | - | - | `device_not_found` until the picker chose a new stable id; 2 TVs discovered; Refresh then Status |

## Feasibility gates

Each is verified on the owner's actual platform or hardware before the work that depends on it is promised; G1 comes first, because it decides the central path:

- **G1, ChatGPT reach:** whether the owner's ChatGPT app, in voice mode, can call custom MCP tools; which transport it accepts (remote HTTPS endpoint, OpenAI's Secure MCP Tunnel), the credentials it needs, the fact that traffic transits OpenAI, and whether command tools can run without an extra confirmation step.
- **G2, content provider:** the official YouTube Data API (quota, key, terms) or a provider link supplied by ChatGPT; what the Cast YouTube receiver reports after a launch, and whether any observable state can confirm the requested video.
- **G3, the owner's TVs and audio path:** model and platform of each TV (Google TV/Android TV, a Cast dongle on HDMI, a vendor platform), TV speakers or sound bar, HDMI-CEC, and which control endpoints actually exist (for example a paired Android TV remote protocol or a vendor API).
- **G4, power and audible sound:** power on from off, standby and audible volume per endpoint (HDMI-CEC, which a Cast launch can trigger on some setups, a remote-protocol key, Wake-on-LAN, a vendor API); Cast offers no universal or guaranteed path to either.
- **G5, applications:** whether any available endpoint lists installed applications; otherwise only a launchable catalog, said as such.
- **G6, licenses:** of any new library, under control-TV's GPL-3.0 and for Android packaging.

## Proposed pull requests

Small, independent, one shared engine; each synchronizes `DEVELOPMENT_PLAN.md`. None is created yet beyond D0.

| PR | Scope | Depends on | Acceptance |
|---|---|---|---|
| D0 | This documentation reconciliation | owner review, Codex review | scope, matrix and plan reconciled; no item marked done; history kept |
| S0 | **First, gate G1:** read-only ChatGPT feasibility test, right after D0 and before substantial P3 work: only read-only tools offered through a temporary transport the owner authorizes, called from ChatGPT text then voice mode | D0, owner authorization of the transport | ChatGPT voice observed calling a control-TV tool (or the failure recorded); no TV command |
| P1 | Capability and evidence model: per-device capabilities (endpoint, Cast volume type, supported operations), the evidence levels and the Cast-logical/audible distinction in tool answers and GUI wording | - | a fixed-volume receiver is never presented as controlling the TV's sound; tests; no new command path |
| P2 | Desktop hardware validation tranche, one command at a time | owner go-ahead per command | each command recorded at the levels above |
| P3 | Content on the TV from tools: expose `load_media` and a provider launch (e.g. YouTube by identifier or link) as MCP tools, behind a resolver boundary; verification of observed playback | G2, and S0's result before substantial work | nothing confirmed without observed evidence; one send, no replay; no GUI search or player |
| P4 | ChatGPT reach for real: the transport and its security review, then the command tools | S0, owner decision | commands from ChatGPT only with the owner's go-ahead, one at a time |
| S1 | Read-only feasibility spike on the owner's hardware for G3-G5 | owner go-ahead | each TV's endpoints and capabilities recorded; no command without go-ahead |
| P5 | One non-Cast endpoint adapter proven by S1 (pairing, secret storage) behind the shared service | S1, G6 | capability-detected per device; Cast-only devices report unsupported |
| P6 | Power on/off and audible volume/mute in service, bridge, MCP and GUI | P1, P5 | hardware matrix per device; nothing claimed beyond what was observed |
| P7 | Navigation keys and input sources in GUI and MCP | P5 | as P6 |
| P8 | Application catalog and launch on the TV, GUI and MCP | S1 (G5), P5 or Cast app ids | "Ouvre Netflix" works or fails honestly per device; launchable is never called installed |
| P9 | Android GUI parity for P6-P8 | P6-P8 | phone visual and hardware validation |

The Android widget (PR #33), the Linux tray and Windows stay separate items of the plan.

## Estimates

Corrected-scope baseline (Codex, adopted after the review of PR #35, 2026-10-08), in **focused effort** (person-weeks of engineering work, reviews included):

- **Core Linux desktop: 5-9 person-weeks.** The capability and evidence model, the read-only ChatGPT feasibility test and then ChatGPT reach, content started on the TV from tools (YouTube first), the desktop hardware validation, the TV endpoint feasibility and, for one TV family, the window's power, audible sound, navigation and applications.
- **Corrected full roadmap: 14-24 person-weeks.** The core, plus Android GUI parity, the completion of the Android widget (PR #33), the Linux tray and release hardening (personal APK signing, license blockers).

Assumptions: one TV family; YouTube as the first provider; no Windows and no Play Store; ChatGPT voice mode proven able to call control-TV's tools (gate G1). The gates are uncertainty modifiers: if G1 fails, the central ChatGPT path needs another route and the estimate is revisited; a TV without a usable endpoint for power, audible sound or applications (G3-G5) shrinks that work to reporting "unsupported" rather than adding it; another TV family or provider adds work.

**Elapsed time is not the same as effort and is not estimated as a fixed figure:** it also depends on review rounds, the owner's go-aheads for every hardware command, the availability of the TVs and the phone for physical tests, platform access for ChatGPT (credentials, transport), and how much two agents work in parallel. It is expected to exceed the focused effort. The phase-1 ranges of both audits are superseded: they included in-app voice and search work that is not requested. Per-PR estimates follow once the gates are answered.
