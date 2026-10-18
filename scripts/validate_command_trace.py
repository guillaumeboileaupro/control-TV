"""Validate C6/C7 command-trace evidence offline: no TV, no network, no flag needed.

    python3 scripts/validate_command_trace.py list TRACE [TRACE...]
    python3 scripts/validate_command_trace.py c6 --request-id ID REMOTE_TRACE OBSERVER_TRACE
    python3 scripts/validate_command_trace.py c7 --request-id ID REMOTE_TRACE

Exit status 0 means PASS, 1 FAIL (a violation the evidence demonstrates), 2 BLOCKED (no usable
evidence: a trace that is absent, empty, unreadable or incomplete, an invalid or unknown request
id, an uncorrelated C7 trace), always with the reason. BLOCKED is never a hardware failure. A
PASS is application-side evidence only: not a network transmission, a reception by the TV or a
physical effect (docs/COMMAND_TRACE.md).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parent / "src"))

from acceptance_trace_evidence import (  # noqa: E402
    Classification,
    classify_c6,
    classify_c7,
    load_traces,
    summarize,
)
from control_tv.trace_files import is_request_id  # noqa: E402

EXIT_STATUS = {Classification.PASS: 0, Classification.FAIL: 1, Classification.BLOCKED: 2}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list", help="list the requests of trace files")
    listing.add_argument("traces", type=Path, nargs="+")
    for step in ("c6", "c7"):
        check = commands.add_parser(step, help=f"validate {step.upper()} evidence")
        check.add_argument("--request-id", required=True)
        check.add_argument("traces", type=Path, nargs="+")
    arguments = parser.parse_args(argv)
    if arguments.command != "list" and not is_request_id(arguments.request_id):
        print(f"BLOCKED: invalid request id {arguments.request_id!r}")
        return EXIT_STATUS[Classification.BLOCKED]
    try:
        events = load_traces(arguments.traces)
    except (OSError, ValueError) as error:
        print(f"BLOCKED: unusable trace: {error}")
        return EXIT_STATUS[Classification.BLOCKED]
    if arguments.command == "list":
        for line in summarize(events):
            print(line)
        return 0
    classify = classify_c6 if arguments.command == "c6" else classify_c7
    classification, reason = classify(events, arguments.request_id)
    print(f"{classification.value}: {reason}")
    return EXIT_STATUS[classification]


if __name__ == "__main__":
    raise SystemExit(main())
