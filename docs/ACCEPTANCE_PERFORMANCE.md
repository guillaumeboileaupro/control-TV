# Acceptance performance measurements

K1, K2 and K3 retain the definitions in `docs/DUAL_CONTROLLER_VALIDATION.md`. K1 is ten
alternating Right/Left presses from Ubuntu, K2 the same from Android, and K3 five rapid Right
presses from Ubuntu. They are prepared as real-hardware scenarios and remain blocked until the
owner authorizes each run.

## Timing model

Instrumentation records monotonic timestamps for command trigger, effective send, transport
response, state confirmation and scenario completion. The report calculates separate durations
for trigger-to-send, send-to-transport-response, transport-response-to-confirmation,
confirmation-to-finished and total time.

For each duration it reports count, minimum, median, p90, p95, p99 and maximum. It also reports
the failure rate over all samples. Percentiles use linear interpolation. Non-finite or non-monotonic
timestamps fail closed.

No performance threshold is encoded. K1 and K2 can record a functional result and measurements,
but their latency cannot pass or fail a target until the owner adopts one. Every metrics object
therefore carries `"threshold": null`.

K3 records presses, sends, refusals and focus moves. Its functional invariant is that focus moves
equal sent presses and that sent plus refused equals pressed. It does not infer sends from the final
focus position.

## Reports

The JSON and HTML reports include Git SHA, total execution time, application/device versions,
failures, non-executed scenarios and the full scenario table. JUnit distinguishes the evidence
level in each classname. `execution_origin` records `ci` or `local`, independently from evidence
level (`static`, `simulated`, or `real`): a simulation executed in CI remains simulation.

Optional version metadata is supplied without contacting a device:

```bash
CONTROL_TV_UBUNTU_VERSION=0.1.0 \
CONTROL_TV_ANDROID_VERSION=0.1.0 \
CONTROL_TV_DEVICE_GOOGLE_TV_VERSION='firmware label' \
python3 scripts/run_acceptance.py --category google-tv
```

No version value changes authorization. Device values containing private identifiers must stay in
the ignored report/evidence area and must not be committed.
