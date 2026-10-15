"""Deterministic K1-K3 latency measurements, independent from device transport."""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass


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
