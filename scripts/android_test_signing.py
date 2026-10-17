"""The stable test signing identity of the Android debug APK (docs/ANDROID_TEST_SIGNING.md).

Android updates an installed app in place, keeping its data, preferences and home-screen widget,
only when the new APK has the same signing certificate. Without configuration every builder signs
with its own debug key (a new one on every CI run), so each build needs an uninstall.

With `CONTROL_TV_ANDROID_KEYSTORE` and `CONTROL_TV_ANDROID_KEYSTORE_PASSWORD` set, Gradle signs
debug builds with one test key (alias `control-tv-test`). Its public certificate SHA-256 is pinned
in `packaging/android/test-signing-certificate.sha256`, and `verify-apk` refuses an APK signed by
any other key when the stable key was meant to be used: a replaced key never passes silently.

The private key and its password never enter the repository, a command line or a log: keytool
reads the password from the environment (`-storepass:env`), and nothing here prints it.

Usage:
    CONTROL_TV_ANDROID_KEYSTORE_PASSWORD=... python3 scripts/android_test_signing.py \
        generate --keystore ~/.config/control-tv/android-test.p12
    python3 scripts/android_test_signing.py verify-apk --apk APK --apksigner PATH \
        [--pin FILE] [--stable-key-used] [--require-stable] \
        [--commit SHA --run-id ID --provenance-output PATH]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

KEY_ALIAS = "control-tv-test"
KEYSTORE_ENV = "CONTROL_TV_ANDROID_KEYSTORE"
PASSWORD_ENV = "CONTROL_TV_ANDROID_KEYSTORE_PASSWORD"
PIN = Path(__file__).resolve().parent.parent / "packaging/android/test-signing-certificate.sha256"
APK_SCHEMA = "control-tv-apk-provenance/1"
MIN_PASSWORD = 12

_DIGEST = re.compile(r"[0-9a-f]{64}")
_COMMIT = re.compile(r"[0-9a-f]{40}")
# apksigner prints "Signer #1 certificate SHA-256 digest: …" (build-tools 35), "V2 Signer:
# certificate SHA-256 digest: …" (newer build-tools, one line per scheme) or, for SDK-ranged
# signers, "Signer (minSdkVersion=24, maxSdkVersion=32) certificate SHA-256 digest: …".
_APKSIGNER = re.compile(
    r"^(?:Signer (?:#\d+|\([^)\n]*\))|V\d+(?:\.\d+)? Signer:) certificate SHA-256 digest: "
    r"([0-9a-fA-F]{64})\s*$",
    re.M,
)
_KEYTOOL = re.compile(r"^\s*SHA256:\s*((?:[0-9A-Fa-f]{2}:){31}[0-9A-Fa-f]{2})\s*$", re.M)

# Runs a command with exactly the given environment (keytool reads the password from it).
Runner = Callable[[Sequence[str], Mapping[str, str]], subprocess.CompletedProcess[str]]


class SigningError(Exception):
    """The signing identity is missing, ambiguous or not the expected one."""


class Identity(StrEnum):
    PINNED = "pinned-test-key"
    EPHEMERAL = "ephemeral-debug-key"


@dataclass(frozen=True)
class Verdict:
    identity: Identity
    certificate: str
    message: str


def apksigner_certificates(output: str) -> tuple[str, ...]:
    """The distinct signer certificate SHA-256 digests printed by `apksigner verify
    --print-certs`, in order."""
    digests = (match.group(1).lower() for match in _APKSIGNER.finditer(output))
    return tuple(dict.fromkeys(digests))  # one certificate printed per scheme counts once


def keytool_certificate(output: str) -> str:
    """The certificate SHA-256 printed by `keytool -list -v` (colons removed, lower case)."""
    found = [match.group(1) for match in _KEYTOOL.finditer(output)]
    if len(found) != 1:
        raise SigningError("keytool did not print exactly one SHA-256 certificate fingerprint")
    return found[0].replace(":", "").lower()


def read_pin(path: Path) -> str | None:
    """The pinned certificate digest, or None when no identity is pinned yet."""
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as error:
        raise SigningError(f"unreadable pin file: {type(error).__name__}") from None
    values = [line.strip() for line in lines if line.strip() and not line.startswith("#")]
    if len(values) != 1 or not _DIGEST.fullmatch(values[0]):
        raise SigningError("the pin file must hold one lower-case SHA-256 certificate digest")
    return values[0]


def judge(
    certificates: Sequence[str],
    pin: str | None,
    *,
    stable_key_used: bool,
    require_stable: bool,
) -> Verdict:
    """Whether an APK's signature is acceptable; `SigningError` says why not."""
    if len(certificates) != 1:
        raise SigningError(f"expected exactly one signer, found {len(certificates)}")
    certificate = certificates[0]
    if stable_key_used:
        if pin is None:
            raise SigningError(
                "the stable test key was used but no certificate is pinned: commit "
                "packaging/android/test-signing-certificate.sha256 first"
            )
        if certificate != pin:
            raise SigningError(
                "the APK is signed by another key than the pinned test identity "
                "(the key was replaced or the secret is wrong); refusing it"
            )
        return Verdict(Identity.PINNED, certificate, "signed with the pinned test identity")
    if pin is not None and require_stable:
        raise SigningError(
            "a test identity is pinned but this build used an ephemeral debug key "
            "(signing secret unavailable)"
        )
    note = (
        "ephemeral debug key: installing it over another build needs an uninstall"
        if pin is None
        else "ephemeral debug key, NOT the pinned test identity: do not install over a pinned build"
    )
    return Verdict(Identity.EPHEMERAL, certificate, note)


def apk_provenance(
    apk: Path, verdict: Verdict, *, commit: str, run_id: int | None
) -> Mapping[str, str | int | None]:
    if not _COMMIT.fullmatch(commit):
        raise SigningError("the commit must be a full 40-character Git SHA")
    digest = hashlib.sha256(apk.read_bytes()).hexdigest()
    return {
        "schema": APK_SCHEMA,
        "commit": commit,
        "runId": run_id,
        "apkSha256": digest,
        "signerCertificateSha256": verdict.certificate,
        "signingIdentity": verdict.identity.value,
    }


@dataclass(frozen=True)
class ApkProvenance:
    """What CI recorded for one APK (`apk-provenance.json`)."""

    commit: str
    run_id: int | None
    apk_sha256: str
    signer_sha256: str
    identity: Identity


def read_apk_provenance(path: Path) -> ApkProvenance:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SigningError(f"no APK provenance at {path}") from None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SigningError(f"unreadable APK provenance: {type(error).__name__}") from None
    if not isinstance(document, dict) or document.get("schema") != APK_SCHEMA:
        raise SigningError(f"the APK provenance is not {APK_SCHEMA}")
    commit, run_id = document.get("commit"), document.get("runId")
    apk, signer = document.get("apkSha256"), document.get("signerCertificateSha256")
    identity = document.get("signingIdentity")
    if not isinstance(commit, str) or not _COMMIT.fullmatch(commit):
        raise SigningError("the APK provenance commit must be a full 40-character Git SHA")
    if run_id is not None and (not isinstance(run_id, int) or isinstance(run_id, bool)):
        raise SigningError("the APK provenance run id must be an integer or null")
    if not isinstance(identity, str) or identity not in {member.value for member in Identity}:
        raise SigningError("the APK provenance signing identity is unrecognized")
    return ApkProvenance(
        commit,
        run_id,
        _sha256_field(apk, "apkSha256"),
        _sha256_field(signer, "signerCertificateSha256"),
        Identity(identity),
    )


def _sha256_field(value: object, name: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise SigningError(f"the APK provenance {name} must be a SHA-256")
    return value


def check_apk(
    apk: Path,
    apksigner: Path,
    provenance_path: Path,
    pin_path: Path,
    *,
    expect_commit: str,
    expect_run_id: int | None,
    expect_apk_sha256: str | None,
    environ: Mapping[str, str],
    run: Runner | None = None,
) -> tuple[int, list[str]]:
    """The `check-apk` verdict: 0 PASS, 1 FAIL (another APK, key, commit or run), 2 BLOCKED
    (the identity cannot be established), with report lines."""
    try:
        provenance = read_apk_provenance(provenance_path)
        digest = hashlib.sha256(apk.read_bytes()).hexdigest()
    except SigningError as error:
        return 2, [f"BLOCKED: the APK identity cannot be established: {error}"]
    except OSError as error:
        return 2, [f"BLOCKED: the APK cannot be read: {type(error).__name__}"]
    problems: list[str] = []
    if digest != provenance.apk_sha256:
        problems.append("the APK SHA-256 differs from its provenance (another APK)")
    if expect_apk_sha256 is not None and digest != expect_apk_sha256:
        problems.append("the APK SHA-256 differs from the one the CI run published")
    if provenance.commit != expect_commit:
        problems.append("the APK provenance commit is not the expected one")
    if expect_run_id is not None and provenance.run_id != expect_run_id:
        problems.append("the APK provenance CI run is not the expected one")
    try:
        certificates = verify_apk(apk, apksigner, environ, run or _run)
    except OSError as error:
        return 2, [f"BLOCKED: apksigner cannot be run: {type(error).__name__}"]
    except SigningError as error:
        problems.append(f"signature: {error}")
    else:
        try:
            verdict = judge(
                certificates,
                read_pin(pin_path),
                stable_key_used=provenance.identity is Identity.PINNED,
                require_stable=False,
            )
        except SigningError as error:
            problems.append(f"signature: {error}")
        else:
            if verdict.certificate != provenance.signer_sha256:
                problems.append("the APK signer differs from its provenance")
    if problems:
        return 1, [f"FAIL: {problem}" for problem in problems]
    return 0, [
        f"APK {digest}: commit {provenance.commit}, run {provenance.run_id}",
        f"signer {provenance.signer_sha256} ({provenance.identity.value})",
        "OK: the APK is the one its provenance and the expected CI run name",
    ]


def _run(args: Sequence[str], environ: Mapping[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args), capture_output=True, text=True, check=False, shell=False, env=dict(environ)
    )


def generate(keystore: Path, environ: Mapping[str, str], run: Runner = _run) -> str:
    """Creates the test key in a new PKCS#12 keystore and returns its certificate digest.

    Never replaces an existing keystore; the password comes from `PASSWORD_ENV` only."""
    if keystore.exists():
        raise SigningError(f"{keystore} exists; an existing key is never replaced")
    password = environ.get(PASSWORD_ENV, "")
    if len(password) < MIN_PASSWORD:
        raise SigningError(
            f"set {PASSWORD_ENV} to a password of at least {MIN_PASSWORD} characters"
        )
    keystore.parent.mkdir(parents=True, exist_ok=True)
    # English output whatever the locale, so the fingerprint line can be read back.
    keytool = ("keytool", "-J-Duser.language=en", "-J-Duser.country=US")
    common = ("-keystore", str(keystore), "-storetype", "PKCS12", "-alias", KEY_ALIAS)
    secret = ("-storepass:env", PASSWORD_ENV, "-keypass:env", PASSWORD_ENV)
    created = run(
        (
            *keytool,
            "-genkeypair",
            *common,
            "-keyalg",
            "RSA",
            "-keysize",
            "3072",
            "-validity",
            "10000",
            "-dname",
            "CN=control-TV test signing, O=control-TV",
            *secret,
            "-noprompt",
        ),
        environ,
    )
    if created.returncode != 0:
        raise SigningError("keytool could not create the key (its output is not shown)")
    os.chmod(keystore, 0o600)
    listed = run((*keytool, "-list", "-v", *common, "-storepass:env", PASSWORD_ENV), environ)
    if listed.returncode != 0:
        raise SigningError("keytool could not read the new key back")
    return keytool_certificate(listed.stdout)


def verify_apk(
    apk: Path, apksigner: Path, environ: Mapping[str, str], run: Runner = _run
) -> tuple[str, ...]:
    # A path to a file is run as such ("./apksigner" stays local); a bare name is looked up.
    program = str(apksigner.resolve()) if apksigner.exists() else str(apksigner)
    checked = run((program, "verify", "--print-certs", str(apk)), environ)
    if checked.returncode != 0:
        raise SigningError("apksigner rejected the APK signature")
    certificates = apksigner_certificates(checked.stdout)
    if not certificates:
        # Certificate lines are public data; show them so a new apksigner format is visible.
        shown = [line for line in checked.stdout.splitlines() if "certificate" in line][:6]
        raise SigningError(
            "apksigner printed no signer certificate SHA-256 line; its certificate lines: "
            + ("; ".join(shown) or "none")
        )
    return certificates


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] = os.environ) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("generate", help="create the stable test key (never replaces)")
    create.add_argument("--keystore", type=Path, required=True)
    check = commands.add_parser("verify-apk", help="check an APK's signing identity")
    check.add_argument("--apk", type=Path, required=True)
    check.add_argument("--apksigner", type=Path, required=True)
    check.add_argument("--pin", type=Path, default=PIN)
    check.add_argument("--stable-key-used", action="store_true")
    check.add_argument("--require-stable", action="store_true")
    check.add_argument("--commit")
    check.add_argument("--run-id", type=int)
    check.add_argument("--provenance-output", type=Path)
    acceptance = commands.add_parser(
        "check-apk", help="acceptance: the APK is exactly the expected CI build"
    )
    acceptance.add_argument("--apk", type=Path, required=True)
    acceptance.add_argument("--apksigner", type=Path, required=True)
    acceptance.add_argument("--provenance", type=Path)
    acceptance.add_argument("--pin", type=Path, default=PIN)
    acceptance.add_argument("--expect-commit", required=True)
    acceptance.add_argument("--expect-run-id", type=int)
    acceptance.add_argument("--expect-apk-sha256")
    record = commands.add_parser(
        "write-apk-provenance", help="record the provenance of an older CI APK (never overwrites)"
    )
    record.add_argument("--apk", type=Path, required=True)
    record.add_argument("--apksigner", type=Path, required=True)
    record.add_argument("--pin", type=Path, default=PIN)
    record.add_argument("--commit", required=True)
    record.add_argument("--run-id", type=int)
    record.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)
    if arguments.command == "check-apk":
        status, lines = check_apk(
            arguments.apk,
            arguments.apksigner,
            arguments.provenance or arguments.apk.parent / "apk-provenance.json",
            arguments.pin,
            expect_commit=arguments.expect_commit,
            expect_run_id=arguments.expect_run_id,
            expect_apk_sha256=arguments.expect_apk_sha256,
            environ=environ,
        )
        print("\n".join(lines))
        return status
    try:
        if arguments.command == "write-apk-provenance":
            if arguments.output.exists():
                raise SigningError(f"{arguments.output} exists; it is never overwritten")
            verdict = judge(
                verify_apk(arguments.apk, arguments.apksigner, environ),
                read_pin(arguments.pin),
                stable_key_used=False,
                require_stable=False,
            )
            document = apk_provenance(
                arguments.apk, verdict, commit=arguments.commit, run_id=arguments.run_id
            )
            arguments.output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(document, indent=2))
            return 0
        if arguments.command == "generate":
            digest = generate(arguments.keystore.expanduser(), environ)
            print(f"certificate SHA-256: {digest}")
            print("pin it: write this digest to packaging/android/test-signing-certificate.sha256")
            return 0
        verdict = judge(
            verify_apk(arguments.apk, arguments.apksigner, environ),
            read_pin(arguments.pin),
            stable_key_used=arguments.stable_key_used,
            require_stable=arguments.require_stable,
        )
        print(f"signing identity: {verdict.identity.value}")
        print(f"signer certificate SHA-256: {verdict.certificate}")
        print(verdict.message)
        if arguments.provenance_output is not None:
            if arguments.commit is None:
                raise SigningError("--provenance-output needs --commit")
            document = apk_provenance(
                arguments.apk, verdict, commit=arguments.commit, run_id=arguments.run_id
            )
            arguments.provenance_output.write_text(
                json.dumps(document, indent=2) + "\n", encoding="utf-8"
            )
    except SigningError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
