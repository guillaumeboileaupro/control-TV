"""The tests run this checkout's own code.

Worktrees share a machine: a virtual environment made in another checkout installs that checkout's
`src/` (an editable install), and a `PYTHONPATH` can point anywhere. pytest's `pythonpath` puts this
checkout's `src/` first, so a test run here never exercises another worktree's code.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import control_tv


def test_the_tests_import_this_checkouts_control_tv() -> None:
    source = Path(__file__).resolve().parents[1] / "src"

    assert Path(control_tv.__file__).resolve().is_relative_to(source)


def test_a_python_subprocess_of_the_tests_imports_this_checkouts_control_tv() -> None:
    """Tests that start `python -m control_tv...` must run this checkout's code too."""
    source = Path(__file__).resolve().parents[1] / "src"
    found = subprocess.run(
        [sys.executable, "-c", "import control_tv; print(control_tv.__file__)"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    assert Path(found).resolve().is_relative_to(source)
