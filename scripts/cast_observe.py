"""Read-only Cast observation for hardware validation campaigns (development tooling only).

This is NOT part of the product and is never imported by it. It records, privately, what a
receiver reports, so that a separately authorized hardware command can be judged from evidence
instead of from memory:

    python3 scripts/cast_observe.py snapshot --device-id ID
    python3 scripts/cast_observe.py watch --device-id ID --seconds 60

- `snapshot`: one fresh media GET_STATUS (raw reply, with its requestId and type) plus the
  domain status ControlService derives from a fresh read.
- `watch`: the same snapshot, then every message the receiver sends on the media and receiver
  namespaces during the window, recorded passively (a command sent by another sender shows up
  here through the receiver's broadcasts), then a final domain status.

It sends no control command at all: the only requests are discovery and media GET_STATUS, sent
with `send_message_nocheck`, which never launches an application. There is no retry and no
replay. Every record is written as one JSON line, with a timestamp, under the git-ignored
`.ai-private/hardware/` directory (directory mode 700, file mode 600); the tool refuses any
other location. Device identifiers, addresses and content ids stay in those private files.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
PRIVATE_ROOT = REPO_ROOT / ".ai-private" / "hardware"
MEDIA_NAMESPACE = "urn:x-cast:com.google.cast.media"
RECEIVER_NAMESPACE = "urn:x-cast:com.google.cast.receiver"

Sink = Callable[[dict[str, Any]], None]


def private_run_dir(root: Path, run: str, *, repo: Path = REPO_ROOT) -> Path:
    """The directory a run writes to: under `root`, which must be inside `repo` and ignored by
    git, so that hardware evidence can never be committed by accident."""
    resolved = root.resolve()
    if repo.resolve() not in resolved.parents:
        raise SystemExit(f"refused: {resolved} is not inside the repository")
    probe = resolved / run / "probe.jsonl"
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", str(probe)], cwd=repo, check=False
    ).returncode
    if ignored != 0:
        raise SystemExit(f"refused: {resolved} is not ignored by git")
    directory = resolved / run
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    return directory


def message_record(namespace: str, data: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    """One received Cast message as a record: when, which namespace, its type and requestId
    (0 or absent for a broadcast, our own id for a reply), and the raw payload."""
    return {
        "at": now.isoformat(),
        "event": "cast_message",
        "namespace": namespace,
        "type": data.get("type"),
        "requestId": data.get("requestId"),
        "raw": data,
    }


def file_sink(path: Path) -> Sink:
    def write(record: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str) + "\n")
        os.chmod(path, 0o600)

    return write


def _now() -> datetime:
    return datetime.now(UTC)


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def _snapshot(control: Any, transport: Any, device_id: Any, sink: Sink) -> None:
    from pychromecast.response_handler import WaitResponse

    cast_device = transport._casts[device_id]  # the transport's own connection, read-only use
    response = WaitResponse(5.0, "media status")
    sent_at = _now()
    cast_device.media_controller.send_message_nocheck(
        {"type": "GET_STATUS"}, callback_function=response.callback
    )
    response.wait_response()
    reply = response.response or {}
    sink(
        {
            "at": _now().isoformat(),
            "event": "media_get_status_reply",
            "sentAt": sent_at.isoformat(),
            "type": reply.get("type"),
            "requestId": reply.get("requestId"),
            "raw": reply,
        }
    )
    status = control.get_status(device_id)
    sink({"at": _now().isoformat(), "event": "domain_status", "status": asdict(status)})


def _watch(transport: Any, device_id: Any, seconds: float, sink: Sink) -> int:
    from pychromecast.controllers import BaseController

    class Recorder(BaseController):
        """Passive: it only receives what the receiver sends, and never answers."""

        def receive_message(self, _message: object, data: dict[str, Any]) -> bool:
            sink(message_record(self.namespace, data, now=_now()))
            return False

    socket_client = transport._casts[device_id].socket_client
    recorders = [Recorder(MEDIA_NAMESPACE), Recorder(RECEIVER_NAMESPACE)]
    for recorder in recorders:
        socket_client.register_handler(recorder)
    try:
        time.sleep(seconds)
    finally:
        for recorder in recorders:
            socket_client.unregister_handler(recorder)
    return len(recorders)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("mode", choices=["snapshot", "watch"])
    parser.add_argument("--device-id", required=True, help="stable id from discovery")
    parser.add_argument("--seconds", type=float, default=60.0, help="watch window")
    parser.add_argument("--run", default=None, help="run name (default: a timestamp)")
    args = parser.parse_args(argv)
    if not 0 < args.seconds <= 600:
        raise SystemExit("--seconds must be within (0, 600]")

    from control_tv.adapters import PyChromecastTransport
    from control_tv.domain import DeviceId
    from control_tv.service import ControlService

    run = args.run or _now().strftime("%Y-%m-%dT%H%M%SZ")
    sink = file_sink(private_run_dir(PRIVATE_ROOT, run) / f"{args.mode}.jsonl")
    sink({"at": _now().isoformat(), "event": "start", "mode": args.mode, "head": _git_head()})
    transport = PyChromecastTransport()
    control = ControlService(transport)
    device_id = DeviceId(args.device_id)
    try:
        devices = control.discover_devices(timeout=5.0)
        sink({"at": _now().isoformat(), "event": "discovered", "count": len(devices)})
        if device_id not in {device.id for device in devices}:
            print("device not discovered; nothing recorded beyond discovery", file=sys.stderr)
            return 2
        _snapshot(control, transport, device_id, sink)
        if args.mode == "watch":
            _watch(transport, device_id, args.seconds, sink)
            status = control.get_status(device_id)
            sink({"at": _now().isoformat(), "event": "domain_status", "status": asdict(status)})
        print(f"recorded privately under {PRIVATE_ROOT.relative_to(REPO_ROOT) / run}")
        return 0
    finally:
        transport.close()
        sink({"at": _now().isoformat(), "event": "closed", "commandsSent": 0})


if __name__ == "__main__":
    sys.exit(main())
