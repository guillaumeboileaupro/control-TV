"""Run the independent C6 observer (docs/COMMAND_TRACE.md). Owner-run only, for one authorized
step: it **reads** the receiver status (no command) until the held command's session changes.

    CONTROL_TV_OBSERVED_DEVICE=<stable device id> python3 scripts/acceptance_observer.py \
        --gate DIR --trace OBSERVER_TRACE.jsonl [--timeout 60]

The device id is read from the environment so it never appears in a shell history or a trace.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from control_tv.acceptance_observer import IndependentObserver
from control_tv.command_trace import TraceRecorder, TraceRole
from control_tv.domain import DeviceId
from control_tv.trace_files import JsonLinesTraceSink


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - reads a real TV
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--gate", type=Path, required=True)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=60.0)
    arguments = parser.parse_args(argv)
    device = os.environ.get("CONTROL_TV_OBSERVED_DEVICE", "")
    if not device:
        print("set CONTROL_TV_OBSERVED_DEVICE to the TV's stable device id", file=sys.stderr)
        return 2
    from control_tv.adapters import PyChromecastTransport
    from control_tv.service import ControlService

    service = ControlService(PyChromecastTransport())
    service.discover_devices()
    device_id = DeviceId(device)

    def read_session() -> int | None:
        media = service.get_status(device_id).media
        return None if media is None else media.media_session_id

    recorder = TraceRecorder(JsonLinesTraceSink(arguments.trace), role=TraceRole.OBSERVER)
    outcome = IndependentObserver(
        arguments.gate,
        read_session,
        recorder,
        timeout_seconds=arguments.timeout,
        on_hold_seen=lambda: print("hold seen on the captured session: start the other video now"),
    ).observe()
    print(f"observer outcome: {outcome.value}")
    return 0 if outcome.value == "released" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
