#!/usr/bin/env python3
"""Generate the Android launcher icon from the control-TV logo (`assets/logo.svg`).

The logo is one polygon (`M`/`L`/`Z` path) filled with one linear gradient. It is not
redrawn: its points and gradient are scaled and centred, unchanged in shape and colour,
into the safe zone of an Android adaptive icon (a 66dp circle in a 108dp canvas, so no
launcher mask shape can cut it), on a white background.

Written under `src-tauri/gen/android/app/src/main/res/`:
- `drawable/ic_launcher_foreground.xml`: the logo as a vector drawable (API 24+ gradients);
- `drawable/ic_widget_logo.xml`: the same logo cropped to its drawn extent, for the widget;
- `mipmap-anydpi-v26/ic_launcher.xml` and `ic_launcher_round.xml`: adaptive icons (API 26+);
- `mipmap-<density>/ic_launcher.png` and `ic_launcher_round.png`: the same composition as
  bitmaps for API 24 and 25 (no adaptive icons there), rendered with Inkscape.

The background colour is `@color/ic_launcher_background` in `values/colors.xml`.
Run `python3 packaging/android_icon.py` after a change to the logo.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOGO = REPO_ROOT / "assets" / "logo.svg"
RES = REPO_ROOT / "src-tauri" / "gen" / "android" / "app" / "src" / "main" / "res"
SCRATCH = REPO_ROOT / "build" / "android-icon"

CANVAS = 108.0  # adaptive icon canvas, dp
SAFE_DIAMETER = 64.0  # inside the 66dp safe circle, with a small margin
BACKGROUND = "#FFFFFF"
WIDGET_LOGO_WIDTH_DP = 32.0
# Legacy launcher icon sizes (48dp) per density.
LEGACY_SIZES = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}


@dataclass(frozen=True)
class Logo:
    points: list[list[tuple[float, float]]]  # one list per closed subpath
    gradient: tuple[float, float, float, float]  # x1, y1, x2, y2 (user space)
    stops: list[tuple[float, str]]  # offset, colour


def parse_logo(svg: str) -> Logo:
    """Read the single gradient-filled polygon path of the logo; refuse anything else."""
    paths = re.findall(r"<path\b[^>]*\sd=\"([^\"]+)\"", svg)
    if len(paths) != 1:
        raise ValueError(f"expected exactly one path in the logo, found {len(paths)}")
    data = paths[0]
    if set(re.findall(r"[A-Za-z]", data)) - {"M", "L", "Z"}:
        raise ValueError("the logo path uses commands other than absolute M, L and Z")
    subpaths: list[list[tuple[float, float]]] = []
    for chunk in re.split(r"(?=M)", data):
        coordinates = re.findall(r"(-?\d+(?:\.\d+)?)\s*,?\s*(-?\d+(?:\.\d+)?)", chunk)
        if coordinates:
            subpaths.append([(float(x), float(y)) for x, y in coordinates])
    gradient = re.search(
        r"<linearGradient\b[^>]*\bx1=\"([\d.]+)\"[^>]*\by1=\"([\d.]+)\"[^>]*"
        r"\bx2=\"([\d.]+)\"[^>]*\by2=\"([\d.]+)\"[^>]*gradientUnits=\"userSpaceOnUse\"",
        svg,
    )
    if gradient is None:
        raise ValueError("the logo has no user-space linear gradient")
    stops = re.findall(r"<stop\b[^>]*offset=\"([\d.]+)\"[^>]*stop-color=\"(#[0-9a-fA-F]{6})\"", svg)
    if len(stops) < 2:
        raise ValueError("the logo gradient has fewer than two stops")
    x1, y1, x2, y2 = (float(value) for value in gradient.groups())
    return Logo(subpaths, (x1, y1, x2, y2), [(float(o), c.upper()) for o, c in stops])


def placement(logo: Logo) -> tuple[float, float, float]:
    """Scale and offsets that centre the logo's drawn extent inside the safe circle."""
    xs = [x for subpath in logo.points for x, _ in subpath]
    ys = [y for subpath in logo.points for _, y in subpath]
    width, height = max(xs) - min(xs), max(ys) - min(ys)
    scale = SAFE_DIAMETER / (width**2 + height**2) ** 0.5
    centre = CANVAS / 2
    offset_x = centre - scale * (min(xs) + width / 2)
    offset_y = centre - scale * (min(ys) + height / 2)
    return scale, offset_x, offset_y


def _number(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _mapped(
    logo: Logo, place: tuple[float, float, float] | None = None
) -> tuple[str, tuple[float, float, float, float]]:
    scale, dx, dy = placement(logo) if place is None else place
    path = " ".join(
        "M "
        + " L ".join(f"{_number(dx + scale * x)} {_number(dy + scale * y)}" for x, y in sub)
        + " Z"
        for sub in logo.points
    )
    x1, y1, x2, y2 = logo.gradient
    return path, (dx + scale * x1, dy + scale * y1, dx + scale * x2, dy + scale * y2)


def foreground_xml(logo: Logo) -> str:
    """The logo as a 108dp vector drawable, its points and gradient placed in the safe zone."""
    return _vector_xml(logo, _mapped(logo), ("108dp", "108dp"), (108.0, 108.0))


def widget_logo_xml(logo: Logo) -> str:
    """The logo cropped to its own drawn extent (no icon padding), for the home-screen widget.

    Its points and gradient are only moved to the origin, never scaled or redrawn.
    """
    xs = [x for subpath in logo.points for x, _ in subpath]
    ys = [y for subpath in logo.points for _, y in subpath]
    width, height = max(xs) - min(xs), max(ys) - min(ys)
    size = (WIDGET_LOGO_WIDTH_DP, WIDGET_LOGO_WIDTH_DP * height / width)
    return _vector_xml(
        logo,
        _mapped(logo, (1.0, -min(xs), -min(ys))),
        (f"{_number(size[0])}dp", f"{_number(size[1])}dp"),
        (width, height),
    )


def _vector_xml(
    logo: Logo,
    mapped: tuple[str, tuple[float, float, float, float]],
    size_dp: tuple[str, str],
    viewport: tuple[float, float],
) -> str:
    path, (x1, y1, x2, y2) = mapped
    items = "\n".join(
        f'                    <item android:offset="{_number(offset)}" '
        f'android:color="#FF{colour[1:]}" />'
        for offset, colour in logo.stops
    )
    return f"""<?xml version="1.0" encoding="utf-8"?>
<!-- Generated by packaging/android_icon.py from assets/logo.svg: do not edit. -->
<vector xmlns:android="http://schemas.android.com/apk/res/android"
    xmlns:aapt="http://schemas.android.com/aapt"
    android:width="{size_dp[0]}"
    android:height="{size_dp[1]}"
    android:viewportWidth="{_number(viewport[0])}"
    android:viewportHeight="{_number(viewport[1])}">
    <path android:pathData="{path}">
        <aapt:attr name="android:fillColor">
            <gradient
                android:type="linear"
                android:startX="{_number(x1)}"
                android:startY="{_number(y1)}"
                android:endX="{_number(x2)}"
                android:endY="{_number(y2)}">
{items}
            </gradient>
        </aapt:attr>
    </path>
</vector>
"""


ADAPTIVE_XML = """<?xml version="1.0" encoding="utf-8"?>
<!-- Generated by packaging/android_icon.py: the control-TV logo on a white background. -->
<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">
    <background android:drawable="@color/ic_launcher_background" />
    <foreground android:drawable="@drawable/ic_launcher_foreground" />
</adaptive-icon>
"""


def legacy_svg(logo: Logo, *, round_icon: bool) -> str:
    """The visible 72dp of the adaptive icon (white square or circle, logo) as an SVG."""
    path, (x1, y1, x2, y2) = _mapped(logo)
    stops = "".join(f'<stop offset="{_number(o)}" stop-color="{c}"/>' for o, c in logo.stops)
    if round_icon:
        shape = f'<circle cx="54" cy="54" r="36" fill="{BACKGROUND}"/>'
    else:
        shape = f'<rect x="18" y="18" width="72" height="72" rx="10" fill="{BACKGROUND}"/>'
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="18 18 72 72" width="72" height="72">'
        f'<defs><linearGradient id="g" x1="{_number(x1)}" y1="{_number(y1)}" '
        f'x2="{_number(x2)}" y2="{_number(y2)}" gradientUnits="userSpaceOnUse">{stops}'
        f'</linearGradient></defs>{shape}<path d="{path}" fill="url(#g)"/></svg>'
    )


def render_png(inkscape: str, svg: Path, png: Path, size: int) -> None:
    subprocess.run(
        [
            inkscape,
            "--export-type=png",
            f"--export-filename={png}",
            "-w",
            str(size),
            "-h",
            str(size),
            str(svg),
        ],
        check=True,
        capture_output=True,
    )


# Resources of the default Tauri/Android Studio icon that the generated ones replace.
STALE = [
    "drawable-v24/ic_launcher_foreground.xml",
    "drawable/ic_launcher_background.xml",
    *(f"mipmap-{density}/ic_launcher_foreground.png" for density in LEGACY_SIZES),
]


def generate(res: Path = RES, inkscape: str | None = None) -> None:
    logo = parse_logo(LOGO.read_text(encoding="utf-8"))
    tool = inkscape or shutil.which("inkscape")
    if tool is None:
        raise RuntimeError("inkscape is required to render the legacy (API 24-25) icons")
    for stale in STALE:
        (res / stale).unlink(missing_ok=True)
    (res / "drawable").mkdir(parents=True, exist_ok=True)
    (res / "drawable" / "ic_launcher_foreground.xml").write_text(foreground_xml(logo))
    (res / "drawable" / "ic_widget_logo.xml").write_text(widget_logo_xml(logo))
    adaptive = res / "mipmap-anydpi-v26"
    adaptive.mkdir(parents=True, exist_ok=True)
    for name in ("ic_launcher.xml", "ic_launcher_round.xml"):
        (adaptive / name).write_text(ADAPTIVE_XML)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    try:
        for round_icon, name in ((False, "ic_launcher"), (True, "ic_launcher_round")):
            svg = SCRATCH / f"{name}.svg"
            svg.write_text(legacy_svg(logo, round_icon=round_icon))
            for density, size in LEGACY_SIZES.items():
                render_png(tool, svg, res / f"mipmap-{density}" / f"{name}.png", size)
    finally:
        shutil.rmtree(SCRATCH)


def main() -> int:
    try:
        generate()
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
