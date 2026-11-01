"""The private, atomic restoration journal shared by D2 (display) and D4-5 (sound)."""

from __future__ import annotations

import fcntl
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from control_tv.screen_streaming import restore_journal
from control_tv.screen_streaming.model import StreamCleanupError, StreamStartError
from control_tv.screen_streaming.restore_journal import RestoreJournal, state_path


def test_a_journal_round_trips_privately_and_is_cleared(tmp_path: Path) -> None:
    journal = RestoreJournal(tmp_path / "state" / "x.json")
    assert journal.read() is None

    journal.write({"previous": "a"})
    assert journal.read() == {"version": 1, "previous": "a"}
    assert journal.path.stat().st_mode & 0o777 == 0o600
    assert not journal.path.with_suffix(".tmp").exists()

    journal.clear()
    journal.clear()  # nothing pending is fine
    assert journal.read() is None


def test_a_rewrite_replaces_the_whole_journal(tmp_path: Path) -> None:
    journal = RestoreJournal(tmp_path / "x.json")
    journal.write({"first": 1, "kept": True})
    journal.write({"second": 2})
    assert journal.read() == {"version": 1, "second": 2}


@pytest.mark.parametrize("content", ["[]", '"text"', '{"version":2}', "{"])
def test_an_unexpected_journal_is_an_error_and_stays(tmp_path: Path, content: str) -> None:
    path = tmp_path / "x.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(StreamCleanupError, match="is invalid"):
        RestoreJournal(path).read()
    assert path.read_text(encoding="utf-8") == content


def test_an_unreadable_journal_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "x.json").mkdir()
    with pytest.raises(StreamCleanupError, match="cannot be read"):
        RestoreJournal(tmp_path / "x.json").read()


def test_a_failed_write_leaves_no_temporary_file(tmp_path: Path) -> None:
    target = tmp_path / "x.json"
    target.mkdir()
    (target / "keep").write_text("", encoding="utf-8")  # os.replace cannot overwrite it

    with pytest.raises(OSError):
        RestoreJournal(target).write({"a": 1})
    assert not target.with_suffix(".tmp").exists() and (target / "keep").exists()


def test_a_journal_that_cannot_be_removed_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "x.json").mkdir()
    with pytest.raises(StreamCleanupError, match="cannot be removed"):
        RestoreJournal(tmp_path / "x.json").clear()

    journal = RestoreJournal(tmp_path / "y.json")
    journal.write({"a": 1})

    def unsynced(directory: Path) -> None:
        raise OSError(os.strerror(5))

    monkeypatch.setattr(restore_journal, "_sync_directory", unsynced)
    with pytest.raises(StreamCleanupError, match="cannot be removed"):
        journal.clear()


def test_journals_live_in_the_user_state_directory() -> None:
    assert state_path("x.json", {"XDG_STATE_HOME": "/run/s"}) == Path("/run/s/control-tv/x.json")
    assert state_path("x.json", {}) == Path.home() / ".local/state/control-tv/x.json"


def test_the_writer_owns_the_journal_until_it_is_cleared(tmp_path: Path) -> None:
    path = tmp_path / "state" / "screen.json"
    owner, other = RestoreJournal(path), RestoreJournal(path)

    owner.write({"output": "HDMI-2"})
    assert owner.claim() and not other.claim()
    with pytest.raises(StreamStartError, match="another running stream"):
        other.write({"output": "HDMI-3"})
    assert owner.read() == {"version": 1, "output": "HDMI-2"}
    assert path.with_suffix(".lock").stat().st_mode & 0o777 == 0o600

    owner.clear()
    assert other.claim()  # free once the restoration was verified
    other.release()
    other.release()  # idempotent


def test_a_released_journal_is_kept_for_the_next_owner(tmp_path: Path) -> None:
    path = tmp_path / "screen.json"
    owner = RestoreJournal(path)
    owner.write({"output": "HDMI-2"})
    owner.release()

    recovery = RestoreJournal(path)
    assert recovery.claim() and recovery.read() == {"version": 1, "output": "HDMI-2"}
    recovery.clear()
    assert not path.exists() and RestoreJournal(path).claim()


def test_clearing_nothing_also_gives_the_ownership_up(tmp_path: Path) -> None:
    path = tmp_path / "screen.json"
    owner = RestoreJournal(path)
    assert owner.claim()
    owner.clear()
    assert RestoreJournal(path).claim()


def test_a_failed_lock_does_not_leak_its_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "screen.json"
    closed: list[int] = []
    real_close = os.close

    def flock(descriptor: int, operation: int) -> None:
        raise OSError("no locks here")

    def close(descriptor: int) -> None:
        closed.append(descriptor)
        real_close(descriptor)

    monkeypatch.setattr(fcntl, "flock", flock)
    monkeypatch.setattr(os, "close", close)
    with pytest.raises(OSError, match="no locks here"):
        RestoreJournal(path).claim()
    assert len(closed) == 1


_OWNER = """
import sys, time
from pathlib import Path
from control_tv.screen_streaming.restore_journal import RestoreJournal
RestoreJournal(Path(sys.argv[1])).write({"output": "HDMI-2"})
print("owned", flush=True)
time.sleep(60)
"""


def test_a_live_process_keeps_its_journal_and_the_kernel_frees_it_at_sigkill(
    tmp_path: Path,
) -> None:
    path = tmp_path / "screen.json"
    child = subprocess.Popen(
        [sys.executable, "-c", _OWNER, str(path)],
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    try:
        assert child.stdout is not None and child.stdout.readline().strip() == "owned"
        assert not RestoreJournal(path).claim()  # a running stream: left alone
    finally:
        child.send_signal(signal.SIGKILL)
        child.wait(timeout=10)

    recovery = RestoreJournal(path)
    assert recovery.claim() and recovery.read() == {"version": 1, "output": "HDMI-2"}
    recovery.release()
