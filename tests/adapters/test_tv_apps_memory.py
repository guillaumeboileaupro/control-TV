"""The last app list each TV gave, kept on this device (issue #112)."""

from __future__ import annotations

import stat
from pathlib import Path

from control_tv.adapters.tv_apps_memory import FileAppMemory, known_apps
from control_tv.domain import DeviceId, TvApp

TV = DeviceId("uuid-living-room")
APPS = (TvApp("com.netflix.ninja", "Netflix"), TvApp("org.example.music"))


def test_a_list_is_kept_privately_per_tv_without_its_id_in_a_file_name(tmp_path: Path) -> None:
    memory = FileAppMemory(tmp_path / "tv-apps")

    memory.remember(TV, APPS)

    assert memory.recall(TV) == APPS
    assert memory.recall(DeviceId("another-tv")) is None
    files = list((tmp_path / "tv-apps").iterdir())
    assert len(files) == 1 and str(TV) not in files[0].name
    assert stat.S_IMODE((tmp_path / "tv-apps").stat().st_mode) == 0o700
    assert stat.S_IMODE(files[0].stat().st_mode) == 0o600


def test_a_damaged_or_unwritable_list_is_simply_not_used(tmp_path: Path) -> None:
    memory = FileAppMemory(tmp_path / "tv-apps")
    memory.remember(TV, APPS)
    kept = next((tmp_path / "tv-apps").iterdir())

    for content in ("not json", '{"apps": [{"package": "a b"}]}', '{"apps": []}', "{}"):
        kept.write_text(content, encoding="utf-8")
        assert memory.recall(TV) is None

    blocked = tmp_path / "file"
    blocked.write_text("")
    FileAppMemory(blocked / "tv-apps").remember(TV, APPS)  # no exception, nothing kept
    assert FileAppMemory(blocked / "tv-apps").recall(TV) is None


def test_known_apps_are_named_and_never_the_home_screen_itself() -> None:
    apps = known_apps()

    assert TvApp("com.netflix.ninja", "Netflix") in apps
    assert all(app.name for app in apps)
    assert "com.google.android.apps.tv.launcherx" not in {app.package for app in apps}
