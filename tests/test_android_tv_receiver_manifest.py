"""ControlTV Receiver's manifest keeps the permissions its WebRTC session needs (no device)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

MANIFEST = (
    Path(__file__).resolve().parents[1] / "android-tv-receiver/app/src/main/AndroidManifest.xml"
)
ANDROID = "{http://schemas.android.com/apk/res/android}"


def test_the_receiver_declares_the_network_permissions_libwebrtc_needs() -> None:
    root = ET.parse(MANIFEST).getroot()
    permissions = {element.get(f"{ANDROID}name") for element in root.iter("uses-permission")}

    # Without ACCESS_NETWORK_STATE, libwebrtc's NetworkMonitor aborts the receiver as soon as a
    # session starts (SecurityException, then a fatal check in jvm.cc), seen on the TV.
    assert {
        "android.permission.INTERNET",
        "android.permission.ACCESS_NETWORK_STATE",
    } <= permissions
