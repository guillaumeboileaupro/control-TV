# Command trace for the C6/C7 acceptance steps

An opt-in, local, structured trace of what the product does for each command. It exists to
validate the two-remote steps C6 and C7 of
[DUAL_CONTROLLER_VALIDATION.md](DUAL_CONTROLLER_VALIDATION.md). It is **off by default**,
opens no network service, and never changes, suppresses or repeats a command.

## What it proves, and what it does not

| Level                                                                                                            | Proven by the trace?             |
| ---------------------------------------------------------------------------------------------------------------- | -------------------------------- |
| Application-side invocation: the product entered the transport once for a request, against a given media session | **yes**                          |
| Network transmission (packets that left the device)                                                              | no — needs a packet capture      |
| Reception by the TV                                                                                              | no — needs receiver-side capture |
| Physical effect on the TV                                                                                        | no — the owner's observation     |

A receiver confirmation (`command_confirmed`) is what the receiver **reported**, not a physical
effect. A simulated PASS (the tests) is never a hardware result.

## Events

Every event has `sequence` (local to one process), `client` (`ubuntu`, `android` or `observer`),
`event`, `request_id` (opaque, one per command invocation), `index` (contiguous per request,
from 0), `application_request_id` (the order the command belongs to: the host's `requestId` -
one per window command on Ubuntu and in the Android app, made once per order by the Tauri
shell; the tap itself for an Android widget command - reused if the same order is issued again,
or one per bridge message without it) and, when relevant, `media_session_id`, `command`, `error_code` and the hold `token`.
Never a device id or address, a media URL or title, a command argument, a credential or
exception text.

| Phase                                    | Events (remote)                                                                                                                                                                                   |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Preparation                              | `command_prepared`                                                                                                                                                                                |
| TV identity and media session (playback) | `pre_status` (the captured session)                                                                                                                                                               |
| Refusal before any send                  | `command_failed` + `not_sent`                                                                                                                                                                     |
| Send                                     | `send_attempt` (exactly one, never retried)                                                                                                                                                       |
| Send result                              | `transport_response`, or `command_failed` + `ambiguous_result` / `command_rejected` / `not_sent`; an unexpected exception adds `unexpected_error` + `ambiguous_result` and is re-raised unchanged |
| Hold (acceptance only)                   | `confirmation_held`, then `confirmation_released` (with the observer's token), `confirmation_hold_timeout` or `confirmation_hold_failed`                                                          |
| Confirmation reads                       | `confirmation_status` per read, `confirmation_read_failed` per expected read failure, `confirmation_unexpected_error` before an unexpected exception propagates, `confirmation_session_changed` when the product itself sees another session (never C6 evidence) |
| Outcome (exactly one)                    | `command_confirmed`, `command_unconfirmed`, `confirmation_skipped`, `ambiguous_result`, `command_rejected` or `not_sent`                                                                          |
| Lost evidence                            | `trace_incomplete`                                                                                                                                                                                |

The observer writes `observer_hold_seen`, `observer_session_already_changed`,
`observer_read_failed`, `independent_session_change`, `observer_release_sent` and
`observer_timeout`.

## Integrity guarantees

- A lost write leaves a gap in `index` or no final outcome: the validators reject that request
  (fail-closed).
- After a lost write the recorder writes `trace_incomplete` before the request's next event, and
  the file sink leaves `<file>.incomplete` beside the trace, which makes the loader refuse the
  whole file. Both are **best effort**: a full disk can lose them too. The gap is the guarantee;
  a truncated file whose lost part held whole requests only lacks those requests, which then
  cannot be validated at all.
- `sequence` is never compared across clients. Across the remote and the observer, order comes
  only from the hold token: the observer can only issue it after seeing the hold (written after
  the send), and the remote only reads the confirmation after receiving it.

## Turning it on

### Ubuntu (desktop bridge)

Start the application from a terminal with:

```bash
export CONTROL_TV_COMMAND_TRACE="$PWD/.ai-private/hardware/c6/ubuntu.jsonl"
export CONTROL_TV_TRACE_CLIENT=ubuntu          # required: ubuntu, android or observer
export CONTROL_TV_CONFIRMATION_GATE="$PWD/.ai-private/hardware/c6/gate"   # C6 only
export CONTROL_TV_CONFIRMATION_GATE_SECONDS=30  # optional, at most 120
npx --prefix ui tauri dev
```

The directories are created 0700 (an existing directory that others can read is refused) and the
files 0600, never through a symbolic link: the existing directories above the evidence directory
are the caller's and may be links (Android's `filesDir` is under the `/data/user/0` link), but the
evidence directory, every directory created for it and every file are opened relative to an open
descriptor without following links. A trace without a valid role is refused at startup.
**The hold is refused in the packaged release bridge**: run C6 from the development mode; the
installed `.deb` can still record a trace (for C7) without a hold.

### Android (debug build only)

The debug APK has a screen **control-TV Recette**, without a home-screen icon (only one control-TV
icon on the phone); open it from the computer with
`adb shell am start -n io.github.guillaumeboileaupro.controltv/.AcceptanceTraceActivity`:

1. turn on "Mode recette", then "Fermer l'application (pour appliquer)" and reopen control-TV;
2. the screen shows "Trace active dans ce processus : oui" and the last requests (id, client,
   command, outcome);
3. run the authorized step;
4. "Exporter la trace…" shares `files/acceptance/command-trace.jsonl` through the Android share
   sheet (only on that tap; nothing is exported automatically, nothing is in shared storage);
5. turn the mode off and close the app; "Supprimer les preuves…" deletes every acceptance file.

The release APK has no such screen, provider or code path. The Android hold works the same way
under `files/acceptance/gate`; the observer must then reach that directory, so C6 is run from
Ubuntu, and Android is used for C7.

## C6, step by step (owner authorization for each step)

1. Media with a reported content and session identity plays on the TV.
2. In a second terminal, start the observer (read-only):

   ```bash
   CONTROL_TV_OBSERVED_DEVICE=<stable device id> python3 scripts/acceptance_observer.py \
     --gate .ai-private/hardware/c6/gate --trace .ai-private/hardware/c6/observer.jsonl \
     [--request-id <id>]
   ```

   With `--request-id` it observes that held request only. Without it, it observes the single
   held command and refuses to choose when several are held (another remote's hold).

3. Press Pause on Ubuntu: the command is sent once, then its confirmation is held.
4. When the observer prints "hold seen on the captured session: start the other video now", start
   another video from the sender app. The observer sees the new session, records it and releases
   the hold; Ubuntu then reads its confirmation.
5. Validate offline (no flag, no TV):

   ```bash
   python3 scripts/validate_command_trace.py list .ai-private/hardware/c6/*.jsonl
   python3 scripts/validate_command_trace.py c6 --request-id <id> \
     .ai-private/hardware/c6/ubuntu.jsonl .ai-private/hardware/c6/observer.jsonl
   ```

If the session had changed before the hold was seen, the observer never releases and C6 fails:
the switch may have preceded the send (the known gap of PR #38 review P3-3).

## C7

Record a trace on the remote (no hold needed), press one command and cut Wi-Fi immediately (owner
agreement), then `python3 scripts/validate_command_trace.py c7 --request-id <id> TRACE`.

The validator exits 0 **PASS**, 1 **FAIL** (complete evidence demonstrates a violation) or 2
**BLOCKED** (no usable evidence: a trace absent, empty, unreadable or incomplete, an invalid or
unknown request id, no observer trace for C6, a C7 trace with no application request). BLOCKED is
recorded NOT RUN, never as a hardware failure. Every
`send_attempt` of the same `application_request_id` counts, whichever invocation made it: a
request sent again by another invocation fails C7, and a trace without an application request
id never passes. A PASS proves one application-side invocation, an ambiguous outcome and no
application replay.

## Keeping and deleting the evidence

Traces hold no personal or device data, but they are acceptance evidence: keep them under
`.ai-private/hardware/` (git-ignored) only as long as the campaign report needs them, then delete
the directory (`rm -r .ai-private/hardware/c6`); on Android, use "Supprimer les preuves…". Never
commit a trace.
