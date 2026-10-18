"""The bridge protocol for a host that runs Python in its own process (the Android app).

The desktop shell starts `control_tv.bridge` as a child process and exchanges JSON lines
over its stdio. An Android app cannot start a separate Python process; it embeds CPython
(Chaquopy) and calls `handle` with one request line, getting one response line back. Both
paths go through the same `bridge.handle_line` and the same single `ControlService`, so no
dispatch, validation, confirmation or Cast logic is duplicated on the host side.

The caller must not call `handle` concurrently: the desktop bridge is single-flight, and
the Android host serializes calls on one worker thread.
"""

from __future__ import annotations

import json
import os
import platform
from pathlib import Path

import control_tv
from control_tv.bridge import default_service, handle_line
from control_tv.service import ControlService
from control_tv.trace_files import AcceptanceTracing, android_tracing

# protobuf's native extension is not available on Android; its pure-Python implementation
# is the one the embedded runtime ships, chosen before anything imports protobuf.
os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")

_control: ControlService | None = None
_tracing: AcceptanceTracing | None = None


def _service() -> ControlService:
    global _control
    if _control is None:
        _control = default_service(_tracing)
    return _control


def configure_acceptance_trace(directory: str) -> str:
    """Switch on the acceptance trace and confirmation hold under `directory` (the app's
    private files). Called only by the Android **debug** build, after the owner enabled the
    acceptance mode, and before the first request: the service is built once per process."""
    global _tracing
    if _control is not None:
        raise RuntimeError("the acceptance trace must be configured before the first request")
    _tracing = android_tracing(Path(directory))
    return acceptance_trace_status()


def acceptance_trace_status() -> str:
    """Whether this process records an acceptance trace, for the debug acceptance screen."""
    tracing = _tracing
    if tracing is None or not tracing.recorder.active:
        return json.dumps({"active": False, "role": None, "hold": False, "file": ""})
    role = tracing.recorder.role
    return json.dumps(
        {
            "active": True,
            "role": None if role is None else role.value,
            "hold": tracing.gate is not None,
            "file": "" if tracing.trace_file is None else str(tracing.trace_file),
        }
    )


def handle(line: str) -> str:
    """Run one request line against the process-wide service; return one response line."""
    return json.dumps(handle_line(_service(), line))


def startup_diagnostic() -> str:
    """One log line proving the embedded runtime works, written by the Android app at start.

    It runs a `ping` through `handle`, the path every request takes, and names only the
    Python and control-TV versions: no device, address, identifier or media. `ping` never
    touches the network, so no Cast message is sent.
    """
    response = json.loads(handle(json.dumps({"id": 0, "method": "ping", "params": {}})))
    imported = f"python {platform.python_version()}; control_tv {control_tv.__version__} imported"
    if not response.get("ok"):
        return f"{imported}; embedded ping failed ({response['error']['code']})"
    version = response["result"]["controlTvVersion"]
    remote = "yes" if response["result"].get("tvRemote") else "no"
    return f"{imported}; embedded ping ok, controlTvVersion={version}, tvRemote={remote}"
