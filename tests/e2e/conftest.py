"""Fixtures for acceptance infrastructure tests; no fixture reaches real hardware."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


@dataclass
class FakeCommandRunner:
    responses: list[subprocess.CompletedProcess[str]] = field(default_factory=list)
    calls: list[tuple[tuple[str, ...], float]] = field(default_factory=list)

    def run(self, args: Sequence[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        self.calls.append((tuple(args), timeout))
        if self.responses:
            return self.responses.pop(0)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
