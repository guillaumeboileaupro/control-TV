# Google TV acceptance automation

The Google TV catalog is a structured transcription of
`docs/DUAL_CONTROLLER_VALIDATION.md`. R1-R4 and C1-C7 keep those identifiers and semantics; the
automation does not define substitutes. Additional `TV-*` entries cover power, navigation and
K1-K3, remote keyboard, applications and controlled error handling.

Run the preparation report safely with:

```bash
python3 scripts/run_acceptance.py --category google-tv
```

Every scenario is `real` evidence and remains `BLOCKED` by default. Each report contains its
identifier, preconditions, device roles, planned commands, required initial state, expected result,
observed result, duration, status and proof reference. Device names, addresses, identifiers and
media identifiers belong only in the ignored private evidence directory.

## Authorization boundaries

R1-R4 and error reads require explicit execution authorization. C1-C7, power, navigation and
keyboard also require TV-command authorization. Application launch additionally requires ADB to
already have been enabled and authorized by the owner. The runner never pairs the remote channel,
enables Android developer options, starts ADB, changes Wi-Fi or sends a command by itself.

The runner never initiates a C6 or C7 command. After an individually authorized hardware step, it
can validate the product trace when `CONTROL_TV_COMMAND_TRACE` and the matching
`CONTROL_TV_C6_REQUEST_ID` or `CONTROL_TV_C7_REQUEST_ID` are configured. Missing evidence remains
`BLOCKED`; malformed or contradictory evidence is `FAIL`, never `PASS`.

## Enabling the product trace

Tracing is off by default. Before launching the Ubuntu or Android bridge for an authorized C6/C7
step, set `CONTROL_TV_COMMAND_TRACE` to a local JSONL file and set `CONTROL_TV_TRACE_CLIENT` to the
client role (`ubuntu` or `android`). The bridge appends a mode-0600 record for each phase. It never
records a device id or address, media URL/title, command argument, credential or exception text.
Each command gets an opaque request id; use that exact id for acceptance validation.

The command path records `command_prepared`, the playback `pre_status`, exactly one
`send_attempt`, the transport response or failure, each fresh `confirmation_status`, and the final
confirmation outcome. A changed session observed during confirmation adds `session_changed` before
that status. A failure after transport entry adds `ambiguous_result`; it does not trigger a retry.
Tracing is evidence only: a trace-write failure is ignored and cannot change, suppress or repeat a
command.

Example validation, only after the owner separately authorized and performed the relevant step:

```bash
export CONTROL_TV_COMMAND_TRACE="$PWD/.ai-private/hardware/c6-command-trace.jsonl"
export CONTROL_TV_C6_REQUEST_ID="<opaque request_id from that trace>"
python3 scripts/run_acceptance.py --category google-tv --execute --allow-tv-commands
```

## C6 instrumentation

The trace schema adds optional `command` and stable `error_code` fields to
`TraceEvent(sequence, client, event, request_id, media_session_id)`. C6 requires,
for one request id and in strict order:

1. `pre_status` with the captured old `mediaSessionId`;
2. `send_attempt` carrying that exact old identifier;
3. `session_changed` from an independent read-only receiver trace with a distinct new identifier;
4. `confirmation_status` after the switch.

A `command_confirmed` event tied to the replacement session fails the evidence. Missing identity,
equal old/new identifiers, an unbound send or unordered events cannot pass. Human timing is never
accepted as proof.

## C7 instrumentation

For one request id, C7 requires exactly one event at entry to the low-level `send_attempt`, followed
by an `ambiguous_result`. Any second send attempt, `automatic_retry` or `replay` fails. Final TV state
does not prove delivery count; without the instrumented trace, C7 remains blocked. Packet or
receiver-side capture is still required to claim how many deliveries the TV itself received.

The validators are deterministic and covered with positive, mutation-style negative and
product-to-validator integration tests. They establish application-side invocation count and
session binding. They do not prove packet delivery, physical TV behavior or hardware compatibility.
