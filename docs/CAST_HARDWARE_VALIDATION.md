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
- No command result (load, play, pause, stop, seek, volume, mute) is recorded yet.

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
