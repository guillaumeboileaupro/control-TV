"""K1-K3 performance calculations with synthetic timestamps only."""

from __future__ import annotations

import importlib.util
import json
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


# K1/K2: a filmed key step ---------------------------------------------------------------------


def press(**changes: Any) -> Any:
    """A sent press made while idle that moved the focus once: 18 frames to the effect and
    30 to idle again (0.3 s and 0.5 s at 60 frames per second)."""
    values: dict[str, Any] = {
        "made_while_idle": True,
        "sent": True,
        "focus_moves": 1,
        "outcome_shown": True,
        "send_frame": 100,
        "effect_frame": 118,
        "ready_frame": 130,
    }
    values.update(changes)
    return latency.FilmedPress(**values)


def ten_presses() -> list[Any]:
    return [press(effect_frame=112 + index, ready_frame=124 + index) for index in range(10)]


def test_ten_alternating_presses_pass_with_their_delays_and_no_adopted_criterion() -> None:
    result = latency.evaluate_key_step(ten_presses(), fps=60)

    assert result.verdict is latency.Verdict.PASS and result.reasons == ()
    assert result.send_to_effect.median == pytest.approx(16.5 / 60)
    assert result.send_to_effect.maximum == pytest.approx(21 / 60)
    assert result.send_to_ready.median == pytest.approx(28.5 / 60)
    assert result.send_to_ready.count == 10
    assert result.latency == "measured, no criterion adopted"


def test_a_press_refused_while_busy_is_not_a_failure() -> None:
    refused = press(made_while_idle=False, sent=False, focus_moves=0, send_frame=None)
    result = latency.evaluate_key_step([press(), refused], fps=60)
    assert result.verdict is latency.Verdict.PASS and result.send_to_ready.count == 1


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"sent": False, "focus_moves": 0, "send_frame": None}, "made while the remote was idle"),
        (
            {"made_while_idle": False, "sent": False, "send_frame": None},
            "moved the focus although it was not sent",
        ),
        ({"focus_moves": 2}, "moved the focus 2 times: a key sent twice"),
        ({"focus_moves": 0, "effect_frame": None}, "the focus never moved"),
        ({"outcome_shown": False}, "showed no outcome"),
    ],
    ids=["idle-not-sent", "move-without-send", "sent-twice", "no-effect", "no-outcome"],
)
def test_every_broken_rule_fails_the_step(changes: dict[str, Any], reason: str) -> None:
    result = latency.evaluate_key_step([press(), press(**changes)], fps=60)
    assert result.verdict is latency.Verdict.FAIL
    assert any(reason in line and line.startswith("press 2") for line in result.reasons)


@pytest.mark.parametrize(
    ("presses", "reason"),
    [
        ([], "no press was recorded"),
        ([press(send_frame=None)], "does not show its loading indicator"),
        ([press(ready_frame=None)], "does not show its loading indicator"),
        ([press(effect_frame=None)], "does not show the focus moving"),
        (
            [press(focus_moves=0, effect_frame=None, may_have_reached=True)],
            "may or may not have reached the TV",
        ),
    ],
    ids=["empty", "no-send-frame", "no-ready-frame", "no-effect-frame", "may-have-reached"],
)
def test_what_the_video_cannot_show_is_not_run(presses: list[Any], reason: str) -> None:
    result = latency.evaluate_key_step(presses, fps=60)
    assert result.verdict is latency.Verdict.NOT_RUN
    assert any(reason in line for line in result.reasons)


def test_a_demonstrated_failure_outranks_missing_evidence() -> None:
    result = latency.evaluate_key_step([press(send_frame=None), press(focus_moves=2)], fps=60)
    assert result.verdict is latency.Verdict.FAIL and len(result.reasons) == 1


def test_an_adopted_criterion_is_checked_only_when_given() -> None:
    proposed = latency.LatencyCriterion(effect_median=0.5, effect_slowest=1.0, ready_median=1.0)
    within = latency.evaluate_key_step(ten_presses(), fps=60, criterion=proposed)
    assert within.verdict is latency.Verdict.PASS
    assert within.latency == "within the adopted criterion"

    slow = [*ten_presses(), press(effect_frame=190, ready_frame=200)]  # 1.5 s to the effect
    outside = latency.evaluate_key_step(slow, fps=60, criterion=proposed)
    assert outside.verdict is latency.Verdict.FAIL
    assert outside.reasons == ("outside the adopted criterion: slowest send-to-effect",)

    unmeasured = latency.evaluate_key_step([], fps=60, criterion=proposed)
    assert unmeasured.latency == "measured, no criterion adopted"


@pytest.mark.parametrize(
    ("presses", "fps", "message"),
    [
        ([press()], 0, "positive"),
        ([press()], float("nan"), "finite"),
        ([press()], True, "finite"),
        ([press(ready_frame=90)], 60, "ready_frame comes before send_frame"),
        ([press(effect_frame=90)], 60, "effect_frame comes before send_frame"),
        ([press(send_frame=-1)], 60, "frame number"),
        ([press(effect_frame=True)], 60, "frame number"),
        ([press(focus_moves=-1)], 60, "focus_moves"),
    ],
)
def test_an_inconsistent_record_is_refused_not_guessed(
    presses: list[Any], fps: float, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        latency.evaluate_key_step(presses, fps=fps)


@pytest.mark.parametrize("value", [0.0, -1.0, float("inf")])
def test_a_criterion_is_finite_and_positive(value: float) -> None:
    with pytest.raises(ValueError, match="finite and positive"):
        latency.LatencyCriterion(effect_median=value, effect_slowest=1.0, ready_median=1.0)


def record(tmp_path: Path, presses: object, name: str) -> Path:
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps({"presses": presses}), encoding="utf-8")
    return path


def as_json(filmed: Any) -> dict[str, Any]:
    return {name: getattr(filmed, name) for name in latency.FilmedPress.__dataclass_fields__}


def test_the_key_step_command_reports_and_exits_with_the_verdict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    passed = record(tmp_path, [as_json(press) for press in ten_presses()], "passed")
    assert latency.main(["key-step", "--fps", "60", str(passed)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "PASS: every rule holds"
    assert out[1] == "send to effect: median 0.275 s, slowest 0.350 s"
    assert out[3] == "latency: measured, no criterion adopted"

    failed = record(tmp_path, [as_json(press(focus_moves=2))], "failed")
    assert latency.main(["key-step", "--fps", "60", str(failed)]) == 1
    assert capsys.readouterr().out.startswith("FAIL: press 1 moved the focus 2 times")

    criterion = ["--effect-median", "0.1", "--effect-slowest", "0.2", "--ready-median", "0.3"]
    assert latency.main(["key-step", "--fps", "60", str(passed), *criterion]) == 1
    assert "outside the adopted criterion" in capsys.readouterr().out

    empty = record(tmp_path, [], "empty")
    assert latency.main(["key-step", "--fps", "60", str(empty)]) == 2
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "NOT RUN: no press was recorded"
    assert out[1] == "send to effect: median -, slowest -"


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        "[]",
        '{"presses": {}}',
        '{"presses": [1]}',
        '{"presses": [{"made_while_idle": true, "device": "x"}]}',
        '{"presses": [{"made_while_idle": 1, "sent": true, "focus_moves": 1, '
        '"outcome_shown": true}]}',
        '{"presses": [{"made_while_idle": true}]}',
    ],
    ids=["json", "not-object", "not-list", "not-press", "unknown-field", "not-bool", "missing"],
)
def test_an_unusable_record_is_not_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], content: str
) -> None:
    path = tmp_path / "k1.json"
    path.write_text(content, encoding="utf-8")
    assert latency.main(["key-step", "--fps", "60", str(path)]) == 2
    assert capsys.readouterr().out.startswith("NOT RUN: unusable record")


def test_a_missing_record_or_a_partial_criterion_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert latency.main(["key-step", "--fps", "60", str(tmp_path / "absent.json")]) == 2
    assert capsys.readouterr().out.startswith("NOT RUN: unusable record")
    with pytest.raises(SystemExit):
        latency.main(["key-step", "--fps", "60", "x.json", "--effect-median", "0.5"])


def test_no_statistic_is_made_up_from_no_value() -> None:
    with pytest.raises(ValueError, match="at least one"):
        latency.distribution([])
    with pytest.raises(ValueError, match="at least one"):
        latency.percentile([], 50)
