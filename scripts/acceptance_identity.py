"""Which build a hardware acceptance tests, provably.

Every Debian package of control-TV is version 0.1.0, so a version never identifies a build. A
build is identified by a provenance manifest that binds, for one package:

- the Git commit it was built from (and the CI run, when it comes from CI);
- the version and architecture read from the package;
- the SHA-256 of the `.deb` file;
- the SHA-256 of the application binary inside it (`usr/bin/control-tv`);
- the SHA-256 of its `md5sums` control file, which lists every packaged file with its digest
  (the application, the frozen Python bridge, icons...): the **payload** digest. dpkg keeps that
  file for an installed package, so an installed system can be compared with it. The binary alone
  is not enough: it is reproducible and stays identical when only the bridge changes.

The manifest is written by CI next to the package (`provenance.json`) or, for an older CI
artifact, by `deb-provenance` below from the run's recorded commit. The acceptance runner
recomputes both digests itself and refuses a real validation when they cannot be established
or do not match.

Usage:
    python3 scripts/acceptance_identity.py deb-provenance --deb PATH --commit SHA \
        [--run-id ID] --output PATH
    python3 scripts/acceptance_identity.py check-deb --deb PATH [--provenance PATH] \
        [--expect-commit SHA] [--expect-run-id ID] [--require-installed] [--root /]

`check-deb` exits 0 (identified; the installed state is printed), 1 FAIL (a contradiction:
another build), or 2 BLOCKED (the identity cannot be established). An absent installation is
"not installed", not an error, unless `--require-installed` is given.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import tarfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

DEB_SCHEMA = "control-tv-deb-provenance/2"
APPLICATION_MEMBER = "usr/bin/control-tv"
PAYLOAD_MEMBER = "md5sums"

_COMMIT = re.compile(r"[0-9a-f]{40}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_AR_MAGIC = b"!<arch>\n"
_AR_HEADER = 60


class IdentityError(Exception):
    """The identity of an artifact cannot be established; `str(error)` says why."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise IdentityError(f"cannot read {path.name}: {type(error).__name__}") from None
    return digest.hexdigest()


def _ar_members(deb: Path) -> Mapping[str, bytes]:
    """The members of a Debian `ar` archive, by name."""
    try:
        data = deb.read_bytes()
    except OSError as error:
        raise IdentityError(f"cannot read {deb.name}: {type(error).__name__}") from None
    if not data.startswith(_AR_MAGIC):
        raise IdentityError(f"{deb.name} is not a Debian package (no ar header)")
    members: dict[str, bytes] = {}
    offset = len(_AR_MAGIC)
    while offset + _AR_HEADER <= len(data):
        header = data[offset : offset + _AR_HEADER]
        name = header[:16].decode("ascii", errors="replace").strip().rstrip("/")
        try:
            size = int(header[48:58].decode("ascii").strip())
        except ValueError:
            raise IdentityError(f"{deb.name} has a malformed ar member header") from None
        start = offset + _AR_HEADER
        if start + size > len(data):
            raise IdentityError(f"{deb.name} is truncated")
        members[name] = data[start : start + size]
        offset = start + size + (size % 2)
    return members


def _tar_member(members: Mapping[str, bytes], prefix: str, member: str, deb: Path) -> bytes:
    archives = [name for name in members if name.startswith(prefix)]
    if len(archives) != 1:
        raise IdentityError(f"{deb.name} has no single {prefix}* member")
    try:
        with tarfile.open(fileobj=io.BytesIO(members[archives[0]]), mode="r:*") as archive:
            for info in archive.getmembers():
                if info.name.removeprefix("./") == member and info.isfile():
                    extracted = archive.extractfile(info)
                    if extracted is not None:
                        return extracted.read()
    except (tarfile.TarError, EOFError, OSError) as error:
        raise IdentityError(f"cannot read {archives[0]}: {type(error).__name__}") from None
    raise IdentityError(f"{deb.name} has no regular file {member}")


def deb_member_sha256(deb: Path, member: str = APPLICATION_MEMBER) -> str:
    """The SHA-256 of one regular file of the package's data archive."""
    return hashlib.sha256(_tar_member(_ar_members(deb), "data.tar", member, deb)).hexdigest()


def deb_payload_sha256(deb: Path) -> str:
    """The SHA-256 of the package's `md5sums` control file (every packaged file's digest)."""
    return hashlib.sha256(
        _tar_member(_ar_members(deb), "control.tar", PAYLOAD_MEMBER, deb)
    ).hexdigest()


def deb_control_fields(deb: Path) -> Mapping[str, str]:
    """The fields of the package's `control` file (`Package`, `Version`, `Architecture`...)."""
    text = _tar_member(_ar_members(deb), "control.tar", "control", deb).decode(
        "utf-8", errors="replace"
    )
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if line and not line[0].isspace() and ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip()
    return fields


@dataclass(frozen=True)
class DebProvenance:
    commit: str
    version: str
    architecture: str
    deb_sha256: str
    binary_sha256: str
    payload_sha256: str
    run_id: int | None = None

    def __post_init__(self) -> None:
        if not _COMMIT.fullmatch(self.commit):
            raise IdentityError("the provenance commit must be a full 40-character Git SHA")
        digests = (
            ("deb", self.deb_sha256),
            ("binary", self.binary_sha256),
            ("payload", self.payload_sha256),
        )
        for name, digest in digests:
            if not _DIGEST.fullmatch(digest):
                raise IdentityError(f"the provenance {name} SHA-256 must be 64 hex characters")
        if not self.version or not self.architecture:
            raise IdentityError("the provenance version and architecture are required")
        if self.run_id is not None and (isinstance(self.run_id, bool) or self.run_id <= 0):
            raise IdentityError("the provenance CI run id must be a positive integer")

    def to_json(self) -> str:
        document: dict[str, str | int | None] = {
            "schema": DEB_SCHEMA,
            "commit": self.commit,
            "runId": self.run_id,
            "version": self.version,
            "architecture": self.architecture,
            "debSha256": self.deb_sha256,
            "binarySha256": self.binary_sha256,
            "payloadSha256": self.payload_sha256,
        }
        return json.dumps(document, indent=2) + "\n"


def _text(document: Mapping[str, object], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str):
        raise IdentityError(f"the provenance field {key!r} must be a string")
    return value


def read_deb_provenance(path: Path) -> DebProvenance:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise IdentityError(f"no provenance manifest at {path}") from None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise IdentityError(f"unreadable provenance manifest: {type(error).__name__}") from None
    if not isinstance(document, dict) or document.get("schema") != DEB_SCHEMA:
        raise IdentityError(f"the provenance manifest is not {DEB_SCHEMA}")
    run_id = document.get("runId")
    if run_id is not None and not isinstance(run_id, int):
        raise IdentityError("the provenance field 'runId' must be an integer or null")
    return DebProvenance(
        commit=_text(document, "commit"),
        version=_text(document, "version"),
        architecture=_text(document, "architecture"),
        deb_sha256=_text(document, "debSha256"),
        binary_sha256=_text(document, "binarySha256"),
        payload_sha256=_text(document, "payloadSha256"),
        run_id=run_id,
    )


def deb_provenance_for(deb: Path, *, commit: str, run_id: int | None) -> DebProvenance:
    """The provenance of `deb`, computed from the package itself and its build commit."""
    fields = deb_control_fields(deb)
    return DebProvenance(
        commit=commit,
        version=fields.get("Version", ""),
        architecture=fields.get("Architecture", ""),
        deb_sha256=sha256_file(deb),
        binary_sha256=deb_member_sha256(deb),
        payload_sha256=deb_payload_sha256(deb),
        run_id=run_id,
    )


def verify_deb(deb: Path, provenance: DebProvenance) -> list[str]:
    """Every way `deb` contradicts `provenance` (empty when it matches)."""
    fields = deb_control_fields(deb)
    problems: list[str] = []
    if sha256_file(deb) != provenance.deb_sha256:
        problems.append("the package SHA-256 differs from its provenance")
    if deb_member_sha256(deb) != provenance.binary_sha256:
        problems.append("the application binary SHA-256 differs from its provenance")
    if deb_payload_sha256(deb) != provenance.payload_sha256:
        problems.append("the package payload (md5sums) SHA-256 differs from its provenance")
    if fields.get("Version") != provenance.version:
        problems.append("the package version differs from its provenance")
    if fields.get("Architecture") != provenance.architecture:
        problems.append("the package architecture differs from its provenance")
    return problems


INSTALLED_BINARY = Path("usr/bin/control-tv")
# dpkg's copy of the installed package's md5sums: every installed file and its MD5.
INSTALLED_PAYLOAD = Path("var/lib/dpkg/info/control-tv.md5sums")
DPKG_STATUS = Path("var/lib/dpkg/status")
INSTALLED_OK = "install ok installed"
_MD5SUMS_LINE = re.compile(r"([0-9a-f]{32})  (\S.*)")


def installed_file_problems(root: Path, md5sums: str) -> list[str]:
    """Every installed file that is missing or differs from dpkg's record of it."""
    problems: list[str] = []
    lines = [line for line in md5sums.splitlines() if line.strip()]
    if not lines:
        return ["dpkg's file list is empty"]
    for line in lines:
        match = _MD5SUMS_LINE.fullmatch(line)
        if match is None or match.group(2).startswith("/") or ".." in Path(match.group(2)).parts:
            problems.append("dpkg's file list is malformed")
            continue
        path = root / match.group(2)
        try:
            digest = hashlib.md5(path.read_bytes(), usedforsecurity=False).hexdigest()
        except FileNotFoundError:
            problems.append(f"missing: {match.group(2)}")
            continue
        except OSError as error:
            problems.append(f"unreadable ({type(error).__name__}): {match.group(2)}")
            continue
        if digest != match.group(1):
            problems.append(f"modified: {match.group(2)}")
    return problems


def dpkg_status(root: Path, package: str = "control-tv") -> str | None:
    """The `Status:` dpkg records for `package` under `root`, or None when it has no entry."""
    try:
        text = (root / DPKG_STATUS).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for stanza in text.split("\n\n"):
        fields = dict(
            line.split(":", 1) for line in stanza.splitlines() if ":" in line and line[0] != " "
        )
        if fields.get("Package", "").strip() == package:
            return fields.get("Status", "").strip() or None
    return None


class Installed(StrEnum):
    ABSENT = "not installed"
    THIS_BUILD = "installed: this build"
    OTHER_BUILD = "installed: another build"
    UNKNOWN = "installed state cannot be established"


@dataclass(frozen=True)
class InstalledState:
    kind: Installed
    detail: str


def installed_state(root: Path, provenance: DebProvenance) -> InstalledState:
    """Whether the system under `root` has no control-tv, this build (binary, dpkg record and
    every installed file), another build, or a state that cannot be read (partial install)."""
    binary, record = root / INSTALLED_BINARY, root / INSTALLED_PAYLOAD
    if not binary.exists() and not record.exists():
        return InstalledState(Installed.ABSENT, "no installed binary and no dpkg record")
    status = dpkg_status(root)
    if status != INSTALLED_OK:
        return InstalledState(
            Installed.UNKNOWN, f"files are present but dpkg reports {status!r}, not installed"
        )
    try:
        binary_sha256 = sha256_file(binary)
        record_bytes = record.read_bytes()
    except (IdentityError, OSError) as error:
        return InstalledState(Installed.UNKNOWN, f"partial or unreadable installation: {error}")
    # The digest of the record's exact bytes, as `deb_payload_sha256` takes it from the package.
    payload_sha256 = hashlib.sha256(record_bytes).hexdigest()
    record_text = record_bytes.decode("utf-8", errors="surrogateescape")
    if binary_sha256 != provenance.binary_sha256 or payload_sha256 != provenance.payload_sha256:
        return InstalledState(
            Installed.OTHER_BUILD,
            f"installed binary {binary_sha256}, payload {payload_sha256}",
        )
    problems = installed_file_problems(root, record_text)
    if problems:
        return InstalledState(Installed.OTHER_BUILD, "; ".join(problems[:5]))
    return InstalledState(Installed.THIS_BUILD, "binary, dpkg record and every file match")


def check_deb(
    deb: Path,
    provenance_path: Path,
    *,
    root: Path,
    expect_commit: str,
    expect_run_id: int,
    require_installed: bool,
) -> tuple[int, list[str]]:
    """The `check-deb` verdict: (exit status, report lines)."""
    try:
        provenance = read_deb_provenance(provenance_path)
        problems = verify_deb(deb, provenance)
    except IdentityError as error:
        return 2, [f"BLOCKED: the build identity cannot be established: {error}"]
    if provenance.commit != expect_commit:
        problems.append("the provenance commit is not the expected one")
    if provenance.run_id != expect_run_id:
        problems.append("the provenance CI run is not the expected one")
    if problems:
        return 1, [f"FAIL: {problem}" for problem in problems]
    lines = [
        f"package: commit {provenance.commit}, run {provenance.run_id}, "
        f"{provenance.version} {provenance.architecture}",
        f"deb {provenance.deb_sha256}, binary {provenance.binary_sha256}, "
        f"payload {provenance.payload_sha256}",
    ]
    state = installed_state(root, provenance)
    lines.append(f"{state.kind.value} ({state.detail})")
    if state.kind is Installed.UNKNOWN:
        return 2, [*lines, "BLOCKED: the installed state cannot be established"]
    if require_installed and state.kind is not Installed.THIS_BUILD:
        return 1, [*lines, "FAIL: the installed package is not this build"]
    return 0, [*lines, "OK: the package matches its provenance"]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    commands = parser.add_subparsers(dest="command", required=True)
    deb = commands.add_parser("deb-provenance", help="write the provenance of a .deb")
    deb.add_argument("--deb", type=Path, required=True)
    deb.add_argument("--commit", required=True)
    deb.add_argument("--run-id", type=int)
    deb.add_argument("--output", type=Path, required=True)
    check = commands.add_parser("check-deb", help="identify a .deb and the installed package")
    check.add_argument("--deb", type=Path, required=True)
    check.add_argument("--provenance", type=Path)
    check.add_argument("--expect-commit", required=True)
    check.add_argument("--expect-run-id", type=int, required=True)
    check.add_argument("--require-installed", action="store_true")
    check.add_argument("--root", type=Path, default=Path("/"))
    arguments = parser.parse_args(argv)
    if arguments.command == "check-deb":
        status, lines = check_deb(
            arguments.deb,
            arguments.provenance or arguments.deb.parent / "provenance.json",
            root=arguments.root,
            expect_commit=arguments.expect_commit,
            expect_run_id=arguments.expect_run_id,
            require_installed=arguments.require_installed,
        )
        print("\n".join(lines))
        return status
    try:
        if arguments.output.exists():
            raise IdentityError(f"{arguments.output} exists; it is never overwritten")
        provenance = deb_provenance_for(
            arguments.deb, commit=arguments.commit, run_id=arguments.run_id
        )
        arguments.output.write_text(provenance.to_json(), encoding="utf-8")
    except IdentityError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(provenance.to_json(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
