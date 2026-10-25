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

    private fun run(before: JSONObject, action: WidgetAction = WidgetAction.PLAY_PAUSE, answer: (String) -> String): WidgetOutcome {
        val bridge = FakeBridge { method, _ -> if (method == "get_status") statusAnswer(before) else answer(method) }
        return WidgetActionRunner(bridge, TapGuard(MemoryWidgetStore()), { logs += it }).run(action, SELECTED, "tap")
    }

    @Test
    fun anUnconfirmedCommandLogsTheReportedStateTheCommandAndTheConfirmation() {
        val outcome = run(status(muted = false)) { commandAnswer(it, "unconfirmed", status(muted = false)) }

        assertEquals("unconfirmed", (outcome as WidgetOutcome.Sent).confirmation)
        assertEquals(
            listOf(
                "widget command: pause (TV reported playback=playing muted=false volume=0.47)",
                "widget command result: pause -> sent, unconfirmed (TV then reported playback=playing muted=false volume=0.47)",
            ),
            logs,
        )
    }

    @Test
    fun refusedAmbiguousAndUncheckedAnswersAreLoggedAsSuch() {
        run(status()) { errorAnswer("unsupported_operation") }
        run(status()) { errorAnswer("timeout") }
        run(status()) { commandAnswer(it, "not_checked") }

        assertEquals(
            listOf(
                "widget command result: pause -> not sent (unsupported_operation)",
                "widget command result: pause -> may have been sent (timeout)",
                "widget command result: pause -> sent, not_checked",
            ),
            logs.filter { it.startsWith("widget command result") },
        )
    }

    @Test
    fun aRemoteKeyLogsTheKeyPressedAndHowTheLayerAnswered() {
        run(status(), WidgetAction.VOLUME_UP) { errorAnswer("pairing_required") }

        assertEquals(
            listOf(
                "widget command: press_remote_key key=volume_up",
                "widget command result: press_remote_key -> not sent (pairing_required)",
            ),
            logs,
        )
    }

    @Test
    fun noCommandMeansNoCommandLineAndNoPrivateValueIsEverLogged() {
        run(status(playback = null)) { commandAnswer(it, "confirmed") }
        assertEquals(emptyList<String>(), logs)

        run(status(muted = false)) { commandAnswer(it, "unconfirmed", status(muted = false)) }
        run(status(), WidgetAction.HOME) { errorAnswer("timeout") }
        assertTrue(logs.isNotEmpty())
        for (line in logs) {
            for (secret in listOf(TV, SELECTED.name, "A film", "tap", "192.168")) {
                assertFalse("\"$line\" contains a private value", Regex("\\b${Regex.escape(secret)}\\b").containsMatchIn(line))
            }
        }
    }
}
