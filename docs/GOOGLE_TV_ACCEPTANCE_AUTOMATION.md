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

Even when flags are supplied, this iteration's real adapter remains blocked. Execution is enabled
only after a separately reviewed adapter can identify both builds, the selected TV and the private
evidence destination, and after the owner authorizes the individual step.

## C6 instrumentation

The product trace, the confirmation hold and the independent observer are described in
[COMMAND_TRACE.md](COMMAND_TRACE.md). Validation is offline and needs no flag:
`python3 scripts/validate_command_trace.py c6 --request-id <id> REMOTE_TRACE OBSERVER_TRACE`.

C6 passes only when, for one request id:

- the remote's own stream (one client, `ubuntu` or `android`) is complete: contiguous indexes,
  no `trace_incomplete`, exactly one final outcome, one command;
- it shows, in this order, `pre_status` (the captured old `mediaSessionId`), `send_attempt` bound
  to it, `transport_response`, `confirmation_held`, then `confirmation_released` carrying the
  observer's token, and no `confirmation_status` before that release;
- the **observer's** stream (role `observer`, its own process and trace) shows
  `observer_hold_seen` still on the old session (otherwise the switch may precede the send),
  then `independent_session_change` to a different session, then `observer_release_sent` with
  the same token;
- no `command_confirmed` belongs to another session.

The product's own `confirmation_session_changed` is never accepted as the proof. Order across the
two streams comes only from the token handshake, never from the two devices' clocks. A hold
that timed out, failed or was released without the observer's token fails C6.

## C7 instrumentation

For one request id, C7 requires a complete remote stream with exactly one `send_attempt` and
`ambiguous_result` as its final outcome; a `transport_response`, a second send attempt,
`automatic_retry` or `replay` fails. This proves one **application-side** invocation and no
application replay; it does not prove how many packets left the device or reached the TV, which
needs packet or receiver-side capture.

The validators are deterministic and covered with positive, mutation-style negative and
product-to-validator tests, including an end-to-end simulation (remote, file hold and observer in
two traces). A simulated PASS is never a hardware result.
