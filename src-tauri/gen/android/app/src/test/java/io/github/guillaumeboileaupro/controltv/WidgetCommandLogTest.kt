package io.github.guillaumeboileaupro.controltv

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * What a widget command leaves in the log (2026-10-07 phone Mute failure: the log said only
 * "MUTE -> Sent", so it could not tell which value was requested, what the TV reported
 * before it, or whether the shared layer confirmed it).
 */
class WidgetCommandLogTest {
    private val logs = mutableListOf<String>()

    private fun run(before: JSONObject, action: WidgetAction = WidgetAction.MUTE, answer: (String) -> String): WidgetOutcome {
        val bridge = FakeBridge { method, _ -> if (method == "get_status") statusAnswer(before) else answer(method) }
        return WidgetActionRunner(bridge, TapGuard(MemoryWidgetStore()), { logs += it }).run(action, SELECTED, "tap")
    }

    @Test
    fun anUnconfirmedMuteLogsTheReportedStateTheRequestedValueAndTheConfirmation() {
        val outcome = run(status(muted = false)) { commandAnswer(it, "unconfirmed", status(muted = false)) }

        assertEquals("unconfirmed", (outcome as WidgetOutcome.Sent).confirmation)
        assertEquals(
            listOf(
                "widget command: set_muted muted=true (TV reported playback=playing muted=false volume=0.47)",
                "widget command result: set_muted -> sent, unconfirmed (TV then reported playback=playing muted=false volume=0.47)",
            ),
            logs,
        )
    }

    @Test
    fun aConfirmedMuteAndAnUnmuteFromAReportedMuteAreToldApart() {
        run(status(muted = false)) { commandAnswer(it, "confirmed", status(muted = true)) }
        run(status(muted = true)) { commandAnswer(it, "confirmed", status(muted = false)) }

        assertEquals("widget command: set_muted muted=true (TV reported playback=playing muted=false volume=0.47)", logs[0])
        assertEquals(
            "widget command result: set_muted -> sent, confirmed (TV then reported playback=playing muted=true volume=0.47)",
            logs[1],
        )
        assertEquals("widget command: set_muted muted=false (TV reported playback=playing muted=true volume=0.47)", logs[2])
    }

    @Test
    fun refusedAmbiguousAndUncheckedAnswersAreLoggedAsSuch() {
        run(status()) { errorAnswer("unsupported_operation") }
        run(status()) { errorAnswer("timeout") }
        run(status()) { commandAnswer(it, "not_checked") }

        assertEquals(
            listOf(
                "widget command result: set_muted -> not sent (unsupported_operation)",
                "widget command result: set_muted -> may have been sent (timeout)",
                "widget command result: set_muted -> sent, not_checked",
            ),
            logs.filter { it.startsWith("widget command result") },
        )
    }

    @Test
    fun aVolumeTapLogsTheLevelReportedAndTheLevelRequested() {
        run(status(level = 0.47), WidgetAction.VOLUME_UP) { commandAnswer(it, "confirmed", status(level = 0.57)) }

        assertEquals("widget command: set_volume level=0.57 (TV reported playback=playing muted=false volume=0.47)", logs[0])
    }

    @Test
    fun noCommandMeansNoCommandLineAndNoPrivateValueIsEverLogged() {
        run(status(muted = null)) { commandAnswer(it, "confirmed") }
        assertEquals(emptyList<String>(), logs)

        run(status(muted = false)) { commandAnswer(it, "unconfirmed", status(muted = false)) }
        assertTrue(logs.isNotEmpty())
        for (line in logs) {
            for (secret in listOf(TV, SELECTED.name, "A film", "tap", "192.168")) {
                assertFalse("\"$line\" contains a private value", Regex("\\b${Regex.escape(secret)}\\b").containsMatchIn(line))
            }
        }
    }
}
