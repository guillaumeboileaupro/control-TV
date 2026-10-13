"""The Android launcher icon generated from the control-TV logo (`packaging/android_icon.py`)."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "control_tv_android_icon", ROOT / "packaging" / "android_icon.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


icon = load_module()
LOGO_SVG = (ROOT / "assets" / "logo.svg").read_text(encoding="utf-8")
RES = ROOT / "src-tauri" / "gen" / "android" / "app" / "src" / "main" / "res"


def test_the_logo_is_read_as_its_polygon_and_gradient() -> None:
    logo = icon.parse_logo(LOGO_SVG)

    assert sum(len(sub) for sub in logo.points) == len(
        re.findall(r"[ML]\s*\d", re.search(r'\sd="([^"]+)"', LOGO_SVG).group(1))  # type: ignore[union-attr]
    )
    assert logo.gradient == (197.0, 575.0, 1160.0, 575.0)
    assert logo.stops == [(0.0, "#2FD4EF"), (0.5, "#249CF4"), (1.0, "#1760FF")]


def test_a_logo_with_curves_or_several_paths_is_refused_rather_than_approximated() -> None:
    with pytest.raises(ValueError, match="other than absolute M, L and Z"):
        icon.parse_logo(LOGO_SVG.replace(" L ", " C ", 1))
    with pytest.raises(ValueError, match="exactly one path"):
        icon.parse_logo(LOGO_SVG.replace("</svg>", '<path d="M 0 0 L 1 1 Z"/></svg>'))


def test_the_whole_logo_fits_the_adaptive_icon_safe_circle_without_distortion() -> None:
    logo = icon.parse_logo(LOGO_SVG)
    scale, dx, dy = icon.placement(logo)

    mapped = [(dx + scale * x, dy + scale * y) for sub in logo.points for x, y in sub]
    assert max(((x - 54) ** 2 + (y - 54) ** 2) ** 0.5 for x, y in mapped) <= 33
    xs, ys = [x for x, _ in mapped], [y for _, y in mapped]
    assert (min(xs) + max(xs)) / 2 == pytest.approx(54)
    assert (min(ys) + max(ys)) / 2 == pytest.approx(54)


def test_the_foreground_keeps_every_point_and_the_exact_gradient_colours() -> None:
    logo = icon.parse_logo(LOGO_SVG)

    xml = icon.foreground_xml(logo)

    data = re.search(r'android:pathData="([^"]+)"', xml).group(1)  # type: ignore[union-attr]
    assert len(re.findall(r"[ML] ", data)) == sum(len(sub) for sub in logo.points)
    for _offset, colour in logo.stops:
        assert f'android:color="#FF{colour[1:]}"' in xml
    assert 'android:viewportWidth="108"' in xml


def test_the_committed_icon_resources_match_the_generator() -> None:
    logo = icon.parse_logo(LOGO_SVG)

    assert (RES / "drawable" / "ic_launcher_foreground.xml").read_text() == icon.foreground_xml(
        logo
    )
    for name in ("ic_launcher.xml", "ic_launcher_round.xml"):
        assert (RES / "mipmap-anydpi-v26" / name).read_text() == icon.ADAPTIVE_XML
    for stale in icon.STALE:
        assert not (RES / stale).exists(), stale
    colors = (RES / "values" / "colors.xml").read_text()
    assert '<color name="ic_launcher_background">#FFFFFFFF</color>' in colors


def test_the_manifest_uses_the_generated_launcher_icons() -> None:
    manifest = (RES.parent / "AndroidManifest.xml").read_text()

    assert 'android:icon="@mipmap/ic_launcher"' in manifest
    for density in icon.LEGACY_SIZES:
        assert (RES / f"mipmap-{density}" / "ic_launcher.png").is_file()
        assert (RES / f"mipmap-{density}" / "ic_launcher_round.png").is_file()


def test_generation_writes_every_resource_and_removes_the_default_icon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rendered: list[tuple[str, int]] = []
    for stale in icon.STALE:
        (tmp_path / stale).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / stale).write_text("default")

    def render(inkscape: str, svg: Path, png: Path, size: int) -> None:
        rendered.append((png.relative_to(tmp_path).as_posix(), size))

    monkeypatch.setattr(icon, "render_png", render)
    monkeypatch.setattr(icon, "SCRATCH", tmp_path / "scratch")

    icon.generate(tmp_path, inkscape="inkscape")

    assert not any((tmp_path / stale).exists() for stale in icon.STALE)
    assert (tmp_path / "drawable" / "ic_launcher_foreground.xml").is_file()
    assert (tmp_path / "mipmap-anydpi-v26" / "ic_launcher_round.xml").is_file()
    assert ("mipmap-xxxhdpi/ic_launcher.png", 192) in rendered
    assert ("mipmap-mdpi/ic_launcher_round.png", 48) in rendered
    assert len(rendered) == 2 * len(icon.LEGACY_SIZES)
    assert not (tmp_path / "scratch").exists()


def test_the_widget_logo_is_the_logo_cropped_to_its_extent_never_scaled() -> None:
    logo = icon.parse_logo(LOGO_SVG)

    xml = icon.widget_logo_xml(logo)

    xs = [x for sub in logo.points for x, _ in sub]
    ys = [y for sub in logo.points for _, y in sub]
    assert f'android:viewportWidth="{icon._number(max(xs) - min(xs))}"' in xml
    assert f'android:viewportHeight="{icon._number(max(ys) - min(ys))}"' in xml
    data = re.search(r'android:pathData="([^"]+)"', xml).group(1)  # type: ignore[union-attr]
    first_x, first_y = logo.points[0][0]
    assert data.startswith(
        f"M {icon._number(first_x - min(xs))} {icon._number(first_y - min(ys))} "
    )
    for _offset, colour in logo.stops:
        assert f'android:color="#FF{colour[1:]}"' in xml
    assert (RES / "drawable" / "ic_widget_logo.xml").read_text() == xml
