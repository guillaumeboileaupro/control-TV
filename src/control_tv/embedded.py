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

from control_tv.bridge import handle_line
from control_tv.service import ControlService

# protobuf's native extension is not available on Android; its pure-Python implementation
# is the one the embedded runtime ships, chosen before anything imports protobuf.
os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")

_control: ControlService | None = None


def _service() -> ControlService:
    global _control
    if _control is None:
        from control_tv.adapters import PyChromecastTransport

        _control = ControlService(PyChromecastTransport())
    return _control


def handle(line: str) -> str:
    """Run one request line against the process-wide service; return one response line."""
    return json.dumps(handle_line(_service(), line))
