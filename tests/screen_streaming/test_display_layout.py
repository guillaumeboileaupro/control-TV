"""The screen configuration service on a fake X server: read, check, apply, confirm, revert and
restore after a failure. No real display is touched."""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import ClassVar

import pytest

from control_tv.screen_streaming import InvalidStreamConfiguration, StreamStartError
from control_tv.screen_streaming.display_layout import (
    Display,
    DisplayManager,
    OutputSetting,
    Rotation,
    check,
    logical_size,
    parse_layout,
    xrandr_arguments,
)
from x11_fakes import X11

MODES = {
    "eDP-1": [("0x48", 1920, 1080, 60.0), ("0x49", 1920, 1080, 47.99), ("0x53", 1280, 720, 60.0)],
    "HDMI-1": [
        ("0x1a8", 1920, 1080, 59.96),
        ("0x1a9", 1920, 1080, 49.93),
        ("0x54", 1024, 768, 60.0),
    ],
    "DP-1": [],
}


@dataclass
class FakeScreens:
    """An X server with eDP-1 (primary), a forced-on HDMI-1 and a disconnected DP-1."""

    settings: dict[str, OutputSetting] = field(
        default_factory=lambda: {
            "eDP-1": OutputSetting("eDP-1", True, "0x48", 0, 0, primary=True),
            "HDMI-1": OutputSetting("HDMI-1", False),
            "DP-1": OutputSetting("DP-1", False),
        }
    )
    refuse: int = 0  # how many changes xrandr refuses
    ignore: bool = False  # xrandr accepts the change but nothing happens
    calls: list[tuple[str, ...]] = field(default_factory=list)

    def __call__(
        self, argv: Sequence[str], environ: Mapping[str, str]
    ) -> subprocess.CompletedProcess[str]:
        arguments = tuple(argv[1:])
        self.calls.append(arguments)
        if arguments == ("--verbose",):
            return subprocess.CompletedProcess(argv, 0, self.verbose(), "")
        if self.refuse:
            self.refuse -= 1
            return subprocess.CompletedProcess(argv, 1, "", "refused")
        if not self.ignore:
            self.apply(arguments)
        return subprocess.CompletedProcess(argv, 0, "", "")

    def apply(self, arguments: tuple[str, ...]) -> None:
        index = 0
        while index < len(arguments):
            assert arguments[index] == "--output"
            name = arguments[index + 1]
            index += 2
            if arguments[index] == "--off":
                self.settings[name] = OutputSetting(name, False)
                index += 1
                continue
            mode, position, rotation, scale = (arguments[index + n] for n in (1, 3, 5, 7))
            index += 8
            primary = index < len(arguments) and arguments[index] == "--primary"
            index += primary
            x, y = (int(value) for value in position.split("x"))
            self.settings[name] = OutputSetting(
                name, True, mode, x, y, Rotation(rotation), float(scale.split("x")[0]), primary
            )

    def verbose(self) -> str:
        lines = ["Screen 0: minimum 320 x 200, current 1920 x 1080, maximum 16384 x 16384"]
        for name, setting in self.settings.items():
            state = "disconnected" if name == "DP-1" else "connected"
            head = f"{name} {state}" + (" primary" if setting.primary else "")
            if setting.enabled:
                mode = next(m for m in MODES[name] if m[0] == setting.mode)
                width, height = mode[1], mode[2]
                if setting.rotation in (Rotation.LEFT, Rotation.RIGHT):
                    width, height = height, width
                width, height = round(width * setting.scale), round(height * setting.scale)
                place = f"{width}x{height}+{setting.x}+{setting.y}"
                head += f" {place} ({setting.mode}) {setting.rotation.value}"
            lines.append(head + " (normal left inverted right x axis y axis) 0mm x 0mm")
            lines.append(f"\tTransform:  {setting.scale:.6f} 0.000000 0.000000")
            for mode_id, width, height, rate in MODES[name]:
                lines.append(f"  {width}x{height} ({mode_id}) 138.650MHz +HSync -VSync")
                lines.append(
                    f"        h: width  {width} start 1968 end 2000 total 2080 clock  66.66KHz"
                )
                lines.append(f"        v: height {height} start 1083 end 1088 clock  {rate:.2f}Hz")
        return "\n".join(lines) + "\n"


class FakeTimer:
    """A timer that fires only when the test says so."""

    made: ClassVar[list[FakeTimer]] = []

    def __init__(self, seconds: float, function: Callable[[], None]) -> None:
        self.seconds = seconds
        self.function = function
        self.started = False
        self.cancelled = False
        self.daemon = False
        FakeTimer.made.append(self)

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True

    def fire(self) -> None:
        if not self.cancelled:
            self.function()


def manager(screens: FakeScreens) -> DisplayManager:
    FakeTimer.made.clear()
    return DisplayManager(X11, screens, confirm_seconds=20, timer=FakeTimer)  # type: ignore[arg-type]


def joined(screens: FakeScreens) -> list[OutputSetting]:
    """The TV's screen on to the right of the laptop's."""
    return [
        OutputSetting("eDP-1", True, "0x48", 0, 0, primary=True),
        OutputSetting("HDMI-1", True, "0x1a8", 1920, 0),
        OutputSetting("DP-1", False),
    ]


def test_the_layout_is_read_with_modes_positions_and_flags() -> None:
    screens = FakeScreens()
    screens.settings["HDMI-1"] = OutputSetting(
        "HDMI-1", True, "0x54", 1920, 312, Rotation.LEFT, 0.8
    )
    displays = {display.name: display for display in manager(screens).layout()}

    laptop, tv, absent = displays["eDP-1"], displays["HDMI-1"], displays["DP-1"]
    assert laptop.setting == OutputSetting("eDP-1", True, "0x48", 0, 0, primary=True)
    assert [(mode.width, mode.height, mode.rate) for mode in laptop.modes][:2] == [
        (1920, 1080, 60.0),
        (1920, 1080, 47.99),
    ]
    assert tv.setting.rotation is Rotation.LEFT and tv.setting.scale == 0.8
    assert (tv.setting.x, tv.setting.y) == (1920, 312)
    assert logical_size(tv, tv.setting) == (614, 819)  # 768x1024 turned, at 0.8
    assert not absent.connected and absent.modes == () and not absent.setting.enabled
    assert logical_size(absent, absent.setting) == (0, 0)
    assert parse_layout("garbage\n") == ()


def test_apply_sets_every_screen_in_one_command_and_waits_for_confirmation() -> None:
    screens = FakeScreens()
    displays = manager(screens)

    applied = displays.apply(joined(screens))

    assert {display.name: display.setting.enabled for display in applied}["HDMI-1"]
    command = screens.calls[1]
    assert command[:2] == ("--output", "eDP-1") and "--primary" in command
    assert command[-3:] == ("--output", "DP-1", "--off")
    assert displays.awaiting_confirmation
    timer = FakeTimer.made[-1]
    assert timer.started and timer.seconds == 20

    displays.confirm()
    assert timer.cancelled and not displays.awaiting_confirmation
    displays.confirm()  # idempotent


def test_an_unconfirmed_change_is_reverted_by_itself() -> None:
    screens = FakeScreens()
    displays = manager(screens)
    displays.apply(joined(screens))

    FakeTimer.made[-1].fire()

    assert not screens.settings["HDMI-1"].enabled
    assert screens.settings["eDP-1"] == OutputSetting("eDP-1", True, "0x48", 0, 0, primary=True)
    assert displays.take_reverted_notice() and not displays.take_reverted_notice()
    FakeTimer.made[-1].function()  # a late second firing changes nothing


def test_revert_restores_the_previous_configuration_on_request() -> None:
    screens = FakeScreens()
    displays = manager(screens)
    displays.apply(joined(screens))

    reverted = displays.revert()

    assert not {display.name: display for display in reverted}["HDMI-1"].setting.enabled
    assert FakeTimer.made[-1].cancelled
    displays.revert()  # nothing waits: only a read


def test_one_change_at_a_time() -> None:
    screens = FakeScreens()
    displays = manager(screens)
    displays.apply(joined(screens))
    with pytest.raises(StreamStartError, match="keep or revert"):
        displays.apply(joined(screens))
    displays.close()  # the application quits: the change is reverted
    assert not screens.settings["HDMI-1"].enabled
    displays.close()


@pytest.mark.parametrize(
    ("screens", "message"),
    [
        (FakeScreens(refuse=1), "xrandr refused"),
        (FakeScreens(ignore=True), "did not take that configuration"),
    ],
)
def test_a_failed_change_restores_the_previous_configuration(
    screens: FakeScreens, message: str
) -> None:
    displays = manager(screens)
    with pytest.raises(StreamStartError, match=message):
        displays.apply(joined(screens))
    assert not displays.awaiting_confirmation
    assert screens.calls[-2][:2] == ("--output", "eDP-1")  # restore, then verification read
    assert screens.settings["eDP-1"].enabled and not screens.settings["HDMI-1"].enabled


def test_clone_shares_a_position_and_single_turns_the_others_off() -> None:
    screens = FakeScreens()
    displays = manager(screens)
    clone = [
        OutputSetting("eDP-1", True, "0x48", 0, 0, primary=True),
        OutputSetting("HDMI-1", True, "0x1a8", 0, 0),
        OutputSetting("DP-1", False),
    ]
    displays.apply(clone)
    displays.confirm()
    single = [
        OutputSetting("eDP-1", False),
        OutputSetting("HDMI-1", True, "0x1a8", 0, 0, primary=True),
        OutputSetting("DP-1", False),
    ]
    displays.apply(single)
    assert not screens.settings["eDP-1"].enabled and screens.settings["HDMI-1"].primary
    displays.revert()
    assert screens.settings["eDP-1"].enabled


def displays_of(screens: FakeScreens) -> tuple[Display, ...]:
    return parse_layout(screens.verbose())


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda s: s[:2], "every screen exactly once"),
        (lambda s: [replace(x, enabled=False, primary=False) for x in s], "at least one screen"),
        (lambda s: [replace(s[0], primary=False), *s[1:]], "exactly one screen"),
        (lambda s: [s[0], replace(s[1], primary=True), s[2]], "exactly one screen"),
        (lambda s: [s[0], s[1], replace(s[2], primary=True)], "exactly one screen"),
        (lambda s: [s[0], replace(s[1], mode="0x48"), s[2]], "does not offer"),
        (lambda s: [s[0], replace(s[1], rotation="up"), s[2]], "orientation"),
        (lambda s: [s[0], replace(s[1], scale=0.9), s[2]], "100, 125, 150 or 200"),
        (lambda s: [s[0], replace(s[1], x=-1), s[2]], "out of range"),
        (lambda s: [s[0], replace(s[1], x=True), s[2]], "whole pixels"),
        (lambda s: [s[0], replace(s[1], x=1000), s[2]], "overlap"),
    ],
)
def test_a_configuration_that_could_not_be_used_is_refused(
    change: Callable[[list[OutputSetting]], list[OutputSetting]], message: str
) -> None:
    screens = FakeScreens()
    with pytest.raises(InvalidStreamConfiguration, match=message):
        check(displays_of(screens), change(joined(screens)))


def test_the_command_line_turns_screens_off_last_and_scales() -> None:
    arguments = xrandr_arguments(
        [
            OutputSetting("A", False),
            OutputSetting("B", True, "0x1", 5, 6, Rotation.RIGHT, 0.667, True),
        ]
    )
    assert arguments == (
        "--output",
        "B",
        "--mode",
        "0x1",
        "--pos",
        "5x6",
        "--rotate",
        "right",
        "--scale",
        "0.667x0.667",
        "--primary",
        "--output",
        "A",
        "--off",
    )


def test_xrandr_that_cannot_run_is_reported() -> None:
    def missing(
        argv: Sequence[str], environ: Mapping[str, str]
    ) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("xrandr")

    with pytest.raises(StreamStartError, match="FileNotFoundError"):
        DisplayManager(X11, missing).layout()


def test_the_real_timer_is_a_daemon_that_can_be_cancelled() -> None:
    screens = FakeScreens()
    displays = DisplayManager(X11, screens, confirm_seconds=60)
    displays.apply(joined(screens))
    timer = displays._pending_timer
    assert isinstance(timer, threading.Timer) and timer.daemon
    displays.confirm()
    timer.join(1)
    assert not timer.is_alive()


def test_a_failed_change_whose_restore_also_fails_says_so() -> None:
    screens = FakeScreens(refuse=2)
    with pytest.raises(StreamStartError, match="could not be restored"):
        manager(screens).apply(joined(screens))


def test_a_restore_that_fails_when_the_change_expires_stays_available_for_retry() -> None:
    screens = FakeScreens()
    displays = manager(screens)
    displays.apply(joined(screens))
    screens.refuse = 1

    FakeTimer.made[-1].fire()

    assert not displays.take_reverted_notice() and displays.awaiting_confirmation
    assert screens.settings["HDMI-1"].enabled
    displays.revert()
    assert not displays.awaiting_confirmation and not screens.settings["HDMI-1"].enabled


def test_a_restore_that_is_ignored_stays_available_for_retry() -> None:
    screens = FakeScreens()
    displays = manager(screens)
    displays.apply(joined(screens))
    screens.ignore = True

    FakeTimer.made[-1].fire()

    assert displays.awaiting_confirmation and screens.settings["HDMI-1"].enabled
    screens.ignore = False
    displays.close()
    assert not displays.awaiting_confirmation and not screens.settings["HDMI-1"].enabled


def test_a_change_the_screens_took_differently_is_restored() -> None:
    screens = FakeScreens()
    applying = screens.apply

    def shifted(arguments: tuple[str, ...]) -> None:
        applying(arguments)
        screens.settings["HDMI-1"] = replace(
            screens.settings["HDMI-1"], y=8
        )  # the X server moved it

    screens.apply = shifted  # type: ignore[method-assign]
    with pytest.raises(StreamStartError, match="did not take that configuration"):
        manager(screens).apply(joined(screens))


# Bridge methods ---------------------------------------------------------------------------


@pytest.fixture
def bridged(monkeypatch: pytest.MonkeyPatch) -> FakeScreens:
    from control_tv import bridge

    screens = FakeScreens()
    monkeypatch.setattr(bridge, "_display_manager", manager(screens))
    monkeypatch.setattr(bridge, "_screen_controller", None)
    return screens


def call(method: str, **params: object) -> dict[str, object]:
    from control_tv import bridge

    request = {"id": 1, "method": method, "params": params}
    return bridge.dispatch(None, request)  # type: ignore[arg-type,return-value]


def as_json(settings: Sequence[OutputSetting]) -> list[dict[str, object]]:
    return [
        {
            "name": s.name,
            "enabled": s.enabled,
            "mode": s.mode,
            "x": s.x,
            "y": s.y,
            "rotation": s.rotation.value,
            "scale": s.scale,
            "primary": s.primary,
        }
        for s in settings
    ]


def test_the_bridge_reads_applies_keeps_and_reverts_the_layout(bridged: FakeScreens) -> None:
    read = call("get_display_layout")
    assert read["ok"] is True
    result = read["result"]
    assert isinstance(result, dict) and result["awaitingConfirmation"] is False
    laptop = result["displays"][0]
    assert laptop["name"] == "eDP-1" and laptop["primary"] is True and laptop["mode"] == "0x48"
    assert laptop["modes"][0] == {"id": "0x48", "width": 1920, "height": 1080, "rate": 60.0}

    applied = call("apply_display_layout", displays=as_json(joined(bridged)))
    assert applied["ok"] is True and applied["result"]["awaitingConfirmation"] is True  # type: ignore[index]
    kept = call("confirm_display_layout")
    assert kept["result"]["awaitingConfirmation"] is False  # type: ignore[index]

    call(
        "apply_display_layout",
        displays=as_json(
            [
                OutputSetting("eDP-1", True, "0x48", 0, 0, primary=True),
                OutputSetting("HDMI-1", False),
                OutputSetting("DP-1", False),
            ]
        ),
    )
    reverted = call("revert_display_layout")
    assert reverted["ok"] is True and bridged.settings["HDMI-1"].enabled


@pytest.mark.parametrize(
    ("displays", "message"),
    [
        ("all", "displays must be a list"),
        (["eDP-1"], "each display must be an object"),
        ([{"name": "eDP-1"}], "a name and enabled"),
        ([{"name": "eDP-1", "enabled": True, "mode": 1, "primary": True}], "needs a mode"),
        (
            [{"name": "eDP-1", "enabled": True, "mode": "0x48", "primary": True, "rotation": "up"}],
            "rotation must be",
        ),
        (
            [
                {
                    "name": "eDP-1",
                    "enabled": True,
                    "mode": "0x48",
                    "primary": True,
                    "rotation": "normal",
                    "scale": "1",
                }
            ],
            "scale must be",
        ),
        (
            [
                {
                    "name": "eDP-1",
                    "enabled": True,
                    "mode": "0x48",
                    "primary": True,
                    "rotation": "normal",
                    "scale": 1,
                    "x": 0,
                    "y": 0,
                }
            ],
            "every screen exactly once",
        ),
    ],
)
def test_the_bridge_refuses_a_malformed_layout(
    bridged: FakeScreens, displays: object, message: str
) -> None:
    answer = call("apply_display_layout", displays=displays)
    assert answer["ok"] is False
    assert message in answer["error"]["message"]  # type: ignore[index]
    assert answer["error"]["code"] == "invalid_argument"  # type: ignore[index]


def test_a_refused_change_is_reported_as_unsupported(bridged: FakeScreens) -> None:
    bridged.refuse = 1
    answer = call("apply_display_layout", displays=as_json(joined(bridged)))
    assert answer["error"]["code"] == "unsupported_operation"  # type: ignore[index]


def test_the_bridge_reverts_an_unkept_change_when_it_exits(bridged: FakeScreens) -> None:
    import io

    from control_tv import bridge

    call("apply_display_layout", displays=as_json(joined(bridged)))
    bridge.run(None, stdin=io.StringIO(""), stdout=io.StringIO())  # type: ignore[arg-type]
    assert not bridged.settings["HDMI-1"].enabled


def test_the_bridge_creates_one_display_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    from control_tv import bridge

    monkeypatch.setattr(bridge, "_display_manager", None)
    first = bridge._displays()
    assert bridge._displays() is first
