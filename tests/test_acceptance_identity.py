"""Build identity for the hardware acceptance: package parsing and provenance manifests."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from deb_fixtures import COMMIT, make_deb, payload_sha256, sha256

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def load(name: str) -> ModuleType:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


identity = load("acceptance_identity")


@pytest.mark.parametrize("compression", ["gz", "xz"])
def test_the_binary_digest_is_read_from_the_package_data(tmp_path: Path, compression: str) -> None:
    deb = make_deb(tmp_path / "p.deb", binary=b"build A", compression=compression)

    assert identity.deb_member_sha256(deb) == sha256(b"build A")
    fields = identity.deb_control_fields(deb)
    assert fields["Version"] == "0.1.0" and fields["Architecture"] == "amd64"


def test_the_payload_digest_covers_files_the_binary_does_not(tmp_path: Path) -> None:
    # The application binary is reproducible: a bridge-only change keeps it identical.
    first = make_deb(tmp_path / "a.deb", binary=b"same", bridge=b"bridge 1")
    second = make_deb(tmp_path / "b.deb", binary=b"same", bridge=b"bridge 2")

    assert identity.deb_member_sha256(first) == identity.deb_member_sha256(second)
    assert identity.deb_payload_sha256(first) == payload_sha256(b"same", b"bridge 1")
    assert identity.deb_payload_sha256(first) != identity.deb_payload_sha256(second)


def test_two_builds_of_the_same_version_are_told_apart(tmp_path: Path) -> None:
    first = make_deb(tmp_path / "a.deb", binary=b"build A")
    second = make_deb(tmp_path / "b.deb", binary=b"build B")

    assert identity.deb_control_fields(first) == identity.deb_control_fields(second)
    assert identity.deb_member_sha256(first) != identity.deb_member_sha256(second)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"not an archive", "not a Debian package"),
        (b"!<arch>\ndebian-binary/  0           0     0     100644  9999999   `\n", "truncated"),
        (b"!<arch>\ndebian-binary/  0           0     0     100644  xx        `\n", "malformed"),
        (b"!<arch>\n", "no single data.tar"),
    ],
    ids=["not-ar", "truncated", "bad-size", "no-data"],
)
def test_a_damaged_package_has_no_identity(tmp_path: Path, content: bytes, message: str) -> None:
    deb = tmp_path / "bad.deb"
    deb.write_bytes(content)

    with pytest.raises(identity.IdentityError, match=message):
        identity.deb_member_sha256(deb)


def test_a_package_without_the_application_or_unreadable_has_no_identity(tmp_path: Path) -> None:
    deb = make_deb(tmp_path / "p.deb")

    with pytest.raises(identity.IdentityError, match="no regular file usr/bin/other"):
        identity.deb_member_sha256(deb, "usr/bin/other")
    with pytest.raises(identity.IdentityError, match="cannot read"):
        identity.deb_member_sha256(tmp_path / "missing.deb")
    with pytest.raises(identity.IdentityError, match="cannot read"):
        identity.sha256_file(tmp_path / "missing.deb")


def test_a_corrupt_data_archive_has_no_identity(tmp_path: Path) -> None:
    deb = tmp_path / "corrupt.deb"
    member = b"\x1f\x8b garbage"
    header = f"{'data.tar.gz/':<16}{0:<12}{0:<6}{0:<6}{'100644':<8}{len(member):<10}`\n"
    deb.write_bytes(b"!<arch>\n" + header.encode() + member)

    with pytest.raises(identity.IdentityError, match=r"cannot read data\.tar\.gz"):
        identity.deb_member_sha256(deb)


def test_a_provenance_round_trips_and_matches_its_package(tmp_path: Path) -> None:
    deb = make_deb(tmp_path / "p.deb", binary=b"build A")
    provenance = identity.deb_provenance_for(deb, commit=COMMIT, run_id=39034444658)
    manifest = tmp_path / "provenance.json"
    manifest.write_text(provenance.to_json(), encoding="utf-8")

    read = identity.read_deb_provenance(manifest)

    assert read == provenance
    assert read.binary_sha256 == sha256(b"build A")
    assert read.deb_sha256 == sha256(deb.read_bytes())
    assert identity.verify_deb(deb, read) == []


def test_every_contradiction_with_the_provenance_is_reported(tmp_path: Path) -> None:
    built = make_deb(tmp_path / "p.deb", binary=b"build A")
    provenance = identity.deb_provenance_for(built, commit=COMMIT, run_id=None)
    other = make_deb(tmp_path / "o.deb", binary=b"build B", version="0.2.0", architecture="arm64")

    problems = identity.verify_deb(other, provenance)

    assert problems == [
        "the package SHA-256 differs from its provenance",
        "the application binary SHA-256 differs from its provenance",
        "the package payload (md5sums) SHA-256 differs from its provenance",
        "the package version differs from its provenance",
        "the package architecture differs from its provenance",
    ]


GOOD = {
    "schema": "control-tv-deb-provenance/2",
    "commit": COMMIT,
    "runId": 1,
    "version": "0.1.0",
    "architecture": "amd64",
    "debSha256": "a" * 64,
    "binarySha256": "b" * 64,
    "payloadSha256": "c" * 64,
}


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"schema": "other/1"}, "is not control-tv-deb-provenance/2"),
        ({"schema": "control-tv-deb-provenance/1"}, "is not control-tv-deb-provenance/2"),
        ({"payloadSha256": "c" * 10}, "payload SHA-256"),
        ({"commit": "de31c94"}, "full 40-character"),
        ({"commit": "G" * 40}, "full 40-character"),
        ({"debSha256": "a" * 63}, "deb SHA-256"),
        ({"binarySha256": None}, "'binarySha256' must be a string"),
        ({"version": ""}, "version and architecture"),
        ({"runId": "1"}, "'runId' must be an integer"),
        ({"runId": 0}, "positive integer"),
        ({"runId": True}, "positive integer"),
    ],
)
def test_an_invalid_provenance_is_refused(
    tmp_path: Path, change: dict[str, object], message: str
) -> None:
    manifest = tmp_path / "provenance.json"
    manifest.write_text(json.dumps({**GOOD, **change}), encoding="utf-8")

    with pytest.raises(identity.IdentityError, match=message):
        identity.read_deb_provenance(manifest)


def test_a_missing_or_unreadable_provenance_is_refused(tmp_path: Path) -> None:
    with pytest.raises(identity.IdentityError, match="no provenance manifest"):
        identity.read_deb_provenance(tmp_path / "absent.json")
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    with pytest.raises(identity.IdentityError, match="unreadable provenance"):
        identity.read_deb_provenance(broken)
    listed = tmp_path / "list.json"
    listed.write_text("[]", encoding="utf-8")
    with pytest.raises(identity.IdentityError, match="is not control-tv-deb-provenance"):
        identity.read_deb_provenance(listed)


def test_the_command_writes_a_provenance_and_never_overwrites_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    deb = make_deb(tmp_path / "p.deb")
    output = tmp_path / "provenance.json"
    arguments = ["deb-provenance", "--deb", str(deb), "--commit", COMMIT, "--output", str(output)]

    assert identity.main([*arguments, "--run-id", "7"]) == 0
    assert identity.read_deb_provenance(output).run_id == 7
    before = output.read_text(encoding="utf-8")

    assert identity.main(arguments) == 1
    assert "never overwritten" in capsys.readouterr().err
    assert output.read_text(encoding="utf-8") == before


def test_the_command_refuses_a_short_commit(tmp_path: Path) -> None:
    deb = make_deb(tmp_path / "p.deb")
    output = tmp_path / "provenance.json"

    assert (
        identity.main(
            ["deb-provenance", "--deb", str(deb), "--commit", "de31c94", "--output", str(output)]
        )
        == 1
    )
    assert not output.exists()
