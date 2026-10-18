"""Local files for acceptance tracing: the JSONL evidence sink, the file-based confirmation hold,
and the opt-in configuration from the environment (`docs/COMMAND_TRACE.md`).

Everything lives in private local directories (created 0700; an existing directory that group
or others can access is refused), files are 0600 and opened without following symbolic links.
No network service is opened; nothing is written unless tracing is configured explicitly.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import stat
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path

from control_tv.command_trace import (
    ConfirmationGate,
    HoldOutcome,
    HoldRequest,
    TraceConfigurationError,
    TraceEvent,
    TraceRecorder,
    TraceRole,
)

TRACE_ENV = "CONTROL_TV_COMMAND_TRACE"
ROLE_ENV = "CONTROL_TV_TRACE_CLIENT"
GATE_ENV = "CONTROL_TV_CONFIRMATION_GATE"
GATE_TIMEOUT_ENV = "CONTROL_TV_CONFIRMATION_GATE_SECONDS"
MAX_HOLD_SECONDS = 120.0
DEFAULT_HOLD_SECONDS = 30.0
HELD_SUFFIX = ".held"
RELEASE_SUFFIX = ".release"
INCOMPLETE_SUFFIX = ".incomplete"

_REQUEST_ID = re.compile(r"[A-Za-z0-9-]{1,64}")
_TOKEN = re.compile(r"[0-9a-f]{32}")
_FILE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _private_directory_fd(path: Path) -> int:
    """Open ``path`` without following any symbolic component.

    Missing components are created mode 0700 relative to an already-open parent. The returned
    descriptor is the final directory after checking its owner and permissions, which prevents a
    path component from being exchanged between validation and the open used by callers.
    """
    absolute = Path(os.path.abspath(path))
    descriptor = os.open(absolute.anchor, _DIRECTORY_FLAGS)
    try:
        for component in absolute.parts[1:]:
            try:
                child = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptor)
            except FileNotFoundError:
                with contextlib.suppress(FileExistsError):
                    os.mkdir(component, mode=0o700, dir_fd=descriptor)
                child = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        info = os.fstat(descriptor)
        if info.st_uid != os.geteuid():
            raise TraceConfigurationError(f"{path} must be owned by the current user")
        if info.st_mode & 0o077:
            raise TraceConfigurationError(
                f"{path} must be private (mode 0700): use a dedicated directory"
            )
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def private_directory(path: Path) -> Path:
    """`path` as a private directory: created 0700 when missing; refused when it is a symbolic
    link, not a directory, or accessible by group or others."""
    try:
        descriptor = _private_directory_fd(path)
    except OSError as error:
        raise TraceConfigurationError(
            f"{path} must be a real directory with no symbolic path component"
        ) from error
    os.close(descriptor)
    return path


def _open_private(path: Path, flags: int) -> int:
    """A file descriptor on a regular file owned by this user, mode 0600, never through a link."""
    directory = _private_directory_fd(path.parent)
    try:
        descriptor = os.open(path.name, _FILE_FLAGS | flags, 0o600, dir_fd=directory)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
                raise OSError(f"{path.name} is not a regular file of this user")
            os.fchmod(descriptor, 0o600)
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor
    finally:
        os.close(directory)


def _open_private_read(path: Path) -> int:
    """Open an existing private regular file without following its directory or file links."""
    directory = _private_directory_fd(path.parent)
    try:
        descriptor = os.open(
            path.name,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=directory,
        )
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
                raise OSError(f"{path.name} is not a regular file of this user")
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor
    finally:
        os.close(directory)


def _unlink_private(directory: Path, *names: str) -> None:
    descriptor = _private_directory_fd(directory)
    try:
        for name in names:
            with contextlib.suppress(FileNotFoundError, IsADirectoryError):
                os.unlink(name, dir_fd=descriptor)
    finally:
        os.close(descriptor)


def _write_atomically(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    descriptor = _open_private(temporary, os.O_TRUNC)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(text)
    directory = _private_directory_fd(path.parent)
    try:
        os.replace(temporary.name, path.name, src_dir_fd=directory, dst_dir_fd=directory)
    finally:
        os.close(directory)


class JsonLinesTraceSink:
    """Appends one JSON line per event to a private local file; on a failed write it leaves a
    `<file>.incomplete` marker (best effort) that makes validators reject the whole file."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def write(self, event: TraceEvent) -> None:
        line = json.dumps(asdict(event), separators=(",", ":"), sort_keys=True) + "\n"
        try:
            private_directory(self._path.parent)
            descriptor = _open_private(self._path, os.O_APPEND)
            with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
                stream.write(line)
        except Exception:
            self._mark_incomplete()
            raise

    def _mark_incomplete(self) -> None:
        marker = self._path.with_name(self._path.name + INCOMPLETE_SUFFIX)
        with contextlib.suppress(OSError):  # the index gap still exposes the loss
            os.close(_open_private(marker, 0))


class FileConfirmationGate:
    """Holds a sent command's confirmation until the observer releases it, through two files in
    a private directory: `<request>.held` (written here) and `<request>.release` (written by the
    observer, holding its token). Bounded by `timeout_seconds` (at most `MAX_HOLD_SECONDS`); both
    files are removed when the hold ends, including on an interruption."""

    def __init__(
        self,
        directory: Path,
        *,
        timeout_seconds: float = DEFAULT_HOLD_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        poll_seconds: float = 0.05,
    ) -> None:
        if not 0 < timeout_seconds <= MAX_HOLD_SECONDS:
            raise TraceConfigurationError(
                f"the confirmation hold must last more than 0 and at most {MAX_HOLD_SECONDS:g} s"
            )
        self._directory = directory
        self._timeout = timeout_seconds
        self._clock = clock
        self._sleep = sleep
        self._poll = poll_seconds

    def hold(self, request: HoldRequest) -> HoldOutcome:
        if not _REQUEST_ID.fullmatch(request.request_id):
            raise TraceConfigurationError("the request id cannot name a hold file")
        directory = private_directory(self._directory)
        held = directory / f"{request.request_id}{HELD_SUFFIX}"
        release = directory / f"{request.request_id}{RELEASE_SUFFIX}"
        marker = {
            "request_id": request.request_id,
            "command": request.command.value,
            "media_session_id": request.media_session_id,
        }
        try:
            _write_atomically(held, json.dumps(marker) + "\n")
            deadline = self._clock() + self._timeout
            while self._clock() < deadline:
                token = _read_token(release)
                if token is not None:
                    return HoldOutcome(released=True, token=token)
                self._sleep(self._poll)
            return HoldOutcome(released=False)
        finally:
            _unlink_private(directory, held.name, release.name)


@dataclass(frozen=True, slots=True)
class HeldCommand:
    """A hold marker as the observer reads it: which request waits, on which session."""

    request_id: str
    media_session_id: int
    path: Path


def held_commands(directory: Path) -> tuple[HeldCommand, ...]:
    """The commands currently held in `directory`; malformed markers are ignored."""
    held: list[HeldCommand] = []
    descriptor = _private_directory_fd(directory)
    try:
        names = sorted(name for name in os.listdir(descriptor) if name.endswith(HELD_SUFFIX))
        for name in names:
            path = directory / name
            try:
                file_descriptor = os.open(
                    name,
                    os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=descriptor,
                )
                with os.fdopen(file_descriptor, "r", encoding="utf-8") as stream:
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
                        continue
                    marker = json.loads(stream.read())
            except (OSError, ValueError):
                continue
            request_id = marker.get("request_id") if isinstance(marker, dict) else None
            session = marker.get("media_session_id") if isinstance(marker, dict) else None
            if (
                isinstance(request_id, str)
                and _REQUEST_ID.fullmatch(request_id)
                and name == f"{request_id}{HELD_SUFFIX}"
                and isinstance(session, int)
                and not isinstance(session, bool)
                and session >= 0
            ):
                held.append(HeldCommand(request_id, session, path))
    finally:
        os.close(descriptor)
    return tuple(held)


def release_command(directory: Path, request_id: str, token: str) -> None:
    """Release one held command with the observer's token."""
    if not _REQUEST_ID.fullmatch(request_id) or not _TOKEN.fullmatch(token):
        raise TraceConfigurationError("invalid request id or release token")
    _write_atomically(private_directory(directory) / f"{request_id}{RELEASE_SUFFIX}", token + "\n")


def _read_token(path: Path) -> str | None:
    try:
        descriptor = _open_private_read(path)
    except OSError:
        return None
    with os.fdopen(descriptor, "r", encoding="ascii", errors="replace") as stream:
        token = stream.read().strip()
    return token if _TOKEN.fullmatch(token) else None


@dataclass(frozen=True, slots=True)
class AcceptanceTracing:
    """What a bridge process runs with: a recorder (inactive by default) and an optional hold."""

    recorder: TraceRecorder
    gate: ConfirmationGate | None = None
    trace_file: Path | None = None


def tracing_from_environment(
    environ: Mapping[str, str] = os.environ, *, frozen: bool | None = None
) -> AcceptanceTracing:
    """Off unless `CONTROL_TV_COMMAND_TRACE` names a trace file. Then `CONTROL_TV_TRACE_CLIENT`
    must name the role (`ubuntu`, `android` or `observer`), and `CONTROL_TV_CONFIRMATION_GATE`
    may name a hold directory, refused in a packaged (frozen) release bridge."""
    destination = environ.get(TRACE_ENV, "")
    if not destination:
        if environ.get(GATE_ENV):
            raise TraceConfigurationError(f"{GATE_ENV} needs {TRACE_ENV}")
        return AcceptanceTracing(TraceRecorder())
    try:
        role = TraceRole(environ.get(ROLE_ENV, ""))
    except ValueError:
        raise TraceConfigurationError(
            f"{ROLE_ENV} must be one of: {', '.join(role.value for role in TraceRole)}"
        ) from None
    trace_file = Path(destination)
    recorder = TraceRecorder(JsonLinesTraceSink(trace_file), role=role)
    gate_directory = environ.get(GATE_ENV, "")
    if not gate_directory:
        return AcceptanceTracing(recorder, trace_file=trace_file)
    if getattr(sys, "frozen", False) if frozen is None else frozen:
        raise TraceConfigurationError("the confirmation hold is refused in a release bridge")
    if role is TraceRole.OBSERVER:
        raise TraceConfigurationError("the observer never holds a confirmation")
    try:
        seconds = float(environ.get(GATE_TIMEOUT_ENV, str(DEFAULT_HOLD_SECONDS)))
    except ValueError:
        raise TraceConfigurationError(f"{GATE_TIMEOUT_ENV} must be a number of seconds") from None
    return AcceptanceTracing(
        recorder,
        FileConfirmationGate(Path(gate_directory), timeout_seconds=seconds),
        trace_file=trace_file,
    )


def android_tracing(directory: Path) -> AcceptanceTracing:
    """The Android debug build's acceptance mode: trace and hold under the app's private files.
    Called only by the debug build's Kotlin code, after the owner switched the mode on."""
    root = private_directory(directory)
    trace_file = root / "command-trace.jsonl"
    return AcceptanceTracing(
        TraceRecorder(JsonLinesTraceSink(trace_file), role=TraceRole.ANDROID),
        FileConfirmationGate(root / "gate"),
        trace_file=trace_file,
    )
