# Chromecast physical validation checklist

This checklist stays open until each item is executed against a real Chromecast or Google TV. Automated tests, fake transports, fake TVs and emulators do not satisfy any item below; they are recorded as automated validation, never here.

Record before testing:

- date, tester, control-TV commit and operating system;
- the general device type (Chromecast, Google TV, Android TV with Google Cast, other) and, if useful, model and firmware;
- media URL/content type used, excluding credentials or private tokens;
- configured discovery, connection, request, recovery and confirmation timeouts.

**Privacy:** device UUIDs, friendly names, IP addresses, network names and screenshots showing them are recorded only in a private local note (for example a git-ignored handoff), never in this file, `DEVELOPMENT_PLAN.md`, a commit, an issue or a pull request. Public records name the device type only.

## Recorded results

- 2026-09-26, read-only, recorded in PR #9: discovery with a 5 s bound (it returned after about 5 s), selection by UUID, receiver status read, a second discovery returning the same UUID, a second status read, and `close()` called twice without error. No command was sent.
- 2026-09-26, read-only, through the desktop application (PRs #10, #13 and #14): discovery, selection by stable id, status read and refresh, rediscovery keeping the selection, change of selection, and the volume/mute display. No command was sent.
- 2026-09-26, command validation tranche: one `Pause` command was sent. Playback actually stopped and the application status showed `Paused`, but the command result was `UNCONFIRMED`. A later `Check state` was read-only and did not replay the command. Several hours later the media session had disappeared and the receiver was idle; that later state cannot be attributed to the earlier Pause. `Play`, `Seek`, `Stop`, volume and mute were not sent. No broader physical command-validation checkbox is completed from this observation.
- 2026-09-29, command campaign on `main` `9f083e8` (PR #21 merged), one Android TV-based Cast receiver running the YouTube application, one command at a time with an explicit owner authorization each, every command sent exactly once (no retry, no replay), observed physically by the owner. The YouTube session reported an empty `contentId` (title, artist, `mediaSessionId` and `currentItemId` were reported), so the application contract could not confirm any command:
  - **Pause**: sent once; application result `UNCONFIRMED` ("media identity was not reported before the command"); the receiver then reported `paused`; physical effect observed by the owner: **yes**.
  - **Play**: sent once; application result `UNCONFIRMED` (same reason); the receiver then reported `playing`; physical effect observed by the owner: **yes**.
  - **Seek +30 s**: sent once (target = fresh position + 30 s); application result `UNCONFIRMED` (same reason); the receiver then reported a position equal to the target plus the elapsed time; physical effect observed by the owner: **no**, the picture did not move. A later read-only status showed the session replaced (`mediaSessionId` 19 -> 20) and a position back on the no-seek timeline. **Seek is not hardware-validated.**
  - Stop, volume and mute were not sent.

**What CONFIRMED means.** A `confirmed` result means the receiver *reported* the expected state for the same identified media within the confirmation window. It is not an independent proof of what the screen shows: the Seek above shows a receiver reporting a state its picture did not reach. Hardware validation therefore always records the physical observation separately from the application result, and a command is hardware-validated only when both are recorded.

**Evidence capture.** `scripts/cast_observe.py` (development tooling, not part of the product) records what a receiver reports, privately under the git-ignored `.ai-private/hardware/`: a fresh media `GET_STATUS` reply with its `requestId`, every message the receiver broadcasts during a window (`watch`), and the derived domain status. It sends no control command; commands stay a separate, explicitly authorized step.

## Discovery, identity and status

- [x] Start from a closed transport and discover the physical device within the configured timeout (PR #9: about 5 s for a 5 s bound).
- [x] Record the discovered UUID privately and select the device by UUID, not friendly name (PR #9; the UUID is kept out of public records).
- [ ] Rename the device, rediscover it and verify that the UUID remains the selection key.
- [ ] Read receiver and media status and compare every reported field with the device UI; record fields the receiver does not report as unknown, never inferred.
- [ ] Repeat discovery and verify that superseded connections/socket workers terminate (repeated discovery with a successful status read after it is recorded in PR #9; worker termination itself was not checked on hardware).
- [ ] Call `close()` twice and verify no active project-owned Cast connection or worker remains (two calls completed without error in PR #9; the absence of remaining connections or workers was not checked).

## Deadline and recovery behavior

- [ ] Make the selected device unavailable before a status read; measure elapsed time and verify it does not exceed the supplied status budget beyond measurement tolerance.
- [ ] Change the device address or otherwise make the cached connection stale; verify one bounded rediscovery by the same UUID and successful recovery when reachable.
- [ ] Exhaust the budget during connection, cleanup, rediscovery and receiver-status phases separately where practical; verify an explicit timeout/unavailable error and no fabricated status.
- [ ] Interrupt connectivity immediately after sending a command; verify the command is not replayed automatically because delivery is ambiguous.

## Media and receiver commands

For every command, record separately: API call attempted, command accepted/delivered, resulting state observed, confirmation result and elapsed time.

- [ ] Load a valid HTTP(S) media URL with its actual MIME content type and verify the observed content ID.
- [ ] Play/resume, pause and stop; confirm only states explicitly reported by the receiver.
- [ ] Seek to zero and a non-zero position on seekable media; record unsupported-seek behavior on non-seekable media.
- [ ] Set volume to 0, an intermediate value and 1; compare the reported receiver volume.
- [ ] Mute and unmute; compare the reported receiver mute state.
- [ ] Verify playing position advances between Cast events and paused position remains at the last reported value.

## Invalid inputs and evidence

- [ ] Verify malformed/non-HTTP media URLs and malformed MIME types are rejected before any network command.
- [ ] Verify negative/non-finite seek values, out-of-range/non-finite volume values and non-boolean mute values are rejected before any network command.
- [ ] Attach sanitized logs/timings and list every unreported or device-specific field.
- [ ] Update `DEVELOPMENT_PLAN.md` only for the physical checks actually completed, citing the evidence location.
