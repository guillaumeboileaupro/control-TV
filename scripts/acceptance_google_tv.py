"""Google TV acceptance contracts and C6/C7 evidence validation.

The catalog mirrors docs/DUAL_CONTROLLER_VALIDATION.md. It prepares real execution but never
sends a command; product behavior remains exclusively in the shared Python control service.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from acceptance import Context, Contract, Outcome, Result, Scenario, make_result
from control_tv.command_trace import TraceEvent


def load_trace(path: Path) -> tuple[TraceEvent, ...]:
    """Load fail-closed JSONL evidence emitted by the product tracer."""
    events: list[TraceEvent] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            raw = json.loads(line)
            if not isinstance(raw, dict):
                raise ValueError("event is not an object")
            sequence = raw.get("sequence")
            session = raw.get("media_session_id")
            client = raw.get("client")
            event = raw.get("event")
            request_id = raw.get("request_id")
            if (
                not isinstance(sequence, int)
                or isinstance(sequence, bool)
                or not isinstance(client, str)
                or not client
                or not isinstance(event, str)
                or not event
                or not isinstance(request_id, str)
                or not request_id
                or (
                    session is not None
                    and (not isinstance(session, int) or isinstance(session, bool) or session < 0)
                )
            ):
                raise ValueError("invalid required trace fields")
            command = raw.get("command")
            error_code = raw.get("error_code")
            if command is not None and not isinstance(command, str):
                raise ValueError("invalid command")
            if error_code is not None and not isinstance(error_code, str):
                raise ValueError("invalid error code")
            events.append(
                TraceEvent(
                    sequence,
                    client,
                    event,
                    request_id,
                    session,
                    command,
                    error_code,
                )
            )
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            raise ValueError(f"invalid trace event at line {line_number}: {error}") from error
    return tuple(events)


def blocked_real(context: Context, scenario: Scenario) -> Result:
    if scenario.id in {"C6", "C7"}:
        trace_path = context.environment.artifacts.get("command_trace")
        request_id = context.environment.artifacts.get(f"{scenario.id.lower()}_request_id")
        if trace_path and request_id:
            started = time.monotonic()
            path = Path(trace_path)
            if not path.is_file():
                return make_result(
                    scenario,
                    Outcome.FAIL,
                    "configured command trace does not exist",
                    started=started,
                    proof="trace path validation",
                )
            events = load_trace(path)
            valid, reason = (
                validate_c6(events, request_id)
                if scenario.id == "C6"
                else validate_c7(events, request_id)
            )
            return make_result(
                scenario,
                Outcome.PASS if valid else Outcome.FAIL,
                reason,
                started=started,
                observed=f"{sum(event.request_id == request_id for event in events)} trace events",
                proof=f"structured command trace for request {request_id}",
            )
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
    commands = {event.command for event in relevant if event.command is not None}
    if len({event.sequence for event in relevant}) != len(relevant):
        return False, "C6 trace sequence is ambiguous"
    if len(commands) != 1:
        return False, "C6 events are not correlated to one identifiable command"
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
    command_confirmed = [event for event in relevant if event.event == "command_confirmed"]
    if command_confirmed:
        if confirmed != old:
            return False, "confirmation status does not belong to the captured mediaSessionId"
        if any(event.media_session_id != old for event in command_confirmed):
            return False, "command was confirmed on a session other than the captured one"
        confirmation_position = names.index("confirmation_status")
        if any(relevant.index(event) <= confirmation_position for event in command_confirmed):
            return False, "command confirmation precedes its confirmation status"
    return True, "C6 proves the send used the captured session and did not confirm from the new one"


def validate_c7(events: Sequence[TraceEvent], request_id: str) -> tuple[bool, str]:
    relevant = sorted(
        (event for event in events if event.request_id == request_id),
        key=lambda event: event.sequence,
    )
    counts = Counter(event.event for event in relevant)
    commands = {event.command for event in relevant if event.command is not None}
    if len({event.sequence for event in relevant}) != len(relevant):
        return False, "C7 trace sequence is ambiguous"
    if len(commands) != 1:
        return False, "C7 events are not correlated to one identifiable command"
    if counts["send_attempt"] != 1:
        return (
            False,
            f"C7 requires exactly one low-level send attempt, observed {counts['send_attempt']}",
        )
    if counts["automatic_retry"] or counts["replay"]:
        return False, "C7 trace records an automatic retry or replay"
    if counts["ambiguous_result"] != 1:
        return False, "C7 needs exactly one ambiguous delivery result in the trace"
    send_position = next(
        index for index, event in enumerate(relevant) if event.event == "send_attempt"
    )
    ambiguous_position = next(
        index for index, event in enumerate(relevant) if event.event == "ambiguous_result"
    )
    if send_position >= ambiguous_position:
        return False, "C7 ambiguous result must follow the low-level send attempt"
    return True, "C7 proves one local send attempt and no application replay"


def contract(
    preconditions: Sequence[str],
    device: str,
    commands: Sequence[str],
    initial: str,
    expected: str,
) -> Contract:
    return Contract(tuple(preconditions), device, tuple(commands), initial, expected)
