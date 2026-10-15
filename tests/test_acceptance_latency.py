"""K1-K3 performance calculations with synthetic timestamps only."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "acceptance_latency.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("acceptance_latency", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


latency = load()


def sample(offset: float, *, success: bool = True) -> Any:
    return latency.LatencySample(
        offset, offset + 0.1, offset + 0.3, offset + 0.6, offset + 0.7, success
    )


def test_phase_timings_are_separate_and_total_is_not_double_counted() -> None:
    assert sample(10).phases() == {
        "trigger_to_send": pytest.approx(0.1),
        "send_to_transport_response": pytest.approx(0.2),
        "transport_response_to_confirmation": pytest.approx(0.3),
        "confirmation_to_finished": pytest.approx(0.1),
        "total": pytest.approx(0.7),
    }


def test_distribution_contains_every_requested_statistic() -> None:
    result = latency.distribution([1.0, 2.0, 3.0, 4.0, 5.0])
    assert result.minimum == 1.0
    assert result.median == 3.0
    assert result.p90 == pytest.approx(4.6)
    assert result.p95 == pytest.approx(4.8)
    assert result.p99 == pytest.approx(4.96)
    assert result.maximum == 5.0


def test_metrics_include_failure_rate_and_no_invented_threshold() -> None:
    metrics: Any = latency.latency_metrics([sample(0), sample(1, success=False)])
    assert metrics["failure_rate"] == 0.5
    assert metrics["threshold"] is None
    assert set(metrics["phases"]) == {
        "trigger_to_send",
        "send_to_transport_response",
        "transport_response_to_confirmation",
        "confirmation_to_finished",
        "total",
    }


@pytest.mark.parametrize(
    "points",
    [
        (0.0, -1.0, 2.0, 3.0, 4.0),
        (0.0, 1.0, float("nan"), 3.0, 4.0),
        (0.0, 1.0, 2.0, 3.0, float("inf")),
    ],
)
def test_invalid_timestamps_fail_closed(points: tuple[float, ...]) -> None:
    with pytest.raises(ValueError, match="finite and monotonic"):
        latency.LatencySample(*points, True).phases()


def test_empty_measurements_cannot_produce_statistics() -> None:
    with pytest.raises(ValueError, match="at least one"):
        latency.latency_metrics([])


def test_k3_counts_busy_refusals_without_latency_threshold() -> None:
    result = latency.k3_counts(5, 2, 3, 2)
    assert result == {
        "pressed": 5,
        "sent": 2,
        "refused": 3,
        "focus_moves": 2,
        "functional_pass": True,
        "threshold": None,
    }


def test_inconsistent_k3_counts_fail_closed() -> None:
    with pytest.raises(ValueError, match="inconsistent"):
        latency.k3_counts(5, 3, 1, 3)
