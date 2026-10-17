"""Google TV acceptance contracts and C6/C7 evidence validation.

The catalog mirrors docs/DUAL_CONTROLLER_VALIDATION.md. It prepares real execution but never
sends a command; product behavior remains exclusively in the shared Python control service.
"""

from __future__ import annotations

import time
from collections.abc import Sequence

from acceptance import Context, Contract, Outcome, Result, Scenario, make_result
from acceptance_trace_evidence import load_trace, validate_c6, validate_c7
from control_tv.command_trace import TraceEvent

__all__ = ["TraceEvent", "blocked_real", "contract", "load_trace", "validate_c6", "validate_c7"]


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


def contract(
    preconditions: Sequence[str],
    device: str,
    commands: Sequence[str],
    initial: str,
    expected: str,
) -> Contract:
    return Contract(tuple(preconditions), device, tuple(commands), initial, expected)
