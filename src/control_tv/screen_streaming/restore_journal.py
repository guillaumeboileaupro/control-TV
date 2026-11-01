"""Private journals of what a screen stream changed on the desktop, kept until it is put back.

D2 records the extended screen and the screens it moved; D4-5 records the computer's sound
output. A journal is written *before* the change and deleted only once the restoration was
verified, so a process killed in between (SIGKILL, crash, power loss) leaves exactly what the
next start needs to finish it. It holds no device, network or user data beyond those names.

Rules:
- the directory is private (0700) and the file 0600; it lives in `$XDG_STATE_HOME/control-tv`
  (`~/.local/state/control-tv` by default), never in the source tree or a shared location;
- a write goes to a temporary file that is flushed, synced and atomically renamed, then the
  directory is synced: a crash leaves the previous complete journal or the new one, never a
  partial one;
- an unreadable or unexpected journal is an error, not an empty one: it is kept for the owner
  rather than silently dropped with the restoration it describes.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Mapping
from pathlib import Path

from control_tv.json_types import JsonObject
from control_tv.screen_streaming.model import StreamCleanupError

VERSION = 1


def state_path(name: str, environ: Mapping[str, str] | None = None) -> Path:
    """`name` in the user's private control-TV state directory."""
    values = os.environ if environ is None else environ
    base = values.get("XDG_STATE_HOME")
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / "control-tv" / name


class RestoreJournal:
    """One restoration journal file, written atomically and removed once restored."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> JsonObject | None:
        """The saved restoration, or None when nothing is pending."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except (FileNotFoundError, NotADirectoryError):
            return None  # no journal can exist there: nothing is pending
        except OSError as error:
            raise StreamCleanupError(
                f"the saved restoration {self.path.name} cannot be read"
            ) from error
        try:
            value = json.loads(text)
        except ValueError as error:
            raise StreamCleanupError(
                f"the saved restoration {self.path.name} is invalid"
            ) from error
        if not isinstance(value, dict) or value.get("version") != VERSION:
            raise StreamCleanupError(f"the saved restoration {self.path.name} is invalid")
        return value

    def write(self, payload: JsonObject) -> None:
        """Replace the journal with `payload`, durably, before the change it describes."""
        directory = self.path.parent
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        temporary = self.path.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump({"version": VERSION, **payload}, stream, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            _sync_directory(directory)
        except BaseException:
            with contextlib.suppress(OSError):
                temporary.unlink()
            raise

    def clear(self) -> None:
        """Forget the journal once its restoration was verified (nothing pending is fine)."""
        try:
            self.path.unlink()
        except (FileNotFoundError, NotADirectoryError):
            return
        except OSError as error:
            raise StreamCleanupError(
                f"the saved restoration {self.path.name} cannot be removed"
            ) from error
        try:
            _sync_directory(self.path.parent)
        except OSError as error:
            raise StreamCleanupError(
                f"the saved restoration {self.path.name} cannot be removed"
            ) from error


def _sync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
