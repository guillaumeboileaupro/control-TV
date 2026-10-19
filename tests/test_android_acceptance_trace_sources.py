"""The Android acceptance mode exists only in debug builds and never collects implicitly."""

from __future__ import annotations

import re
from pathlib import Path

from control_tv.command_trace import TERMINAL_EVENTS

APP = Path(__file__).resolve().parent.parent / "src-tauri/gen/android/app/src"
PACKAGE = "java/io/github/guillaumeboileaupro/controltv"


def read(relative: str) -> str:
    return (APP / relative).read_text(encoding="utf-8")


def test_the_release_build_cannot_switch_a_trace_on() -> None:
    release = read(f"release/{PACKAGE}/AcceptanceTrace.kt")

    assert "fun configure(context: Context, embedded: PyObject) = Unit" in release
    assert "callAttr" not in release and "SharedPreferences" not in release


def test_the_main_manifest_and_sources_hold_no_acceptance_screen_or_export() -> None:
    manifest = read("main/AndroidManifest.xml")

    assert "AcceptanceTrace" not in manifest and ".acceptance" not in manifest
    assert not list((APP / "main" / PACKAGE).glob("AcceptanceTrace*.kt"))


def test_the_debug_screen_and_its_provider_share_only_the_acceptance_directory() -> None:
    manifest = read("debug/AndroidManifest.xml")
    paths = read("debug/res/xml/acceptance_trace_paths.xml")

    assert 'android:name=".AcceptanceTraceActivity"' in manifest
    assert 'android:name=".AcceptanceTraceProvider"' in manifest
    assert 'android:authorities="${applicationId}.acceptance"' in manifest
    assert 'android:exported="false"' in manifest  # the provider
    assert re.findall(r"<(\w+-path)\b[^>]*>", paths) == ["files-path"]
    assert 'path="acceptance/"' in paths


def test_the_mode_is_off_by_default_and_applied_once_before_any_request() -> None:
    switch = read(f"debug/{PACKAGE}/AcceptanceTrace.kt")
    bridge = read(f"main/{PACKAGE}/EmbeddedBridge.kt")

    assert "getBoolean(ENABLED, false)" in switch
    assert "if (!enabled(context)) return" in switch
    assert 'callAttr("configure_acceptance_trace"' in switch
    module = bridge.index('getModule("control_tv.embedded")')
    assert bridge.index("AcceptanceTrace.configure(") > module


def test_export_and_deletion_happen_only_on_an_explicit_tap() -> None:
    screen = read(f"debug/{PACKAGE}/AcceptanceTraceActivity.kt")

    assert screen.count("Intent.ACTION_SEND") == 1
    assert 'button("Exporter la trace…") { export() }' in screen
    assert 'button("Supprimer les preuves…") { confirmDelete() }' in screen
    assert "MODE_WORLD_READABLE" not in screen and "getExternal" not in screen


def test_the_kotlin_terminal_events_match_the_python_trace() -> None:
    files = read(f"debug/{PACKAGE}/AcceptanceTraceFiles.kt")
    block = files[files.index("TERMINAL_EVENTS = setOf(") : files.index(")", files.index("setOf("))]

    assert set(re.findall(r'"([a-z_]+)"', block)) == TERMINAL_EVENTS
