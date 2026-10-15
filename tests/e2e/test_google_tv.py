"""Google TV protocol catalog sourced from DUAL_CONTROLLER_VALIDATION.md."""

from __future__ import annotations

from collections.abc import Sequence

from acceptance import Capability, Context, Contract, Evidence, Result, Scenario
from acceptance_google_tv import blocked_real, contract


def real_scenario(
    scenario_id: str,
    description: str,
    details: Contract,
    *,
    commands: bool = False,
    adb: bool = False,
) -> Scenario:
    capabilities = {Capability.EXECUTE}
    if commands:
        capabilities.add(Capability.TV_COMMAND)
    if adb:
        capabilities.add(Capability.ADB)
    scenario: Scenario

    def run(context: Context) -> Result:
        return blocked_real(context, scenario)

    scenario = Scenario(
        scenario_id,
        "google-tv",
        description,
        run,
        frozenset(capabilities),
        (),
        Evidence.REAL,
        details,
    )
    return scenario


COMMON = ("owner authorization", "Ubuntu and Android builds identified", "same Wi-Fi")
MEDIA = (*COMMON, "known playing or paused media", "same TV selected independently")

SCENARIOS = (
    real_scenario(
        "R1",
        "Each remote discovers the TV independently",
        contract(
            COMMON,
            "Ubuntu + Android + Google TV",
            (),
            "nothing selected",
            "both independent lists contain the same TV",
        ),
    ),
    real_scenario(
        "R2",
        "Both remotes read the same state",
        contract(
            (*MEDIA, "R1 PASS"),
            "Ubuntu + Android + Google TV",
            (),
            "stable playback/volume/mute",
            "both fresh reads agree with the TV",
        ),
    ),
    real_scenario(
        "R3",
        "Both remotes converge after an external change",
        contract(
            (*MEDIA, "R2 PASS"),
            "Ubuntu + Android + Google TV",
            (),
            "state changed outside control-TV",
            "each client sees it only after its own refresh",
        ),
    ),
    real_scenario(
        "R4",
        "Repeated and concurrent reads change nothing",
        contract(
            (*MEDIA, "R2 PASS"),
            "Ubuntu + Android + Google TV",
            (),
            "stable state",
            "reads remain coherent and cause no observed change",
        ),
    ),
    real_scenario(
        "C1",
        "Ubuntu pauses",
        contract(
            MEDIA,
            "Ubuntu -> Google TV",
            ("pause",),
            "playing",
            "TV visibly paused; Android agrees after refresh",
        ),
        commands=True,
    ),
    real_scenario(
        "C2",
        "Android plays",
        contract(
            MEDIA,
            "Android -> Google TV",
            ("play",),
            "paused",
            "TV visibly playing; Ubuntu agrees after refresh",
        ),
        commands=True,
    ),
    real_scenario(
        "C3",
        "Ubuntu lowers volume or mutes fixed volume",
        contract(
            MEDIA,
            "Ubuntu -> Google TV",
            ("set_volume or mute",),
            "reported sound state",
            "reported and physical effects recorded separately",
        ),
        commands=True,
    ),
    real_scenario(
        "C4",
        "Android raises volume or unmutes",
        contract(
            MEDIA,
            "Android -> Google TV",
            ("set_volume or unmute",),
            "C3 result",
            "reported and physical effects recorded separately",
        ),
        commands=True,
    ),
    real_scenario(
        "C5",
        "Concurrent Ubuntu pause and Android play",
        contract(
            MEDIA,
            "Ubuntu + Android -> Google TV",
            ("pause", "play"),
            "identified media session",
            "one attempt per client; both converge on the last delivered state",
        ),
        commands=True,
    ),
    real_scenario(
        "C6",
        "Session switch after dispatch",
        contract(
            (*MEDIA, "usable content/session/item identity", "ordered receiver trace"),
            "Ubuntu + sender app + Google TV",
            ("pause once", "external media switch"),
            "captured old mediaSessionId",
            "send uses old id; replacement session never confirms it",
        ),
        commands=True,
    ),
    real_scenario(
        "C7",
        "Ambiguous delivery without replay",
        contract(
            (*MEDIA, "send-attempt instrumentation", "owner agrees to Wi-Fi interruption"),
            "one client + Google TV",
            ("one command", "owner interrupts Wi-Fi"),
            "reachable TV",
            "exactly one local send attempt; no automatic retry/replay",
        ),
        commands=True,
    ),
    real_scenario(
        "TV-POWER",
        "Power command",
        contract(
            COMMON,
            "client + Google TV",
            ("power",),
            "power state recorded",
            "single attempt and physical effect recorded",
        ),
        commands=True,
    ),
    real_scenario(
        "TV-NAVIGATION",
        "Navigation keys and latency K1-K3",
        contract(
            (*COMMON, "TV home screen", "video evidence"),
            "Ubuntu + Android + Google TV",
            ("left/right/OK/back/home",),
            "focus visible",
            "sent/refused presses and focus moves counted",
        ),
        commands=True,
    ),
    real_scenario(
        "TV-KEYBOARD",
        "Remote keyboard",
        contract(
            (*COMMON, "text field open", "remote channel paired manually"),
            "client + Google TV",
            ("text", "backspace", "enter"),
            "focused editable field",
            "single attempts and visible text effects recorded",
        ),
        commands=True,
    ),
    real_scenario(
        "TV-APPS",
        "List, foreground and launch applications",
        contract(
            (*COMMON, "ADB already enabled and authorized by owner"),
            "client + Google TV",
            ("list apps", "read foreground", "launch selected app"),
            "debug endpoint reachable",
            "list and foreground observed; one launch attempt",
        ),
        commands=True,
        adb=True,
    ),
    real_scenario(
        "TV-ERRORS",
        "Timeout and unavailable-device handling",
        contract(
            (*COMMON, "controlled unavailable state"),
            "Ubuntu + Android + Google TV",
            ("read only unless separately authorized",),
            "known reachable state",
            "bounded error, no fabricated state and no replay",
        ),
    ),
)


def by_id(identifier: str) -> Scenario:
    return next(scenario for scenario in SCENARIOS if scenario.id == identifier)


def test_c1_to_c7_are_the_existing_protocol_ids_in_order() -> None:
    assert [scenario.id for scenario in SCENARIOS if scenario.id.startswith("C")] == [
        f"C{number}" for number in range(1, 8)
    ]


def test_read_only_and_command_authorizations_are_distinct() -> None:
    assert Capability.TV_COMMAND not in by_id("R1").capabilities
    assert all(Capability.TV_COMMAND in by_id(f"C{number}").capabilities for number in range(1, 8))
    assert Capability.ADB in by_id("TV-APPS").capabilities
    assert all(scenario.evidence is Evidence.REAL for scenario in SCENARIOS)


def test_every_scenario_has_the_report_contract() -> None:
    required: Sequence[str] = (
        "preconditions",
        "device",
        "planned_commands",
        "initial_state",
        "expected",
    )
    for scenario in SCENARIOS:
        assert scenario.contract is not None
        assert all(hasattr(scenario.contract, field) for field in required)
