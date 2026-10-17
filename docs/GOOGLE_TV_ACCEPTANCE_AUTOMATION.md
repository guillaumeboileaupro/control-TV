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

**There is no automated real-TV adapter.** Even when every flag is supplied, each scenario reports
`BLOCKED` ("no automated adapter exists … run it by hand"), and it is run by hand per
[MANUAL_ACCEPTANCE_PROTOCOL.md](MANUAL_ACCEPTANCE_PROTOCOL.md), after the owner authorizes that
individual step. An automated adapter would first have to be written and reviewed, and be able to
identify both builds, the selected TV and the private evidence destination.

## C6 instrumentation

The trace schema is `TraceEvent(sequence, client, event, request_id, media_session_id)`. C6 requires,
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

The validators are deterministic and covered with positive and mutation-style negative tests. They
record application-side emission evidence only and do not add logging to the authoritative product
control path.
