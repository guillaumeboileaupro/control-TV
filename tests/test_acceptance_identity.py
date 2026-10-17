"""Build identity for the hardware acceptance: package parsing and provenance manifests."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from deb_fixtures import BRIDGE, COMMIT, make_deb, md5sums, payload_sha256, sha256

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


# The installed package (check-deb) ---------------------------------------------------------


def built(tmp_path: Path, *, binary: bytes = b"build A", bridge: bytes = b"bridge") -> Path:
    deb = make_deb(tmp_path / "p.deb", binary=binary, bridge=bridge)
    (tmp_path / "provenance.json").write_text(
        identity.deb_provenance_for(deb, commit=COMMIT, run_id=7).to_json(), encoding="utf-8"
    )
    return deb


def install(root: Path, *, binary: bytes = b"build A", bridge: bytes = b"bridge") -> Path:
    for relative, content in (
        ("usr/bin/control-tv", binary),
        (BRIDGE, bridge),
        ("var/lib/dpkg/info/control-tv.md5sums", md5sums(binary, bridge)),
        ("var/lib/dpkg/status", b"Package: control-tv\nStatus: install ok installed\n"),
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return root


def check(deb: Path, root: Path, **options: object) -> tuple[int, list[str]]:
    status, lines = identity.check_deb(
        deb,
        deb.parent / "provenance.json",
        root=root,
        expect_commit=str(options.get("commit", COMMIT)),
        expect_run_id=int(str(options.get("run", 7))),
        require_installed=bool(options.get("require")),
    )
    return int(status), [str(line) for line in lines]


def test_a_clean_host_is_not_installed_rather_than_an_error(tmp_path: Path) -> None:
    """Regression: the protocol's snippet raised IdentityError when control-tv was absent."""
    deb = built(tmp_path)
    clean = tmp_path / "clean-root"
    clean.mkdir()

    status, lines = check(deb, clean)

    assert status == 0 and "not installed (no installed binary and no dpkg record)" in lines
    assert check(deb, clean, require=True)[0] == 1


def test_the_installed_package_is_this_build_only_when_every_file_matches(tmp_path: Path) -> None:
    deb = built(tmp_path)
    root = install(tmp_path / "root")

    status, lines = check(deb, root, commit=COMMIT, run=7, require=True)
    assert status == 0 and any(line.startswith("installed: this build") for line in lines)

    (root / BRIDGE).write_bytes(b"tampered bridge")  # same binary, same dpkg record
    status, lines = check(deb, root, require=True)
    assert status == 1 and any(f"modified: {BRIDGE}" in line for line in lines)


def test_another_installed_build_and_a_partial_installation_are_told_apart(
    tmp_path: Path,
) -> None:
    deb = built(tmp_path)
    other = install(tmp_path / "other", bridge=b"another bridge")
    status, lines = check(deb, other, require=True)
    assert status == 1 and any(line.startswith("installed: another build") for line in lines)
    assert check(deb, other)[0] == 0  # before installing, another build is only reported

    partial = tmp_path / "partial"
    (partial / "usr/bin").mkdir(parents=True)
    (partial / "usr/bin/control-tv").write_bytes(b"build A")
    status, lines = check(deb, partial)
    assert status == 2 and lines[-1] == "BLOCKED: the installed state cannot be established"


def test_check_deb_keeps_blocked_and_fail_apart(tmp_path: Path) -> None:
    deb = built(tmp_path)
    root = tmp_path / "root"
    root.mkdir()

    assert check(deb, root, commit="f" * 40)[0] == 1  # contradiction: FAIL
    assert check(deb, root, run=8)[0] == 1
    (tmp_path / "provenance.json").unlink()
    status, lines = check(deb, root)
    assert status == 2 and lines[0].startswith("BLOCKED: the build identity cannot be established")


def test_installed_files_are_checked_against_the_dpkg_record(tmp_path: Path) -> None:
    root = install(tmp_path / "root")
    record = (root / "var/lib/dpkg/info/control-tv.md5sums").read_text()

    assert identity.installed_file_problems(root, record) == []
    assert identity.installed_file_problems(root, "") == ["dpkg's file list is empty"]
    assert identity.installed_file_problems(root, "zz  usr/x\n") == [
        "dpkg's file list is malformed"
    ]
    assert identity.installed_file_problems(root, f"{'0' * 32}  ../etc/passwd\n") == [
        "dpkg's file list is malformed"
    ]
    (root / BRIDGE).unlink()
    assert identity.installed_file_problems(root, record) == [f"missing: {BRIDGE}"]
    (root / BRIDGE).mkdir()
    assert identity.installed_file_problems(root, record) == [
        f"unreadable (IsADirectoryError): {BRIDGE}"
    ]


def test_the_check_deb_command_prints_and_exits_with_the_verdict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    deb = built(tmp_path)
    root = install(tmp_path / "root")

    assert (
        identity.main(
            [
                "check-deb",
                "--deb",
                str(deb),
                "--root",
                str(root),
                "--require-installed",
                "--expect-commit",
                COMMIT,
                "--expect-run-id",
                "7",
            ]
        )
        == 0
    )
    assert "OK: the package matches its provenance" in capsys.readouterr().out


# Self-review follow-ups (d47cf5e) ----------------------------------------------------------


def dpkg_status(root: Path, status: str) -> None:
    path = root / "var/lib/dpkg/status"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "Package: other\nStatus: install ok installed\n\n"
        f"Package: control-tv\nStatus: {status}\nVersion: 0.1.0\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    "status", ["install ok half-configured", "install ok unpacked", "deinstall ok installed"]
)
def test_a_package_dpkg_has_not_finished_installing_is_never_this_build(
    tmp_path: Path, status: str
) -> None:
    """Regression: the files matched, so a half-configured package passed --require-installed."""
    deb = built(tmp_path)
    root = install(tmp_path / "root")
    dpkg_status(root, status)

    code, lines = check(deb, root, commit=COMMIT, run=7, require=True)

    assert code == 2 and any(f"dpkg reports {status!r}" in line for line in lines)


def test_a_package_unknown_to_dpkg_while_its_files_exist_is_blocked(tmp_path: Path) -> None:
    deb = built(tmp_path)
    root = install(tmp_path / "root")
    (root / "var/lib/dpkg").mkdir(parents=True, exist_ok=True)
    (root / "var/lib/dpkg/status").write_text("Package: other\nStatus: install ok installed\n")

    code, lines = check(deb, root, commit=COMMIT, run=7)
    assert code == 2 and any("dpkg reports None" in line for line in lines)


def test_the_payload_digest_is_taken_from_the_record_bytes(tmp_path: Path) -> None:
    deb = built(tmp_path)
    root = install(tmp_path / "root")
    record = root / "var/lib/dpkg/info/control-tv.md5sums"
    record.write_bytes(record.read_bytes().replace(b"\n", b"\r\n"))  # same text, other bytes

    code, lines = check(deb, root, commit=COMMIT, run=7, require=True)
    assert code == 1 and any(line.startswith("installed: another build") for line in lines)


def test_the_check_deb_command_requires_the_expected_commit_and_run(tmp_path: Path) -> None:
    """Regression: without them, check-deb accepted a build of any commit and run."""
    deb = built(tmp_path)
    with pytest.raises(SystemExit):
        identity.main(["check-deb", "--deb", str(deb)])
    with pytest.raises(SystemExit):
        identity.main(["check-deb", "--deb", str(deb), "--expect-commit", COMMIT])
    assert (
        identity.main(
            [
                "check-deb",
                "--deb",
                str(deb),
                "--root",
                str(tmp_path),
                "--expect-commit",
                "f" * 40,
                "--expect-run-id",
                "7",
            ]
        )
        == 1
    )


def test_files_without_any_dpkg_status_are_blocked(tmp_path: Path) -> None:
    deb = built(tmp_path)
    root = install(tmp_path / "root")
    (root / "var/lib/dpkg/status").unlink()

    code, lines = check(deb, root, require=True)
    assert code == 2 and any("dpkg reports None" in line for line in lines)
