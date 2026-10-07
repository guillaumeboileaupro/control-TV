"""Static guarantees of the Android home-screen widget's sources and manifest.

The widget's TV picker must stay a thin view over the shared Python control layer: no Cast,
discovery or network code of its own, no command, and reachable only from the widget.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "src-tauri" / "gen" / "android" / "app" / "src" / "main"
KOTLIN = APP / "java" / "io" / "github" / "guillaumeboileaupro" / "controltv"
ANDROID = "{http://schemas.android.com/apk/res/android}"
PICKER_SOURCES = [
    KOTLIN / "TvPicker.kt",
    KOTLIN / "PickerScreen.kt",
    KOTLIN / "WidgetTvPickerActivity.kt",
]
COMMANDS = {"play", "pause", "stop", "seek", "set_volume", "set_muted", "load_media"}


def component(kind: str, name: str) -> ET.Element:
    manifest = ET.parse(APP / "AndroidManifest.xml").getroot()
    [element] = [e for e in manifest.iter(kind) if e.get(f"{ANDROID}name") == name]
    return element


def test_the_picker_and_the_tap_receiver_are_reachable_only_from_the_app_itself() -> None:
    picker = component("activity", ".WidgetTvPickerActivity")
    receiver = component("receiver", ".WidgetTapReceiver")

    assert picker.get(f"{ANDROID}exported") == "false"
    assert receiver.get(f"{ANDROID}exported") == "false"
    assert picker.findall("intent-filter") == []
    # Its own task: opening it from the widget never brings up the main app.
    assert picker.get(f"{ANDROID}taskAffinity") == ".widgetpicker"
    assert picker.get(f"{ANDROID}excludeFromRecents") == "true"


def test_the_picker_has_no_cast_discovery_or_network_code_of_its_own() -> None:
    forbidden = re.compile(
        r"pychromecast|zeroconf|NsdManager|DatagramSocket|\bSocket\b|InetAddress|8009|_googlecast",
        re.IGNORECASE,
    )
    for source in PICKER_SOURCES:
        text = source.read_text(encoding="utf-8")
        assert not forbidden.search(text), source.name


def test_the_picker_only_asks_the_shared_layer_to_discover_and_never_sends_a_command() -> None:
    methods: set[str] = set()
    for source in PICKER_SOURCES:
        text = source.read_text(encoding="utf-8")
        methods |= set(re.findall(r'"method"\s*:\s*"(\w+)"', text))
        quoted = set(re.findall(r'"(\w+)"', text))
        assert not quoted & COMMANDS, source.name

    assert methods == {"discover_devices"}


def test_the_widget_name_opens_the_picker_and_the_logo_opens_the_app() -> None:
    widget = (KOTLIN / "ControlTvWidget.kt").read_text(encoding="utf-8")

    assert "setOnClickPendingIntent(R.id.widget_header, openPicker(context))" in widget
    assert "setOnClickPendingIntent(R.id.widget_logo, openApp(context))" in widget
    assert "WidgetTvPickerActivity::class.java" in widget
