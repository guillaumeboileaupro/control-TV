"""Google TV acceptance contracts and C6/C7 evidence validation.

The catalog mirrors docs/DUAL_CONTROLLER_VALIDATION.md. It prepares real execution but never
sends a command; product behavior remains exclusively in the shared Python control service.
"""

from __future__ import annotations

from collections.abc import Sequence

from acceptance import Context, Contract, Result, Scenario, manual_only
from acceptance_trace_evidence import load_trace, validate_c6, validate_c7
from control_tv.command_trace import TraceEvent

__all__ = ["TraceEvent", "blocked_real", "contract", "load_trace", "validate_c6", "validate_c7"]


def blocked_real(context: Context, scenario: Scenario) -> Result:
    """Every Google TV scenario is manual: no adapter drives a real TV, whatever the flags."""
    del context
    return manual_only(scenario, "this TV step")


def contract(
    preconditions: Sequence[str],
    device: str,
    commands: Sequence[str],
    initial: str,
    expected: str,
) -> Contract:
    return Contract(tuple(preconditions), device, tuple(commands), initial, expected)
