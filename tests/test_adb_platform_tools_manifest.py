"""The audited ADB candidate stays reproducible evidence, never a bundled executable."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "packaging" / "adb-platform-tools.json"


def manifest() -> dict[str, object]:
    value = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_platform_tools_candidate_is_versioned_and_checksum_pinned() -> None:
    value = manifest()
    archive = value["linuxArchive"]
    assert isinstance(archive, dict)
    version = value["version"]
    assert version == "37.0.1"
    assert value["minimumWirelessPairingVersion"] == "30.0.0"
    assert archive["url"] == (
        f"https://dl.google.com/android/repository/platform-tools_r{version}-linux.zip"
    )
    assert archive["size"] == 9_054_187
    checksum = archive["sha256"]
    assert isinstance(checksum, str) and re.fullmatch(r"[0-9a-f]{64}", checksum)
    assert checksum == "d230f13842f60f782a8645f9c813f8f845bf36089ea7289f28c48f17979313f1"


def test_candidate_is_explicitly_reference_only_and_not_a_tauri_resource() -> None:
    value = manifest()
    assert value["status"] == "reference-only-not-bundled"
    assert value["redistributionApproved"] is False
    assert str(value["licenseTerms"]).startswith("https://developer.android.com/")
    assert value["archiveNotice"] == "platform-tools/NOTICE.txt"

    tauri = (ROOT / "src-tauri" / "tauri.release.conf.json").read_text(encoding="utf-8")
    assert "platform-tools" not in tauri
    assert not list(ROOT.rglob("platform-tools_r*-linux.zip"))
    assert not (ROOT / "platform-tools" / "adb").exists()
