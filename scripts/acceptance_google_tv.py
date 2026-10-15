"""Google TV acceptance contracts and C6/C7 evidence validation.

The catalog mirrors docs/DUAL_CONTROLLER_VALIDATION.md. It prepares real execution but never
sends a command; product behavior remains exclusively in the shared Python control service.
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from acceptance import Context, Contract, Outcome, Result, Scenario, make_result


@dataclass(frozen=True)
class TraceEvent:
    sequence: int
    client: str
    event: str
    request_id: str
    media_session_id: int | None = None


def blocked_real(context: Context, scenario: Scenario) -> Result:
    device = context.environment.devices.get("google_tv")
    reason = (
        "owner authorization is required"
        if device
        else "CONTROL_TV_GOOGLE_TV is not configured and owner authorization is required"
    )
    return make_result(
        scenario,
        Outcome.BLOCKED,
        reason,
        started=time.monotonic(),
        log="No discovery, pairing, ADB operation or TV command was attempted.",
        observed="scenario not executed",
        proof="authorization gate and empty command log",
    )


def validate_c6(events: Sequence[TraceEvent], request_id: str) -> tuple[bool, str]:
    relevant = sorted(
        (event for event in events if event.request_id == request_id), key=lambda e: e.sequence
    )
    names = [event.event for event in relevant]
    required = ["pre_status", "send_attempt", "session_changed", "confirmation_status"]
    if any(name not in names for name in required):
        return (
            False,
            "C6 evidence incomplete: ordered pre-read, send, switch and confirmation required",
        )
    positions = [names.index(name) for name in required]
    if positions != sorted(positions):
        return False, "C6 evidence is not in the required order"
    by_name = {event.event: event for event in relevant}
    old = by_name["pre_status"].media_session_id
    sent = by_name["send_attempt"].media_session_id
    new = by_name["session_changed"].media_session_id
    confirmed = by_name["confirmation_status"].media_session_id
    if old is None or new is None or old == new:
        return False, "C6 needs distinct, non-null old and new mediaSessionId values"
    if sent != old:
        return False, "send attempt was not bound to the captured mediaSessionId"
    if confirmed == new and any(event.event == "command_confirmed" for event in relevant):
        return False, "command was retargeted or confirmed from the replacement session"
    return True, "C6 proves the send used the captured session and did not confirm from the new one"


def validate_c7(events: Sequence[TraceEvent], request_id: str) -> tuple[bool, str]:
    relevant = [event for event in events if event.request_id == request_id]
    counts = Counter(event.event for event in relevant)
    if counts["send_attempt"] != 1:
        return (
            False,
            f"C7 requires exactly one low-level send attempt, observed {counts['send_attempt']}",
        )
    if counts["automatic_retry"] or counts["replay"]:
        return False, "C7 trace records an automatic retry or replay"
    if not counts["ambiguous_result"]:
        return False, "C7 needs the ambiguous delivery result in the trace"
    return True, "C7 proves one local send attempt and no application replay"


def contract(
    preconditions: Sequence[str],
    device: str,
    commands: Sequence[str],
    initial: str,
    expected: str,
) -> Contract:
    return Contract(tuple(preconditions), device, tuple(commands), initial, expected)
