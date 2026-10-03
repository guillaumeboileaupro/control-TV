"""The read-only hardware observation tool (`scripts/cast_observe.py`).

No test touches the network: discovery and the receiver are never reached here.
"""

from __future__ import annotations

import ast
import importlib.util
import stat
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "cast_observe.py"

# Every PyChromecast/control-TV call that changes what a receiver does, or may launch an app.
FORBIDDEN_CALLS = {
    "play",
    "pause",
    "stop",
    "seek",
    "set_volume",
    "set_muted",
    "set_volume_muted",
    "load_media",
    "play_media",
    "queue_next",
    "queue_prev",
    "launch",
    "launch_app",
    "quit_app",
    "update_status",
    "send_message",
    "_send_command",
}


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("control_tv_cast_observe", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


observe = load_script()


def test_the_tool_calls_no_control_command() -> None:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert called.isdisjoint(FORBIDDEN_CALLS), called & FORBIDDEN_CALLS


def test_the_only_message_the_tool_sends_is_a_media_get_status() -> None:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    sent = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "send_message_nocheck"
    ]

    assert len(sent) == 1
    message = sent[0].args[0]
    assert isinstance(message, ast.Dict)
    assert [ast.literal_eval(k) for k in message.keys if k is not None] == ["type"]
    assert [ast.literal_eval(v) for v in message.values] == ["GET_STATUS"]


def git_repo(path: Path, ignored: str) -> Path:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / ".gitignore").write_text(f"/{ignored}/\n", encoding="utf-8")
    return path


def test_evidence_goes_only_to_an_ignored_directory_with_private_permissions(
    tmp_path: Path,
) -> None:
    repo = git_repo(tmp_path / "repo", "private")

    directory = observe.private_run_dir(repo / "private" / "hardware", "run-1", repo=repo)

    assert directory == (repo / "private" / "hardware" / "run-1").resolve()
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700


def test_a_tracked_location_is_refused(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo", "private")

    with pytest.raises(SystemExit, match="not ignored by git"):
        observe.private_run_dir(repo / "docs", "run-1", repo=repo)

    assert not (repo / "docs").exists()


def test_a_location_outside_the_repository_is_refused(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo", "private")

    with pytest.raises(SystemExit, match="not inside the repository"):
        observe.private_run_dir(tmp_path / "elsewhere", "run-1", repo=repo)


def test_the_repository_default_location_is_ignored_by_git() -> None:
    probe = observe.PRIVATE_ROOT / "any-run" / "snapshot.jsonl"
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", str(probe)], cwd=observe.REPO_ROOT, check=False
    )

    assert ignored.returncode == 0


def test_a_message_record_keeps_its_type_request_id_and_raw_payload() -> None:
    now = datetime(2026, 9, 29, 19, 32, 49, tzinfo=UTC)
    data = {"type": "MEDIA_STATUS", "requestId": 0, "status": [{"currentTime": 3340.47}]}

    record = observe.message_record(observe.MEDIA_NAMESPACE, data, now=now)

    assert record == {
        "at": "2026-09-29T19:32:49+00:00",
        "event": "cast_message",
        "namespace": "urn:x-cast:com.google.cast.media",
        "type": "MEDIA_STATUS",
        "requestId": 0,
        "raw": data,
    }


def test_the_file_sink_appends_json_lines_readable_by_the_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "watch.jsonl"
    sink = observe.file_sink(path)

    sink({"event": "a"})
    sink({"event": "b"})

    assert path.read_text(encoding="utf-8").splitlines() == ['{"event": "a"}', '{"event": "b"}']
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_watching_registers_passive_recorders_and_removes_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registered: list[Any] = []
    removed: list[Any] = []
    records: list[dict[str, Any]] = []

    def register(handler: Any) -> None:
        registered.append(handler)
        # The receiver broadcasts a status while the window is open.
        handled = handler.receive_message(None, {"type": "MEDIA_STATUS", "requestId": 0})
        assert handled is False  # never claims the message: other handlers still get it

    socket_client = SimpleNamespace(register_handler=register, unregister_handler=removed.append)
    transport = SimpleNamespace(_casts={"device": SimpleNamespace(socket_client=socket_client)})
    monkeypatch.setattr(observe.time, "sleep", lambda seconds: None)

    count = observe._watch(transport, "device", 1.0, records.append)

    assert count == 2
    assert [r.namespace for r in registered] == [
        observe.MEDIA_NAMESPACE,
        observe.RECEIVER_NAMESPACE,
    ]
    assert removed == registered
    assert [r["namespace"] for r in records] == [
        observe.MEDIA_NAMESPACE,
        observe.RECEIVER_NAMESPACE,
    ]


@pytest.mark.parametrize("seconds", ["0", "-1", "601"])
def test_an_unreasonable_watch_window_is_refused_before_any_network_access(
    seconds: str,
) -> None:
    with pytest.raises(SystemExit, match="seconds"):
        observe.main(["watch", "--device-id", "x", "--seconds", seconds])
