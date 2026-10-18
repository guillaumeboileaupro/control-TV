"""The stable Android test signing identity: parsing, policy, key creation and guard rails."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
COMMIT = "de31c94dceb290f98bc8b0784ac17d3fd3d78727"
PINNED = "9193947fed7e8316daab6299a804018a22272e11deed10df086d6b2347921a78"
OTHER = "83aede743d874f0748d929660f0b9609b8e91ec19879ce63fc66fb5fb082880d"
PASSWORD = "a-test-password-not-a-secret"


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


signing = load("android_test_signing")


def apksigner_output(*digests: str) -> str:
    lines = []
    for number, digest in enumerate(digests, start=1):
        lines += [
            f"Signer #{number} certificate DN: C=US, O=Android, CN=Android Debug",
            f"Signer #{number} certificate SHA-256 digest: {digest}",
            f"Signer #{number} certificate SHA-1 digest: {'0' * 40}",
        ]
    return "\n".join(lines) + "\n"


def test_signer_digests_are_read_from_apksigner() -> None:
    assert signing.apksigner_certificates(apksigner_output(PINNED.upper())) == (PINNED,)
    assert signing.apksigner_certificates(apksigner_output(PINNED, OTHER)) == (PINNED, OTHER)
    assert signing.apksigner_certificates("Verified using v2 scheme: true\n") == ()
    ranged = (
        "Signer (minSdkVersion=24, maxSdkVersion=32) certificate DN: CN=Android Debug\n"
        f"Signer (minSdkVersion=24, maxSdkVersion=32) certificate SHA-256 digest: {PINNED}\n"
    )
    assert signing.apksigner_certificates(ranged) == (PINNED,)
    # The form printed by the CI runner's apksigner (run 39049338669), one line per scheme.
    per_scheme = (
        "V2 Signer: certificate DN: C=US, O=Android, CN=Android Debug\n"
        f"V2 Signer: certificate SHA-256 digest: {PINNED}\n"
        f"V3 Signer: certificate SHA-256 digest: {PINNED}\n"
        "V2 Signer: certificate SHA-1 digest: e98d07733f2dbe5f231ee4ebdecb503e9f1ca848\n"
    )
    assert signing.apksigner_certificates(per_scheme) == (PINNED,)
    rotated = (
        f"V2 Signer: certificate SHA-256 digest: {PINNED}\n"
        f"V3 Signer: certificate SHA-256 digest: {OTHER}\n"
    )
    assert signing.apksigner_certificates(rotated) == (PINNED, OTHER)


def test_the_certificate_digest_is_read_from_keytool() -> None:
    fingerprint = ":".join(PINNED[i : i + 2].upper() for i in range(0, 64, 2))
    output = f"Certificate fingerprints:\n\t SHA1: AA:BB\n\t SHA256: {fingerprint}\n"

    assert signing.keytool_certificate(output) == PINNED
    with pytest.raises(signing.SigningError, match="exactly one"):
        signing.keytool_certificate("Empreintes du certificat :\n SHA 256 : AA\n")
    with pytest.raises(signing.SigningError, match="exactly one"):
        signing.keytool_certificate(output + output)


def test_the_pin_file_holds_one_digest(tmp_path: Path) -> None:
    pin = tmp_path / "pin"
    assert signing.read_pin(pin) is None  # nothing pinned yet

    pin.write_text(f"# the test identity\n{PINNED}\n", encoding="ascii")
    assert signing.read_pin(pin) == PINNED

    for content in (PINNED.upper(), f"{PINNED}\n{OTHER}\n", "short\n", ""):
        pin.write_text(content, encoding="ascii")
        with pytest.raises(signing.SigningError, match="one lower-case"):
            signing.read_pin(pin)
    pin.write_bytes(b"\xff")
    with pytest.raises(signing.SigningError, match="unreadable"):
        signing.read_pin(pin)


def test_the_repository_pins_no_identity_until_the_owner_creates_one() -> None:
    assert not signing.PIN.exists() or signing.read_pin(signing.PIN) is not None


def test_the_pinned_key_is_accepted() -> None:
    verdict = signing.judge((PINNED,), PINNED, stable_key_used=True, require_stable=True)

    assert verdict.identity is signing.Identity.PINNED and verdict.certificate == PINNED


@pytest.mark.parametrize(
    ("certificates", "pin", "stable", "require", "message"),
    [
        ((OTHER,), PINNED, True, False, "another key than the pinned"),
        ((PINNED,), None, True, False, "no certificate is pinned"),
        ((OTHER,), PINNED, False, True, "used an ephemeral debug key"),
        ((), PINNED, True, False, "exactly one signer, found 0"),
        ((PINNED, OTHER), PINNED, True, False, "exactly one signer, found 2"),
    ],
    ids=["replaced-key", "unpinned-stable", "ephemeral-on-push", "unsigned", "two-signers"],
)
def test_a_signature_that_is_not_the_expected_identity_is_refused(
    certificates: tuple[str, ...], pin: str | None, stable: bool, require: bool, message: str
) -> None:
    with pytest.raises(signing.SigningError, match=message):
        signing.judge(certificates, pin, stable_key_used=stable, require_stable=require)


@pytest.mark.parametrize(
    ("pin", "note"), [(None, "needs an uninstall"), (PINNED, "NOT the pinned test identity")]
)
def test_an_ephemeral_build_is_allowed_but_named(pin: str | None, note: str) -> None:
    verdict = signing.judge((OTHER,), pin, stable_key_used=False, require_stable=False)

    assert verdict.identity is signing.Identity.EPHEMERAL and note in verdict.message


def test_the_apk_provenance_binds_commit_digest_and_signer(tmp_path: Path) -> None:
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"apk")
    verdict = signing.judge((PINNED,), PINNED, stable_key_used=True, require_stable=False)

    document = signing.apk_provenance(apk, verdict, commit=COMMIT, run_id=7)

    assert document["commit"] == COMMIT and document["runId"] == 7
    assert document["signerCertificateSha256"] == PINNED
    assert document["signingIdentity"] == "pinned-test-key"
    assert document["apkSha256"] == hashlib.sha256(b"apk").hexdigest()
    with pytest.raises(signing.SigningError, match="40-character"):
        signing.apk_provenance(apk, verdict, commit="de31c94", run_id=None)


@pytest.mark.parametrize("run_id", [None, 0, -1, True])
def test_apk_provenance_requires_a_positive_ci_run(tmp_path: Path, run_id: int | None) -> None:
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"apk")
    verdict = signing.Verdict(signing.Identity.PINNED, PINNED, "test")

    with pytest.raises(signing.SigningError, match="positive integer"):
        signing.apk_provenance(apk, verdict, commit=COMMIT, run_id=run_id)


class FakeKeytool:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, ...]] = []
        self.environs: list[dict[str, str]] = []

    def __call__(
        self, args: Sequence[str], environ: Mapping[str, str]
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(tuple(args))
        self.environs.append(dict(environ))
        if "-genkeypair" in args:
            keystore = Path(args[args.index("-keystore") + 1])
            keystore.write_bytes(b"keystore")
            return subprocess.CompletedProcess(args, 1 if self.fail else 0, "", "")
        fingerprint = ":".join(PINNED[i : i + 2].upper() for i in range(0, 64, 2))
        return subprocess.CompletedProcess(args, 0, f"\t SHA256: {fingerprint}\n", "")


def test_a_key_is_created_with_the_password_from_the_environment_only(tmp_path: Path) -> None:
    keystore = tmp_path / "keys" / "test.p12"
    keytool = FakeKeytool()

    digest = signing.generate(keystore, {signing.PASSWORD_ENV: PASSWORD}, keytool)

    assert digest == PINNED
    assert stat.S_IMODE(keystore.stat().st_mode) == 0o600
    flattened = [part for call in keytool.calls for part in call]
    assert PASSWORD not in flattened  # never on a command line
    assert "-storepass:env" in flattened and signing.PASSWORD_ENV in flattened
    assert "-J-Duser.language=en" in flattened  # read back whatever the locale
    assert all(env[signing.PASSWORD_ENV] == PASSWORD for env in keytool.environs)


def test_an_existing_key_is_never_replaced(tmp_path: Path) -> None:
    keystore = tmp_path / "test.p12"
    keystore.write_bytes(b"the owner's key")
    keytool = FakeKeytool()

    with pytest.raises(signing.SigningError, match="never replaced"):
        signing.generate(keystore, {signing.PASSWORD_ENV: PASSWORD}, keytool)

    assert keystore.read_bytes() == b"the owner's key" and keytool.calls == []


@pytest.mark.parametrize("environ", [{}, {"CONTROL_TV_ANDROID_KEYSTORE_PASSWORD": "short"}])
def test_a_missing_or_weak_password_creates_nothing(
    tmp_path: Path, environ: dict[str, str]
) -> None:
    keytool = FakeKeytool()

    with pytest.raises(signing.SigningError, match="at least 12"):
        signing.generate(tmp_path / "test.p12", environ, keytool)
    assert keytool.calls == [] and not (tmp_path / "test.p12").exists()


def test_a_keytool_failure_is_reported_without_its_output(tmp_path: Path) -> None:
    with pytest.raises(signing.SigningError, match="not shown"):
        signing.generate(
            tmp_path / "t.p12", {signing.PASSWORD_ENV: PASSWORD}, FakeKeytool(fail=True)
        )


@pytest.mark.skipif(shutil.which("keytool") is None, reason="needs a JDK keytool")
def test_a_real_key_is_created_and_read_back(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    keystore = tmp_path / "test.p12"

    code = signing.main(
        ["generate", "--keystore", str(keystore)], {**os.environ, signing.PASSWORD_ENV: PASSWORD}
    )

    output = capsys.readouterr()
    assert code == 0 and keystore.is_file()
    assert "certificate SHA-256: " in output.out
    assert PASSWORD not in output.out + output.err


def fake_apksigner(tmp_path: Path, *digests: str, code: int = 0) -> Path:
    script = tmp_path / "apksigner"
    script.write_text(
        f"#!{sys.executable}\nimport sys\nsys.stdout.write({apksigner_output(*digests)!r})\n"
        f"raise SystemExit({code})\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def test_verify_apk_writes_the_provenance(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"apk")
    pin = tmp_path / "pin"
    pin.write_text(PINNED + "\n", encoding="ascii")
    output = tmp_path / "apk-provenance.json"
    arguments = [
        "verify-apk",
        "--apk",
        str(apk),
        "--apksigner",
        str(fake_apksigner(tmp_path, PINNED)),
    ]

    code = signing.main(
        [
            *arguments,
            "--pin",
            str(pin),
            "--stable-key-used",
            "--require-stable",
            "--commit",
            COMMIT,
            "--run-id",
            "3",
            "--provenance-output",
            str(output),
        ],
        {},
    )

    assert code == 0 and "signing identity: pinned-test-key" in capsys.readouterr().out
    assert json.loads(output.read_text(encoding="utf-8"))["signingIdentity"] == "pinned-test-key"


def test_verify_apk_refuses_a_replaced_key_and_a_rejected_signature(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"apk")
    pin = tmp_path / "pin"
    pin.write_text(PINNED + "\n", encoding="ascii")
    replaced = fake_apksigner(tmp_path, OTHER)

    assert (
        signing.main(
            [
                "verify-apk",
                "--apk",
                str(apk),
                "--apksigner",
                str(replaced),
                "--pin",
                str(pin),
                "--stable-key-used",
            ],
            {},
        )
        == 1
    )
    assert "another key than the pinned" in capsys.readouterr().err

    silent = tmp_path / "silent-apksigner"
    silent.write_text(
        f"#!{sys.executable}\nprint('Signer #1 certificate DN: CN=x')\n", encoding="utf-8"
    )
    silent.chmod(0o755)
    assert signing.main(["verify-apk", "--apk", str(apk), "--apksigner", str(silent)], {}) == 1
    assert "no signer certificate SHA-256 line; its certificate lines: Signer #1" in (
        capsys.readouterr().err
    )

    rejected = fake_apksigner(tmp_path, PINNED, code=1)
    assert signing.main(["verify-apk", "--apk", str(apk), "--apksigner", str(rejected)], {}) == 1
    assert "rejected the APK signature" in capsys.readouterr().err

    assert (
        signing.main(
            [
                "verify-apk",
                "--apk",
                str(apk),
                "--apksigner",
                str(fake_apksigner(tmp_path, OTHER)),
                "--pin",
                str(tmp_path / "absent"),
                "--provenance-output",
                str(tmp_path / "p.json"),
            ],
            {},
        )
        == 1
    )
    assert "needs --commit" in capsys.readouterr().err


# Guard rails --------------------------------------------------------------------------------


def test_no_keystore_or_private_key_is_tracked_by_git() -> None:
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    forbidden = (".jks", ".keystore", ".p12", ".pfx", ".pem", ".key")
    assert [path for path in tracked if path.lower().endswith(forbidden)] == []


def test_gradle_signs_debug_builds_only_from_the_environment() -> None:
    gradle = (ROOT / "src-tauri/gen/android/app/build.gradle.kts").read_text(encoding="utf-8")

    assert 'System.getenv("CONTROL_TV_ANDROID_KEYSTORE")' in gradle
    assert 'System.getenv("CONTROL_TV_ANDROID_KEYSTORE_PASSWORD")' in gradle
    assert 'keyAlias = "control-tv-test"' in gradle
    assert 'signingConfigs.findByName("controlTvTest")?.let { signingConfig = it }' in gradle
    assert 'storePassword = "' not in gradle  # no literal password


def test_ci_never_prints_the_signing_secrets() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    build = workflow[workflow.index("- name: Build the arm64 debug APK") :]
    build = build[: build.index("- name: Run the Android JVM unit tests")]

    assert "secrets.CONTROL_TV_ANDROID_TEST_KEYSTORE_B64" in build
    assert "umask 077" in build and "trap 'rm -f \"$key\"' EXIT" in build
    for leak in (
        "echo $KEYSTORE_B64",
        'echo "$KEYSTORE_B64"',
        "echo $CONTROL_TV_ANDROID_KEYSTORE_PASSWORD",
        'echo "$CONTROL_TV_ANDROID_KEYSTORE_PASSWORD"',
        "set -x",
        "GITHUB_ENV",
    ):
        assert leak not in build
    record = workflow[workflow.index("- name: Record the APK checksum and signing identity") :]
    assert "set -o pipefail" in record[: record.index("- name: Upload the debug APK")]
    # Only the build step sees the secrets: no job- or workflow-level exposure.
    assert workflow.count("secrets.CONTROL_TV_ANDROID_TEST_KEYSTORE_PASSWORD") == 1


# Acceptance: the APK is exactly the expected CI build (check-apk) -------------------------


def provenance_for(
    apk: Path, *, signer: str = PINNED, identity: str = "pinned-test-key"
) -> dict[str, object]:
    return {
        "schema": "control-tv-apk-provenance/1",
        "commit": COMMIT,
        "runId": 39034444658,
        "apkSha256": hashlib.sha256(apk.read_bytes()).hexdigest(),
        "signerCertificateSha256": signer,
        "signingIdentity": identity,
    }


def checked(
    tmp_path: Path,
    *,
    document: dict[str, object] | None = None,
    signer: str = PINNED,
    commit: str = COMMIT,
    run_id: int | None = 39034444658,
    published: str = hashlib.sha256(b"the APK of de31c94").hexdigest(),
    apk_bytes: bytes = b"the APK of de31c94",
) -> tuple[int, list[str]]:
    apk = tmp_path / "app-universal-debug.apk"
    apk.write_bytes(b"the APK of de31c94")
    pin = tmp_path / "pin"
    pin.write_text(PINNED + "\n", encoding="ascii")
    (tmp_path / "apk-provenance.json").write_text(
        json.dumps(document if document is not None else provenance_for(apk)), encoding="utf-8"
    )
    apk.write_bytes(apk_bytes)  # what is actually on disk at the recipe
    status, lines = signing.check_apk(
        apk,
        fake_apksigner(tmp_path, signer),
        tmp_path / "apk-provenance.json",
        pin,
        expect_commit=commit,
        expect_run_id=run_id,
        expect_apk_sha256=published,
        environ={},
    )
    return int(status), [str(line) for line in lines]


def test_check_apk_passes_only_for_the_expected_ci_build(tmp_path: Path) -> None:
    status, lines = checked(tmp_path)
    assert status == 0 and lines[-1].startswith("OK:")


def test_an_apk_that_its_provenance_does_not_name_is_refused(tmp_path: Path) -> None:
    """Regression: verify-apk checked only the signer, so a stale or swapped APK signed with the
    same key passed and the results would be recorded against the wrong commit and run."""
    status, lines = checked(tmp_path, apk_bytes=b"another build, same signing key")
    assert (
        status == 1 and "FAIL: the APK SHA-256 differs from its provenance (another APK)" in lines
    )


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"commit": "f" * 40}, "commit is not the expected one"),
        ({"run_id": 1}, "CI run is not the expected one"),
        ({"published": "0" * 64}, "differs from the one the CI run published"),
        ({"signer": OTHER}, "signature: the APK is signed by another key"),
    ],
    ids=["commit", "run", "published-digest", "signer"],
)
def test_check_apk_fails_on_every_contradiction(
    tmp_path: Path, options: dict[str, object], message: str
) -> None:
    status, lines = checked(tmp_path, **options)  # type: ignore[arg-type]
    assert status == 1 and any(message in line for line in lines)


def test_an_ephemeral_build_must_match_its_recorded_signer(tmp_path: Path) -> None:
    apk = tmp_path / "x.apk"
    apk.write_bytes(b"the APK of de31c94")
    document = provenance_for(apk, signer=PINNED, identity="ephemeral-debug-key")
    assert checked(tmp_path, document=document, signer=PINNED)[0] == 0
    status, lines = checked(tmp_path, document=document, signer=OTHER)
    assert status == 1 and "FAIL: the APK signer differs from its provenance" in lines


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"schema": "other/1"}, "is not control-tv-apk-provenance/1"),
        ({"commit": "de31c94"}, "full 40-character"),
        ({"runId": "1"}, "positive integer"),
        ({"apkSha256": "short"}, "apkSha256 must be a SHA-256"),
        ({"signerCertificateSha256": None}, "signerCertificateSha256 must be a SHA-256"),
        ({"signingIdentity": "release-key"}, "unrecognized"),
    ],
)
def test_an_unusable_provenance_blocks_rather_than_fails(
    tmp_path: Path, change: dict[str, object], message: str
) -> None:
    apk = tmp_path / "x.apk"
    apk.write_bytes(b"the APK of de31c94")
    status, lines = checked(tmp_path, document={**provenance_for(apk), **change})
    assert status == 2 and message in lines[0]


def test_a_missing_provenance_apk_or_apksigner_blocks(tmp_path: Path) -> None:
    apk = tmp_path / "x.apk"
    apk.write_bytes(b"apk")
    pin = tmp_path / "absent-pin"
    common: dict[str, Any] = {
        "expect_commit": COMMIT,
        "expect_run_id": 39034444658,
        "expect_apk_sha256": hashlib.sha256(b"apk").hexdigest(),
        "environ": {},
    }

    status, lines = signing.check_apk(apk, Path("apksigner"), tmp_path / "none.json", pin, **common)
    assert status == 2 and "no APK provenance" in lines[0]

    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    assert signing.check_apk(apk, Path("x"), tmp_path / "broken.json", pin, **common)[0] == 2

    ephemeral = provenance_for(apk, identity="ephemeral-debug-key")
    (tmp_path / "p.json").write_text(json.dumps(ephemeral), encoding="utf-8")
    status, lines = signing.check_apk(
        tmp_path / "gone.apk", Path("x"), tmp_path / "p.json", pin, **common
    )
    assert status == 2 and "cannot be read" in lines[0]
    missing_tool = tmp_path / "no" / "apksigner-not-installed"
    status, lines = signing.check_apk(apk, missing_tool, tmp_path / "p.json", pin, **common)
    assert status == 2 and "apksigner cannot be run" in lines[0]


def test_the_check_apk_and_write_apk_provenance_commands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"apk")
    tool = fake_apksigner(tmp_path, OTHER)
    record = tmp_path / "apk-provenance.json"
    write = ["write-apk-provenance", "--apk", str(apk), "--apksigner", str(tool)]
    write += ["--pin", str(tmp_path / "no-pin"), "--commit", COMMIT, "--run-id", "5"]

    assert signing.main([*write, "--output", str(record)], {}) == 0
    assert json.loads(record.read_text())["signingIdentity"] == "ephemeral-debug-key"
    assert signing.main([*write, "--output", str(record)], {}) == 1  # never overwritten
    assert "never overwritten" in capsys.readouterr().err

    monkeypatch.chdir(tmp_path)  # a relative apksigner path stays local, not a PATH lookup
    check = ["check-apk", "--apk", str(apk), "--apksigner", "./apksigner"]
    check += ["--pin", str(tmp_path / "no-pin"), "--expect-commit", COMMIT, "--expect-run-id", "5"]
    check += ["--expect-apk-sha256", hashlib.sha256(b"apk").hexdigest()]
    assert signing.main(check, {}) == 0
    assert "OK: the APK is the one" in capsys.readouterr().out


def test_check_apk_fails_when_apksigner_rejects_the_signature(tmp_path: Path) -> None:
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"apk")
    (tmp_path / "p.json").write_text(json.dumps(provenance_for(apk)), encoding="utf-8")
    (tmp_path / "pin").write_text(PINNED + "\n", encoding="ascii")

    status, lines = signing.check_apk(
        apk,
        fake_apksigner(tmp_path, PINNED, code=1),
        tmp_path / "p.json",
        tmp_path / "pin",
        expect_commit=COMMIT,
        expect_run_id=39034444658,
        expect_apk_sha256=hashlib.sha256(b"apk").hexdigest(),
        environ={},
    )
    assert status == 1 and lines == ["FAIL: signature: apksigner rejected the APK signature"]


# Self-review follow-ups (d47cf5e) ----------------------------------------------------------


def test_write_apk_provenance_records_a_pinned_signer_as_pinned(tmp_path: Path) -> None:
    """Regression: the pinned-key APK was recorded as ephemeral-debug-key."""
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"apk")
    pin = tmp_path / "pin"
    pin.write_text(PINNED + "\n", encoding="ascii")
    output = tmp_path / "apk-provenance.json"

    assert (
        signing.main(
            [
                "write-apk-provenance",
                "--apk",
                str(apk),
                "--apksigner",
                str(fake_apksigner(tmp_path, PINNED)),
                "--pin",
                str(pin),
                "--commit",
                COMMIT,
                "--run-id",
                "5",
                "--output",
                str(output),
            ],
            {},
        )
        == 0
    )
    assert json.loads(output.read_text())["signingIdentity"] == "pinned-test-key"


def test_a_pinned_claim_without_a_repository_pin_is_blocked_not_failed(tmp_path: Path) -> None:
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"apk")
    (tmp_path / "p.json").write_text(json.dumps(provenance_for(apk)), encoding="utf-8")

    status, lines = signing.check_apk(
        apk,
        fake_apksigner(tmp_path, PINNED),
        tmp_path / "p.json",
        tmp_path / "no-pin",
        expect_commit=COMMIT,
        expect_run_id=39034444658,
        expect_apk_sha256=hashlib.sha256(b"apk").hexdigest(),
        environ={},
    )
    assert status == 2 and "no certificate is pinned in the repository" in lines[0]


def test_the_check_apk_command_requires_the_run_and_a_well_formed_published_digest(
    tmp_path: Path,
) -> None:
    """Regression: without --expect-run-id an APK of any run of the commit was accepted."""
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"apk")
    base = ["check-apk", "--apk", str(apk), "--apksigner", "x", "--expect-commit", COMMIT]
    with pytest.raises(SystemExit):
        signing.main(base, {})
    with pytest.raises(SystemExit):
        signing.main([*base, "--expect-run-id", "5", "--expect-apk-sha256", "ABC"], {})


# Codex review follow-ups (db3ef0c) ---------------------------------------------------------


def test_check_apk_refuses_to_validate_without_the_published_ci_digest(tmp_path: Path) -> None:
    """Regression: without --expect-apk-sha256 the APK was accepted on its provenance alone, a
    file that travels with the APK and so cannot prove which APK the CI run published."""
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"apk")
    (tmp_path / "apk-provenance.json").write_text(json.dumps(provenance_for(apk)), encoding="utf-8")
    pin = tmp_path / "pin"
    pin.write_text(PINNED + "\n", encoding="ascii")
    check = ["check-apk", "--apk", str(apk), "--apksigner", str(fake_apksigner(tmp_path, PINNED))]
    check += ["--pin", str(pin), "--expect-commit", COMMIT, "--expect-run-id", "39034444658"]

    with pytest.raises(SystemExit) as omitted:
        signing.main(check, {})
    assert omitted.value.code == 2  # argparse: the published digest is a required option

    for unusable in ("", "ABC", "0" * 63):
        status, lines = signing.check_apk(
            apk,
            fake_apksigner(tmp_path, PINNED),
            tmp_path / "apk-provenance.json",
            pin,
            expect_commit=COMMIT,
            expect_run_id=39034444658,
            expect_apk_sha256=unusable,
            environ={},
        )
        assert status == 2 and "independent SHA-256 published by the CI run" in lines[0]


@pytest.mark.parametrize("run_id", [0, -1, True])
def test_check_apk_blocks_an_invalid_expected_ci_run(tmp_path: Path, run_id: int) -> None:
    status, lines = checked(tmp_path, run_id=run_id)

    assert status == 2
    assert "positive integer" in lines[0]


@pytest.mark.parametrize("run_id", [0, -1, True])
def test_apk_manifest_with_an_invalid_ci_run_is_blocked(tmp_path: Path, run_id: int) -> None:
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"the APK of de31c94")
    document = {**provenance_for(apk), "runId": run_id}

    status, lines = checked(tmp_path, document=document)

    assert status == 2
    assert "positive integer" in lines[0]


@pytest.mark.parametrize("run_id", ["0", "-1"])
def test_check_apk_cli_rejects_a_non_positive_ci_run(tmp_path: Path, run_id: str) -> None:
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"apk")

    with pytest.raises(SystemExit) as rejected:
        signing.main(
            [
                "check-apk",
                "--apk",
                str(apk),
                "--apksigner",
                "x",
                "--expect-commit",
                COMMIT,
                "--expect-run-id",
                run_id,
                "--expect-apk-sha256",
                hashlib.sha256(b"apk").hexdigest(),
            ],
            {},
        )
    assert rejected.value.code == 2


def test_check_apk_fails_on_a_wrong_published_digest_even_if_the_provenance_agrees(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"apk")
    (tmp_path / "apk-provenance.json").write_text(json.dumps(provenance_for(apk)), encoding="utf-8")
    pin = tmp_path / "pin"
    pin.write_text(PINNED + "\n", encoding="ascii")
    check = ["check-apk", "--apk", str(apk), "--apksigner", str(fake_apksigner(tmp_path, PINNED))]
    check += ["--pin", str(pin), "--expect-commit", COMMIT, "--expect-run-id", "39034444658"]

    assert signing.main([*check, "--expect-apk-sha256", "0" * 64], {}) == 1
    assert "differs from the one the CI run published" in capsys.readouterr().out
    published = hashlib.sha256(b"apk").hexdigest()
    assert signing.main([*check, "--expect-apk-sha256", published], {}) == 0
    assert "OK: the APK is the one" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("write", "message"),
    [
        (lambda pin: pin.mkdir(), "unreadable pin file: IsADirectoryError"),
        (lambda pin: pin.write_text("not a digest\n"), "must hold one lower-case SHA-256"),
        (lambda pin: pin.write_bytes(b"\xff\xfe" + PINNED.encode()), "UnicodeDecodeError"),
        (lambda pin: pin.write_text(f"{PINNED}\n{OTHER}\n"), "must hold one lower-case SHA-256"),
    ],
    ids=["unreadable", "malformed", "mis-encoded", "several-digests"],
)
def test_an_unusable_pinned_certificate_blocks_check_apk(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    write: Callable[[Path], object],
    message: str,
) -> None:
    """Regression: read_pin's SigningError escaped check-apk as a traceback (exit 1, a FAIL)."""
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"apk")
    (tmp_path / "apk-provenance.json").write_text(json.dumps(provenance_for(apk)), encoding="utf-8")
    pin = tmp_path / "pin"
    write(pin)
    published = hashlib.sha256(b"apk").hexdigest()

    status, lines = signing.check_apk(
        apk,
        fake_apksigner(tmp_path, PINNED),
        tmp_path / "apk-provenance.json",
        pin,
        expect_commit=COMMIT,
        expect_run_id=39034444658,
        expect_apk_sha256=published,
        environ={},
    )
    assert status == 2
    assert (
        lines[0].startswith("BLOCKED: the pinned certificate cannot be read")
        and message in lines[0]
    )

    check = ["check-apk", "--apk", str(apk), "--apksigner", str(fake_apksigner(tmp_path, PINNED))]
    check += ["--pin", str(pin), "--expect-commit", COMMIT, "--expect-run-id", "39034444658"]
    assert signing.main([*check, "--expect-apk-sha256", published], {}) == 2
    assert "BLOCKED: the pinned certificate cannot be read" in capsys.readouterr().out
