"""Which build a hardware acceptance tests, provably.

Every Debian package of control-TV is version 0.1.0, so a version never identifies a build. A
build is identified by a provenance manifest that binds, for one package:

- the Git commit it was built from (and the CI run, when it comes from CI);
- the version and architecture read from the package;
- the SHA-256 of the `.deb` file;
- the SHA-256 of the application binary inside it (`usr/bin/control-tv`), which is what an
  installed system can be compared with.

The manifest is written by CI next to the package (`provenance.json`) or, for an older CI
artifact, by `deb-provenance` below from the run's recorded commit. The acceptance runner
recomputes both digests itself and refuses a real validation when they cannot be established
or do not match.

Usage:
    python3 scripts/acceptance_identity.py deb-provenance --deb PATH --commit SHA \
        [--run-id ID] --output PATH
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
from pathlib import Path

DEB_SCHEMA = "control-tv-deb-provenance/1"
APPLICATION_MEMBER = "usr/bin/control-tv"

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
    run_id: int | None = None

    def __post_init__(self) -> None:
        if not _COMMIT.fullmatch(self.commit):
            raise IdentityError("the provenance commit must be a full 40-character Git SHA")
        for name, digest in (("deb", self.deb_sha256), ("binary", self.binary_sha256)):
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
    if fields.get("Version") != provenance.version:
        problems.append("the package version differs from its provenance")
    if fields.get("Architecture") != provenance.architecture:
        problems.append("the package architecture differs from its provenance")
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    commands = parser.add_subparsers(dest="command", required=True)
    deb = commands.add_parser("deb-provenance", help="write the provenance of a .deb")
    deb.add_argument("--deb", type=Path, required=True)
    deb.add_argument("--commit", required=True)
    deb.add_argument("--run-id", type=int)
    deb.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)
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
