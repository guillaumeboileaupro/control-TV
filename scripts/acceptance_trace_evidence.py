"""Offline, fail-closed validation of C6/C7 command-trace evidence (docs/COMMAND_TRACE.md).

What a PASS means: the product's **application-side** trace for one request is complete and
shows what the step requires. It never means a network transmission, a reception by the TV or a
physical effect. Order across clients is never taken from their clocks: within one client's
stream the per-request `index` orders events, and across clients only the confirmation-hold
token links the observer's proof to the remote's released confirmation.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from enum import StrEnum
from pathlib import Path

from control_tv.command_trace import (
    TERMINAL_EVENTS,
    TRACE_INCOMPLETE,
    TraceEvent,
    TraceRole,
    is_application_request_id,
)
from control_tv.trace_files import INCOMPLETE_SUFFIX

REMOTES = frozenset({TraceRole.UBUNTU.value, TraceRole.ANDROID.value})
OBSERVER = TraceRole.OBSERVER.value
_TOKEN = re.compile(r"[0-9a-f]{32}")

Verdict = tuple[bool, str]


class Classification(StrEnum):
    """PASS; FAIL: a violation demonstrated by complete evidence; BLOCKED: no usable evidence
    (absent, incomplete or uncorrelated), which is never a hardware failure."""

    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"


Classified = tuple[Classification, str]


def _optional_integer(raw: dict[str, object], key: str) -> int | None:
    value = raw.get(key)
    return None if value is None else _integer(value, key)


def _integer(value: object, key: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"invalid {key}")
    return value


def _optional_text(raw: dict[str, object], key: str) -> str | None:
    value = raw.get(key)
    return None if value is None else _text(value, key)


def _text(value: object, key: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"invalid {key}")
    return value


def load_trace(path: Path) -> tuple[TraceEvent, ...]:
    """The events of one JSONL trace file; any malformed line, or an `.incomplete` marker left
    by a failed write, makes the whole file unusable (`ValueError`)."""
    if path.with_name(path.name + INCOMPLETE_SUFFIX).exists():
        raise ValueError(f"{path.name} is marked incomplete: an event could not be written")
    events: list[TraceEvent] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            raw = json.loads(line)
            if not isinstance(raw, dict):
                raise ValueError("event is not an object")
            token = _optional_text(raw, "token")
            if token is not None and not _TOKEN.fullmatch(token):
                raise ValueError("invalid token")
            application_request_id = _optional_text(raw, "application_request_id")
            if application_request_id is not None and not is_application_request_id(
                application_request_id
            ):
                raise ValueError("invalid application request id")
            events.append(
                TraceEvent(
                    sequence=_integer(raw.get("sequence"), "sequence"),
                    client=_text(raw.get("client"), "client"),
                    event=_text(raw.get("event"), "event"),
                    request_id=_text(raw.get("request_id"), "request_id"),
                    index=_integer(raw.get("index"), "index"),
                    media_session_id=_optional_integer(raw, "media_session_id"),
                    command=_optional_text(raw, "command"),
                    error_code=_optional_text(raw, "error_code"),
                    token=token,
                    application_request_id=application_request_id,
                )
            )
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            raise ValueError(f"invalid trace event at line {line_number}: {error}") from error
    return tuple(events)


def load_traces(paths: Iterable[Path]) -> tuple[TraceEvent, ...]:
    return tuple(event for path in paths for event in load_trace(path))


def _streams(events: Sequence[TraceEvent], request_id: str) -> dict[str, list[TraceEvent]]:
    streams: dict[str, list[TraceEvent]] = defaultdict(list)
    for event in events:
        if event.request_id == request_id:
            streams[event.client].append(event)
    for stream in streams.values():
        stream.sort(key=lambda event: event.index)
    return streams


def integrity_problem(stream: Sequence[TraceEvent], *, terminal: bool) -> str | None:
    """Why one client's stream for one request is not complete evidence, or None."""
    if [event.index for event in stream] != list(range(len(stream))):
        return "events are missing or duplicated (the per-request index is not contiguous)"
    sequences = [event.sequence for event in stream]
    if sequences != sorted(set(sequences)):
        return "the local sequence contradicts the per-request index"
    if any(event.event == TRACE_INCOMPLETE for event in stream):
        return "the recorder reported a lost event (trace_incomplete)"
    if terminal:
        terminals = [event for event in stream if event.event in TERMINAL_EVENTS]
        if len(terminals) != 1 or stream[-1] is not terminals[0]:
            return "the command has no single final outcome event"
    return None


def _acting(
    streams: dict[str, list[TraceEvent]], step: str, request_id: str
) -> tuple[list[TraceEvent], str]:
    if not streams:
        raise _Blocked(f"{step}: no event for request {request_id} in these traces")
    remotes = sorted(REMOTES & streams.keys())
    if len(remotes) != 1:
        raise _Blocked(f"{step} needs the trace of exactly one remote for this request")
    stream = streams[remotes[0]]
    problem = integrity_problem(stream, terminal=True)
    if problem is not None:
        raise _Blocked(f"{step} remote trace: {problem}")
    commands = {event.command for event in stream if event.command is not None}
    if len(commands) != 1:
        raise _Blocked(f"{step} events are not correlated to one identifiable command")
    return stream, remotes[0]


class _Rejected(Exception):
    """A violation that complete evidence demonstrates (FAIL)."""


class _Blocked(_Rejected):
    """No usable evidence for the claim: absent, incomplete or uncorrelated (BLOCKED)."""


def _classified(check: Callable[[], str]) -> Classified:
    try:
        return Classification.PASS, check()
    except _Blocked as blocked:
        return Classification.BLOCKED, str(blocked)
    except _Rejected as rejected:
        return Classification.FAIL, str(rejected)


def _first(stream: Sequence[TraceEvent], name: str) -> TraceEvent:
    found = next((event for event in stream if event.event == name), None)
    if found is None:
        raise _Rejected(f"missing {name}")
    return found


def classify_c6(events: Sequence[TraceEvent], request_id: str) -> Classified:
    """C6: PASS, FAIL for a demonstrated violation, BLOCKED without usable evidence."""
    return _classified(lambda: _check_c6(events, request_id))


def validate_c6(events: Sequence[TraceEvent], request_id: str) -> Verdict:
    classification, reason = classify_c6(events, request_id)
    return classification is Classification.PASS, reason


def _check_c6(events: Sequence[TraceEvent], request_id: str) -> str:
    """C6: the send used the captured session, the independent observer proved a new session
    after the send and before the confirmation was released, and nothing confirmed from it."""
    streams = _streams(events, request_id)
    acting, _ = _acting(streams, "C6", request_id)
    observer = streams.get(OBSERVER)
    if not observer:
        raise _Blocked("C6 has no independent observer trace for this request")
    problem = integrity_problem(observer, terminal=False)
    if problem is not None:
        raise _Blocked(f"C6 observer trace: {problem}")
    if any(event.event == "observer_session_already_changed" for event in observer):
        raise _Rejected(
            "C6 session had already changed when the hold began: the switch may precede the send"
        )
    names = [event.event for event in acting]
    if {"confirmation_hold_timeout", "confirmation_hold_failed"} & set(names):
        raise _Rejected("C6 confirmation hold was not released by the observer")
    pre = _first(acting, "pre_status")
    send = _first(acting, "send_attempt")
    response = _first(acting, "transport_response")
    held = _first(acting, "confirmation_held")
    released = _first(acting, "confirmation_released")
    if not pre.index < send.index < response.index < held.index < released.index:
        raise _Rejected("C6 remote events are not in the order pre-read, send, hold, release")
    reads = [event for event in acting if event.event == "confirmation_status"]
    if not reads or any(event.index < released.index for event in reads):
        raise _Rejected("C6 confirmation was read before the observer released it")
    old = pre.media_session_id
    if old is None or send.media_session_id != old or held.media_session_id != old:
        raise _Rejected("C6 send was not bound to the captured mediaSessionId")
    seen = _first(observer, "observer_hold_seen")
    change = _first(observer, "independent_session_change")
    release = _first(observer, "observer_release_sent")
    if not seen.index < change.index < release.index:
        raise _Rejected("C6 observer events are not in the order seen, change, release")
    if seen.media_session_id != old:
        raise _Rejected(
            "C6 observer did not see the captured session after the send: the switch may "
            "precede the send"
        )
    new = change.media_session_id
    if new is None or new == old:
        raise _Rejected("C6 observer did not prove a different mediaSessionId")
    if released.token is None or release.token != released.token:
        raise _Rejected("C6 confirmation was not released by the observer's proof")
    if any(
        event.event == "command_confirmed" and event.media_session_id != old for event in acting
    ):
        raise _Rejected("C6 command was confirmed on a session other than the captured one")
    return (
        "C6: send bound to the captured session; the independent observer proved a new session "
        "after the send and before the released confirmation; nothing confirmed from it "
        "(application-side evidence only)"
    )


def classify_c7(events: Sequence[TraceEvent], request_id: str) -> Classified:
    """C7: PASS, FAIL for a demonstrated violation, BLOCKED without usable evidence."""
    return _classified(lambda: _check_c7(events, request_id))


def validate_c7(events: Sequence[TraceEvent], request_id: str) -> Verdict:
    classification, reason = classify_c7(events, request_id)
    return classification is Classification.PASS, reason


def _check_c7(events: Sequence[TraceEvent], request_id: str) -> str:
    """C7: one low-level send attempt, an ambiguous outcome, no application replay."""
    acting, _ = _acting(_streams(events, request_id), "C7", request_id)
    counts = Counter(event.event for event in acting)
    if counts["send_attempt"] != 1:
        raise _Rejected(
            f"C7 requires exactly one low-level send attempt, observed {counts['send_attempt']}"
        )
    application_request_id = _application_request(acting)
    if application_request_id is not None:
        _reject_application_replay(events, application_request_id)
    if counts["automatic_retry"] or counts["replay"]:
        raise _Rejected("C7 trace records an automatic retry or replay")
    if acting[-1].event != "ambiguous_result":
        raise _Rejected("C7 needs an ambiguous delivery result as the command's outcome")
    if counts["transport_response"]:
        raise _Rejected("C7 transport answered, so the delivery was not ambiguous")
    if application_request_id is None:
        raise _Blocked("C7 trace is not correlated to an application request (bridge requestId)")
    return "C7: one local send attempt, an ambiguous outcome and no application replay"


def _application_request(acting: Sequence[TraceEvent]) -> str | None:
    """The one application request a command stream belongs to, or None when it names none (or
    several, which no product trace does)."""
    application = {event.application_request_id for event in acting}
    if len(application) != 1:
        return None
    (application_request_id,) = application
    return application_request_id


def _reject_application_replay(events: Sequence[TraceEvent], application_request_id: str) -> None:
    """C7's 'no application replay': every send of the command's application request counts,
    whichever invocation (trace request id) made it; more than one is a demonstrated replay. A
    trace that names no application request cannot prove the absence of a replay (BLOCKED)."""
    sends = [
        event
        for event in events
        if event.application_request_id == application_request_id
        and event.client in REMOTES
        and event.event == "send_attempt"
    ]
    if len(sends) != 1:
        invocations = len({event.request_id for event in sends})
        raise _Rejected(
            f"C7 application request {application_request_id} was sent {len(sends)} times "
            f"({invocations} invocations): a replay"
        )


def summarize(events: Sequence[TraceEvent]) -> list[str]:
    """One line per (request, client): what the operator needs to pick a request id."""
    lines: list[str] = []
    keys = sorted({(event.request_id, event.client) for event in events})
    for request_id, client in keys:
        stream = _streams(events, request_id)[client]
        command = next((event.command for event in stream if event.command), "-")
        outcome = next(
            (event.event for event in reversed(stream) if event.event in TERMINAL_EVENTS), "-"
        )
        lines.append(f"{request_id} {client} {command} {len(stream)} events, outcome {outcome}")
    return lines
