"""Deterministic K1-K3 latency measurements, independent from device transport.

`key-step` classifies a filmed K1 or K2 step (docs/DUAL_CONTROLLER_VALIDATION.md, "Key latency
steps") from the frame numbers read on the video, so the owner records what the film shows and
the arithmetic and the PASS/FAIL/NOT RUN rules are applied the same way every time:

    python3 scripts/acceptance_latency.py key-step --fps 60 RECORD.json \
        [--effect-median S --effect-slowest S --ready-median S]

RECORD.json holds {"presses": [{...}, ...]}, one object per press with the fields of
`FilmedPress`. The latency criterion is checked only when given (the owner adopts it); otherwise
the delays are reported as "measured, no criterion adopted". Exit status: 0 PASS, 1 FAIL,
2 NOT RUN. Nothing here touches a device.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from enum import StrEnum
from pathlib import Path


@dataclass(frozen=True)
class LatencySample:
    trigger: float
    sent: float
    transport_response: float
    state_confirmation: float
    finished: float
    success: bool

    def phases(self) -> Mapping[str, float]:
        points = (
            self.trigger,
            self.sent,
            self.transport_response,
            self.state_confirmation,
            self.finished,
        )
        if any(not math.isfinite(point) for point in points) or list(points) != sorted(points):
            raise ValueError("latency timestamps must be finite and monotonic")
        return {
            "trigger_to_send": self.sent - self.trigger,
            "send_to_transport_response": self.transport_response - self.sent,
            "transport_response_to_confirmation": self.state_confirmation - self.transport_response,
            "confirmation_to_finished": self.finished - self.state_confirmation,
            "total": self.finished - self.trigger,
        }


@dataclass(frozen=True)
class Distribution:
    count: int
    minimum: float
    median: float
    p90: float
    p95: float
    p99: float
    maximum: float


def percentile(values: Sequence[float], percentage: float) -> float:
    """Linear interpolation, equivalent to the inclusive quantile definition."""
    if not values:
        raise ValueError("at least one latency sample is required")
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentage / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def distribution(values: Sequence[float]) -> Distribution:
    if not values:
        raise ValueError("at least one latency sample is required")
    return Distribution(
        len(values),
        min(values),
        statistics.median(values),
        percentile(values, 90),
        percentile(values, 95),
        percentile(values, 99),
        max(values),
    )


def latency_metrics(samples: Sequence[LatencySample]) -> Mapping[str, object]:
    if not samples:
        raise ValueError("at least one latency sample is required")
    phases = [sample.phases() for sample in samples]
    names = tuple(phases[0])
    return {
        "unit": "seconds",
        "threshold": None,
        "failure_rate": sum(not sample.success for sample in samples) / len(samples),
        "phases": {name: asdict(distribution([phase[name] for phase in phases])) for name in names},
    }


def k3_counts(pressed: int, sent: int, refused: int, focus_moves: int) -> Mapping[str, object]:
    if min(pressed, sent, refused, focus_moves) < 0 or sent + refused != pressed:
        raise ValueError("K3 counts are inconsistent")
    return {
        "pressed": pressed,
        "sent": sent,
        "refused": refused,
        "focus_moves": focus_moves,
        "functional_pass": focus_moves == sent,
        "threshold": None,
    }


# K1 and K2: one filmed key step ----------------------------------------------------------------

NO_CRITERION = "measured, no criterion adopted"


class Verdict(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_RUN = "NOT RUN"


@dataclass(frozen=True)
class FilmedPress:
    """One press of a K1/K2 step as the video shows it; frames are frame numbers of the film."""

    made_while_idle: bool  # the remote was idle when the key was pressed
    sent: bool  # the key showed its loading indicator
    focus_moves: int  # how many times the TV focus moved after this press
    outcome_shown: bool  # the remote showed an outcome for this press
    may_have_reached: bool = False  # that outcome: "may or may not have reached the TV"
    send_frame: int | None = None  # first frame with the loading indicator
    effect_frame: int | None = None  # first frame where the focus has moved
    ready_frame: int | None = None  # first frame where the remote is idle again


@dataclass(frozen=True)
class LatencyCriterion:
    """A criterion the owner adopted, in seconds (none is adopted by default)."""

    effect_median: float
    effect_slowest: float
    ready_median: float

    def __post_init__(self) -> None:
        values = (self.effect_median, self.effect_slowest, self.ready_median)
        if any(not math.isfinite(value) or value <= 0 for value in values):
            raise ValueError("a latency criterion is finite and positive seconds")


@dataclass(frozen=True)
class KeyStepResult:
    verdict: Verdict
    reasons: tuple[str, ...]
    send_to_effect: Distribution | None  # seconds, over the sent presses that moved the focus
    send_to_ready: Distribution | None  # seconds, over the sent presses
    latency: str  # NO_CRITERION, or whether the delays are within the adopted criterion


def _frame(value: int | None, name: str, number: int) -> int | None:
    if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
        raise ValueError(f"press {number}: {name} must be a frame number (0 or more)")
    return value


def evaluate_key_step(
    presses: Sequence[FilmedPress], *, fps: float, criterion: LatencyCriterion | None = None
) -> KeyStepResult:
    """Classify a K1/K2 step by the protocol's rules: FAIL when the video shows the contract
    broken; NOT RUN when it cannot show the outcome (missing frames, nothing recorded, or a
    press that may or may not have reached the TV without moving the focus); PASS otherwise,
    and then FAIL as well when an adopted latency criterion is exceeded."""
    if isinstance(fps, bool) or not isinstance(fps, int | float) or not math.isfinite(fps):
        raise ValueError("the video frame rate must be a finite number of frames per second")
    if fps <= 0:
        raise ValueError("the video frame rate must be positive")
    failures: list[str] = []
    missing: list[str] = []
    effect: list[float] = []
    ready: list[float] = []
    for number, press in enumerate(presses, 1):
        if isinstance(press.focus_moves, bool) or press.focus_moves < 0:
            raise ValueError(f"press {number}: focus_moves must be 0 or more")
        send = _frame(press.send_frame, "send_frame", number)
        moved = _frame(press.effect_frame, "effect_frame", number)
        idle = _frame(press.ready_frame, "ready_frame", number)
        if press.made_while_idle and not press.sent:
            failures.append(f"press {number} was made while the remote was idle but not sent")
        if not press.sent and press.focus_moves:
            failures.append(f"press {number} moved the focus although it was not sent")
        if press.focus_moves > 1:
            failures.append(
                f"press {number} moved the focus {press.focus_moves} times: a key sent twice"
            )
        if not press.outcome_shown:
            failures.append(f"press {number}: the remote showed no outcome")
        if not press.sent:
            continue
        if press.focus_moves == 0:
            if press.may_have_reached:
                missing.append(
                    f"press {number} may or may not have reached the TV and the focus did not "
                    "move: repeat the step"
                )
            else:
                failures.append(
                    f"press {number} was sent but the focus never moved: navigation does not work"
                )
        if send is None or idle is None:
            missing.append(f"press {number}: the video does not show its loading indicator")
            continue
        if idle < send:
            raise ValueError(f"press {number}: ready_frame comes before send_frame")
        ready.append((idle - send) / fps)
        if press.focus_moves == 1:
            if moved is None:
                missing.append(f"press {number}: the video does not show the focus moving")
            elif moved < send:
                raise ValueError(f"press {number}: effect_frame comes before send_frame")
            else:
                effect.append((moved - send) / fps)
    if not presses:
        missing.append("no press was recorded")
    send_to_effect = distribution(effect) if effect else None
    send_to_ready = distribution(ready) if ready else None
    latency = NO_CRITERION
    if criterion is not None and send_to_effect is not None and send_to_ready is not None:
        exceeded = [
            label
            for label, value, limit in (
                ("send-to-effect median", send_to_effect.median, criterion.effect_median),
                ("slowest send-to-effect", send_to_effect.maximum, criterion.effect_slowest),
                ("send-to-ready median", send_to_ready.median, criterion.ready_median),
            )
            if value > limit
        ]
        latency = (
            "outside the adopted criterion: " + ", ".join(exceeded)
            if exceeded
            else "within the adopted criterion"
        )
        if exceeded and not failures and not missing:
            failures.append(latency)
    if failures:
        verdict = Verdict.FAIL
    elif missing:
        verdict = Verdict.NOT_RUN
    else:
        verdict = Verdict.PASS
    return KeyStepResult(
        verdict, tuple(failures or missing), send_to_effect, send_to_ready, latency
    )


_EXIT = {Verdict.PASS: 0, Verdict.FAIL: 1, Verdict.NOT_RUN: 2}
_FIELDS = {field.name for field in fields(FilmedPress)}


def read_presses(path: Path) -> list[FilmedPress]:
    """The presses of a RECORD.json file; an unknown or missing field is refused."""
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("presses"), list):
        raise ValueError('the record must be an object with a "presses" list')
    presses: list[FilmedPress] = []
    for number, raw in enumerate(document["presses"], 1):
        if not isinstance(raw, dict) or not set(raw) <= _FIELDS:
            raise ValueError(f"press {number}: only the FilmedPress fields are accepted")
        for name in ("made_while_idle", "sent", "outcome_shown", "may_have_reached"):
            if name in raw and not isinstance(raw[name], bool):
                raise ValueError(f"press {number}: {name} must be true or false")
        try:
            presses.append(FilmedPress(**raw))
        except TypeError as error:
            raise ValueError(f"press {number}: {error}") from None
    return presses


def _seconds(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f} s"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    commands = parser.add_subparsers(dest="command", required=True)
    step = commands.add_parser("key-step", help="classify a filmed K1/K2 step")
    step.add_argument("record", type=Path)
    step.add_argument("--fps", type=float, required=True)
    for flag in ("--effect-median", "--effect-slowest", "--ready-median"):
        step.add_argument(flag, type=float)
    arguments = parser.parse_args(argv)
    limits = (arguments.effect_median, arguments.effect_slowest, arguments.ready_median)
    if any(limit is not None for limit in limits) and None in limits:
        parser.error("an adopted criterion needs all three limits")
    try:
        criterion = None if None in limits else LatencyCriterion(*limits)
        result = evaluate_key_step(
            read_presses(arguments.record), fps=arguments.fps, criterion=criterion
        )
    except (OSError, ValueError) as error:
        print(f"NOT RUN: unusable record: {error}")
        return _EXIT[Verdict.NOT_RUN]
    print(f"{result.verdict.value}: " + ("; ".join(result.reasons) or "every rule holds"))
    for label, measured in (
        ("send to effect", result.send_to_effect),
        ("send to ready", result.send_to_ready),
    ):
        median = None if measured is None else measured.median
        slowest = None if measured is None else measured.maximum
        print(f"{label}: median {_seconds(median)}, slowest {_seconds(slowest)}")
    print(f"latency: {result.latency}")
    return _EXIT[result.verdict]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
