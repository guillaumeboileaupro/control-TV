"""The official app logo files shipped with the window (assets/app-logos/).

Each file must be referenced by the catalogue (ui/src/app-logos.ts), listed with its source and
licence in docs/APP_LOGOS.md, and be a plain drawing: no script, no foreign content, no external
or data reference, no DOCTYPE or entity, a viewBox, and a small size.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LOGOS = ROOT / "assets" / "app-logos"
CATALOGUE = ROOT / "ui" / "src" / "app-logos.ts"
DOCS = ROOT / "docs" / "APP_LOGOS.md"
SVG = "{http://www.w3.org/2000/svg}"
FORBIDDEN = {"script", "foreignObject", "iframe", "image", "use", "a", "animate", "set", "style"}
MAX_BYTES = 16 * 1024


def _files() -> list[Path]:
    return sorted(LOGOS.glob("*.svg"))


def test_every_shipped_logo_is_used_and_every_used_logo_is_shipped() -> None:
    referenced = set(re.findall(r'shipped\(\s*"[^"]+",\s*"([a-z0-9-]+)"', CATALOGUE.read_text()))

    assert referenced == {path.stem for path in _files()}
    assert len(referenced) == 9


@pytest.mark.parametrize("path", _files(), ids=lambda p: p.name)
def test_a_logo_file_is_a_plain_local_drawing(path: Path) -> None:
    raw = path.read_text(encoding="utf-8")

    assert path.stat().st_size <= MAX_BYTES
    assert not re.search(r"<!DOCTYPE|<!ENTITY", raw, re.IGNORECASE)
    assert not re.search(r"https?:|javascript:|data:", raw.replace("http://www.w3.org/", ""))
    root = ET.fromstring(raw)
    assert root.tag == f"{SVG}svg"
    assert re.fullmatch(r"[\d.]+ [\d.]+ [\d.]+ [\d.]+", root.get("viewBox", ""))
    for element in root.iter():
        name = element.tag.rsplit("}", 1)[-1]
        assert name not in FORBIDDEN, name
        for attribute, value in element.attrib.items():
            assert not attribute.rsplit("}", 1)[-1].startswith("on"), attribute
            if attribute.endswith("href"):
                assert value.startswith("#"), value


@pytest.mark.parametrize("path", _files(), ids=lambda p: p.name)
def test_a_logo_file_has_its_source_and_licence_documented(path: Path) -> None:
    rows = [line for line in DOCS.read_text(encoding="utf-8").splitlines() if path.name in line]

    assert rows, f"{path.name} is not listed in docs/APP_LOGOS.md"
    assert "commons.wikimedia.org" in rows[0]
    assert re.search(r"public domain|CC0", rows[0], re.IGNORECASE)
