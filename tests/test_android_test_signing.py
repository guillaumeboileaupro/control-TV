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
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType

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
    # Only the build step sees the secrets: no job- or workflow-level exposure.
    assert workflow.count("secrets.CONTROL_TV_ANDROID_TEST_KEYSTORE_PASSWORD") == 1
