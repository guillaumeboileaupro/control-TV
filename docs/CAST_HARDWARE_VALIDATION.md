# Chromecast physical validation checklist

This checklist is intentionally open until it is executed against a real Chromecast or Google TV. Automated tests and fake transports do not satisfy any item below.

Record before testing:

- date, tester, control-TV commit, operating system and network;
- device UUID, model, firmware and friendly name;
- media URL/content type used, excluding credentials or private tokens;
- configured discovery, connection, request, recovery and confirmation timeouts.

## Discovery, identity and status

- [ ] Start from a closed transport and discover the physical device within the configured timeout.
- [ ] Record the discovered UUID and select the device by UUID, not friendly name.
- [ ] Rename the device, rediscover it and verify that the UUID remains the selection key.
- [ ] Read receiver and media status and compare every reported field with the device UI; record fields the receiver does not report as unknown, never inferred.
- [ ] Repeat discovery and verify that superseded connections/socket workers terminate.
- [ ] Call `close()` twice and verify no active project-owned Cast connection or worker remains.

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
