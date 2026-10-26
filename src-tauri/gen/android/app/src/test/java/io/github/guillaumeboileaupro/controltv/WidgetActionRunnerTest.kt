package io.github.guillaumeboileaupro.controltv

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class WidgetActionRunnerTest {
    private val store = MemoryWidgetStore()

    private fun runner(bridge: FakeBridge) = WidgetActionRunner(bridge, TapGuard(store))

    /** A TV in [state]; every command is answered with [commandReply]. */
    private fun tv(state: JSONObject = status(), commandReply: (String) -> String = { commandAnswer(it, "confirmed") }) =
        FakeBridge { method, _ ->
            when (method) {
                "get_status" -> statusAnswer(state)
                "discover_devices" -> devicesAnswer(TV)
                else -> commandReply(method)
            }
        }

    private fun commandOf(bridge: FakeBridge): Pair<String, JSONObject>? {
        assertTrue("at most one command per tap", bridge.commands.size <= 1)
        return bridge.commands.singleOrNull()
    }

    // --- No TV, unknown TV: nothing is sent -------------------------------------------------

    @Test
    fun withNoSelectedTvNothingReachesTheBridge() {
        val bridge = tv()

        for (action in WidgetAction.entries) {
            assertEquals(WidgetOutcome.NoSelection, runner(bridge).run(action, null, "tap-$action"))
        }
        assertEquals(emptyList<String>(), bridge.methods)
    }

    @Test
    fun aTvUnknownToThisProcessIsFoundByOneReadOnlyDiscoveryThenCommandedOnce() {
        var firstRead = true
        val bridge = FakeBridge { method, _ ->
            when (method) {
                "get_status" -> if (firstRead) { firstRead = false; errorAnswer("device_not_found") } else statusAnswer()
                "discover_devices" -> devicesAnswer(OTHER_TV, TV)
                else -> commandAnswer(method, "confirmed")
            }
        }

        val outcome = runner(bridge).run(WidgetAction.PLAY_PAUSE, SELECTED, "tap")

        assertEquals(listOf("get_status", "discover_devices", "get_status", "pause"), bridge.methods)
        assertEquals(TV, commandOf(bridge)?.second?.getString("deviceId"))
        assertTrue(outcome is WidgetOutcome.Sent)
    }

    @Test
    fun aSelectedTvMissingFromDiscoveryIsNeverReplacedByAnotherOne() {
        val bridge = FakeBridge { method, _ ->
            when (method) {
                "get_status" -> errorAnswer("device_not_found")
                "discover_devices" -> devicesAnswer(OTHER_TV)
                else -> commandAnswer(method, "confirmed")
            }
        }

        val outcome = runner(bridge).run(WidgetAction.STOP, SELECTED, "tap")

        assertEquals(WidgetOutcome.NotFound, outcome)
        assertEquals(listOf("get_status", "discover_devices"), bridge.methods)
    }

    @Test
    fun aFailedDiscoveryOrStatusReadSendsNothing() {
        val discoveryFails = FakeBridge { method, _ ->
            if (method == "get_status") errorAnswer("device_not_found") else errorAnswer("discovery_failed")
        }
        val statusFails = FakeBridge { _, _ -> errorAnswer("device_unavailable") }

        assertEquals(WidgetOutcome.StatusUnreadable, runner(discoveryFails).run(WidgetAction.STOP, SELECTED, "a"))
        assertEquals(WidgetOutcome.StatusUnreadable, runner(statusFails).run(WidgetAction.STOP, SELECTED, "b"))
        assertEquals(emptyList<Pair<String, JSONObject>>(), discoveryFails.commands + statusFails.commands)
    }

    @Test
    fun aPythonThatCannotStartSendsNothing() {
        val bridge = FakeBridge { _, _ -> throw PythonUnavailable(IllegalStateException("no Python")) }

        assertEquals(WidgetOutcome.Unavailable, runner(bridge).run(WidgetAction.PLAY_PAUSE, SELECTED, "tap"))
        assertEquals(listOf("get_status"), bridge.methods)
    }

    // --- Refresh is a read ------------------------------------------------------------------

    @Test
    fun refreshOnlyReadsTheStatus() {
        val bridge = tv()

        val outcome = runner(bridge).run(WidgetAction.REFRESH, SELECTED, "tap")

        assertEquals(listOf("get_status"), bridge.methods)
        assertTrue(outcome is WidgetOutcome.Status)
        assertEquals(null, store.claimed)
    }

    // --- Which single command the observed state calls for -----------------------------------

    @Test
    fun playPauseAsksForTheOppositeOfWhatTheTvReports() {
        for ((playback, expected) in listOf("playing" to "pause", "buffering" to "pause", "paused" to "play")) {
            val bridge = tv(status(playback = playback))

            runner(bridge).run(WidgetAction.PLAY_PAUSE, SELECTED, "tap-$playback")

            assertEquals(playback, expected, commandOf(bridge)?.first)
        }
    }

    @Test
    fun playPauseSendsNothingWhenThereIsNothingToToggle() {
        for (state in listOf(status(playback = null), status(playback = "idle"), status(playback = "unknown"),
            status(playback = "playing", supportsPause = false), status(connection = "disconnected"))) {
            val bridge = tv(state)

            val outcome = runner(bridge).run(WidgetAction.PLAY_PAUSE, SELECTED, "tap")

            assertTrue(outcome is WidgetOutcome.NoCommand)
            assertEquals(emptyList<Pair<String, JSONObject>>(), bridge.commands)
        }
    }

    @Test
    fun stopIsSentOnlyWhenTheTvReportsMedia() {
        val playing = tv(status(playback = "paused"))
        val idle = tv(status(playback = "idle"))
        val empty = tv(status(playback = null))

        runner(playing).run(WidgetAction.STOP, SELECTED, "a")
        runner(idle).run(WidgetAction.STOP, SELECTED, "b")
        runner(empty).run(WidgetAction.STOP, SELECTED, "c")

        assertEquals("stop", commandOf(playing)?.first)
        assertEquals(emptyList<Pair<String, JSONObject>>(), idle.commands + empty.commands)
    }

    // --- TV remote keys: the TV's own channel, no Cast state needed -------------------------

    private val keyActions = WidgetAction.entries.filter { it.key != null }

    private fun keyAnswer(key: String, confirmation: String = "not_checked"): String {
        val silent = JSONObject().put("isOn", JSONObject.NULL).put("currentApp", JSONObject.NULL).put("volume", JSONObject.NULL)
        return JSONObject().put("id", 1).put("ok", true).put(
            "result",
            JSONObject().put(
                "result",
                JSONObject().put("key", key).put("deviceId", TV).put("confirmation", confirmation)
                    .put("detail", "").put("before", silent).put("after", silent),
            ),
        ).toString()
    }

    private fun remote(reply: (JSONObject) -> String = { keyAnswer(it.getString("key")) }) =
        FakeBridge { method, params ->
            when (method) {
                "discover_devices" -> devicesAnswer(TV)
                "press_remote_key" -> reply(params)
                else -> error("a remote key reads no Cast status: $method")
            }
        }

    @Test
    fun theWidgetPressesTheVolumeMuteArrowsOkBackHomePowerAndAssistantKeys() {
        assertEquals(
            listOf("mute", "volume_down", "volume_up", "power", "up", "down", "left", "right", "ok", "back", "home", "assistant"),
            keyActions.map { it.key },
        )
    }

    @Test
    fun eachKeyIsOnePressOfExactlyThatKeyWithoutReadingTheCastStatus() {
        for (action in keyActions) {
            val bridge = remote()

            val outcome = runner(bridge).run(action, SELECTED, "tap-$action")

            assertEquals(listOf("press_remote_key"), bridge.methods)
            val params = commandOf(bridge)!!.second
            assertEquals(action.key, params.getString("key"))
            assertEquals(TV, params.getString("deviceId"))
            assertEquals(WidgetOutcome.KeySent(action, "not_checked"), outcome)
        }
    }

    @Test
    fun aKeyForATvUnknownToThisProcessIsPressedAfterOneReadOnlyDiscovery() {
        var known = false
        val bridge = remote { if (known) keyAnswer("ok") else errorAnswer("device_not_found") }
        val discovering = FakeBridge { method, params ->
            if (method == "discover_devices") devicesAnswer(OTHER_TV, TV).also { known = true } else bridge(
                JSONObject().put("id", 1).put("method", method).put("params", params).toString(),
            )
        }

        val outcome = runner(discovering).run(WidgetAction.OK, SELECTED, "tap")

        assertEquals(listOf("press_remote_key", "discover_devices", "press_remote_key"), discovering.methods)
        assertEquals(WidgetOutcome.KeySent(WidgetAction.OK, "not_checked"), outcome)
    }

    @Test
    fun aKeyForATvMissingFromDiscoveryOrWhenDiscoveryFailsIsNeverPressed() {
        val missing = FakeBridge { method, _ ->
            if (method == "discover_devices") devicesAnswer(OTHER_TV) else errorAnswer("device_not_found")
        }
        val failing = FakeBridge { method, _ ->
            if (method == "discover_devices") errorAnswer("discovery_failed") else errorAnswer("device_not_found")
        }
        val crashing = FakeBridge { method, _ ->
            if (method == "discover_devices") error("no network") else errorAnswer("device_not_found")
        }

        assertEquals(WidgetOutcome.NotFound, runner(missing).run(WidgetAction.HOME, SELECTED, "a"))
        assertEquals(WidgetOutcome.StatusUnreadable, runner(failing).run(WidgetAction.HOME, SELECTED, "b"))
        assertEquals(WidgetOutcome.StatusUnreadable, runner(crashing).run(WidgetAction.HOME, SELECTED, "c"))
        for (bridge in listOf(missing, failing, crashing)) {
            assertEquals(listOf("press_remote_key", "discover_devices"), bridge.methods)
        }
    }

    @Test
    fun aKeyWhoseTapAgedDuringTheDiscoveryIsNotPressed() {
        var asked = 0
        val bridge = FakeBridge { method, _ ->
            if (method == "discover_devices") devicesAnswer(TV) else errorAnswer("device_not_found")
        }

        val outcome = runner(bridge).run(WidgetAction.UP, SELECTED, "tap", mayCommand = { ++asked == 1 })

        assertEquals(WidgetOutcome.Expired, outcome)
        assertEquals(listOf("press_remote_key", "discover_devices"), bridge.methods)
    }

    @Test
    fun aKeyRefusedOrUnansweredIsNeverPressedAgain() {
        val unpaired = remote { errorAnswer("pairing_required") }
        val timedOut = remote { errorAnswer("timeout") }
        val crashed = remote { error("process died while the key was sent") }
        val noPython = remote { throw PythonUnavailable(IllegalStateException("no Python")) }

        assertEquals(WidgetOutcome.NotSent("pairing_required"), runner(unpaired).run(WidgetAction.POWER, SELECTED, "a"))
        assertEquals(WidgetOutcome.MaybeSent("press_remote_key"), runner(timedOut).run(WidgetAction.POWER, SELECTED, "b"))
        assertEquals(WidgetOutcome.MaybeSent("press_remote_key"), runner(crashed).run(WidgetAction.POWER, SELECTED, "c"))
        assertEquals(WidgetOutcome.NotSent("backend_unavailable"), runner(noPython).run(WidgetAction.POWER, SELECTED, "d"))
        for (bridge in listOf(unpaired, timedOut, crashed, noPython)) {
            assertEquals(listOf("press_remote_key"), bridge.methods)
        }
        val rerun = remote()
        assertEquals(WidgetOutcome.MaybeSent("press_remote_key"), runner(rerun).run(WidgetAction.POWER, SELECTED, "b"))
        assertEquals(emptyList<String>(), rerun.methods)
    }

    @Test
    fun aKeyIsNotPressedWhenTheTapMayNoLongerRun() {
        val bridge = remote()

        assertEquals(WidgetOutcome.Expired, runner(bridge).run(WidgetAction.LEFT, SELECTED, "tap", mayCommand = { false }))
        assertEquals(emptyList<String>(), bridge.methods)
    }

    // --- Command outcomes: one send, never a retry ------------------------------------------

    @Test
    fun aConfirmedCommandCarriesTheStateTheTvReported() {
        val bridge = tv(status(playback = "playing")) { commandAnswer(it, "confirmed", status(playback = "paused")) }

        val outcome = runner(bridge).run(WidgetAction.PLAY_PAUSE, SELECTED, "tap") as WidgetOutcome.Sent

        assertEquals("confirmed", outcome.confirmation)
        assertEquals("paused", outcome.observed?.playback)
    }

    @Test
    fun anUnconfirmedCommandIsNeverSentAgain() {
        val bridge = tv { commandAnswer(it, "unconfirmed", status()) }

        val outcome = runner(bridge).run(WidgetAction.PLAY_PAUSE, SELECTED, "tap")

        assertEquals("unconfirmed", (outcome as WidgetOutcome.Sent).confirmation)
        assertEquals(listOf("get_status", "pause"), bridge.methods)
    }

    @Test
    fun everyAmbiguousFailureIsReportedOnceAndNeverRetried() {
        val failures: List<() -> String> = listOf(
            { errorAnswer("timeout") },
            { errorAnswer("device_unavailable") },
            { errorAnswer("command_rejected") },
            { errorAnswer("internal_error") },
            { throw java.util.concurrent.TimeoutException("no answer in time") },
            { throw IllegalStateException("bridge broke after the request") },
        )
        for ((index, failure) in failures.withIndex()) {
            val bridge = tv { failure() }

            val outcome = runner(bridge).run(WidgetAction.STOP, SELECTED, "tap-$index")

            assertEquals("case $index", WidgetOutcome.MaybeSent("stop"), outcome)
            assertEquals("case $index", listOf("get_status", "stop"), bridge.methods)
        }
    }

    @Test
    fun aRefusalBeforeAnySendIsNotSentAndNotRetried() {
        val refused = tv { errorAnswer("unsupported_operation") }
        val noPython = tv { throw PythonUnavailable(IllegalStateException("no Python")) }

        assertEquals(WidgetOutcome.NotSent("unsupported_operation"), runner(refused).run(WidgetAction.STOP, SELECTED, "a"))
        assertEquals(WidgetOutcome.NotSent("backend_unavailable"), runner(noPython).run(WidgetAction.STOP, SELECTED, "b"))
        assertEquals(listOf("get_status", "stop"), refused.methods)
        assertEquals(listOf("get_status", "stop"), noPython.methods)
    }

    // --- A tap re-run after a process death never sends twice -------------------------------

    @Test
    fun aTapRunAgainAfterItsCommandWasHandedOverSendsNothing() {
        val crashing = tv { throw IllegalStateException("process died while the command ran") }
        runner(crashing).run(WidgetAction.STOP, SELECTED, "tap-1")
        val rerun = tv()

        val outcome = runner(rerun).run(WidgetAction.STOP, SELECTED, "tap-1")

        assertEquals(1, crashing.commands.size)
        assertEquals(WidgetOutcome.MaybeSent("stop"), outcome)
        assertEquals(listOf("get_status"), rerun.methods)
    }

    @Test
    fun aTapRunAgainBeforeItsCommandWasHandedOverStillSendsItOnce() {
        val readFailed = FakeBridge { _, _ -> throw IllegalStateException("process died during the read") }
        runner(readFailed).run(WidgetAction.STOP, SELECTED, "tap-1")
        val rerun = tv()

        runner(rerun).run(WidgetAction.STOP, SELECTED, "tap-1")

        assertEquals(emptyList<Pair<String, JSONObject>>(), readFailed.commands)
        assertEquals(listOf("stop"), rerun.commands.map { it.first })
    }

    @Test
    fun eachNewTapIsItsOwnSingleCommand() {
        val bridge = tv()

        runner(bridge).run(WidgetAction.STOP, SELECTED, "tap-1")
        runner(bridge).run(WidgetAction.STOP, SELECTED, "tap-2")

        assertEquals(listOf("stop", "stop"), bridge.commands.map { it.first })
    }

    @Test
    fun theGuardKeepsEveryClaimedTapNotOnlyTheLast() {
        val guard = TapGuard(store)

        assertTrue(guard.claim("a"))
        assertEquals(false, guard.claim("a"))
        assertTrue(guard.claim("b"))
        assertEquals(false, guard.claim("a"))
        assertEquals(setOf("a", "b"), ClaimedTaps.parse(store.claimed)!!.keys)
    }

    @Test
    fun aTapWhoseJobStartsTooLateNeitherReadsNorSends() {
        val bridge = tv()

        for (age in listOf(WidgetActionRunner.MAX_TAP_AGE_MILLIS + 1, 86_400_000L, -1L)) {
            val outcome = runner(bridge).run(WidgetAction.STOP, SELECTED, "tap-$age", tapAgeMillis = age)

            assertEquals(WidgetOutcome.Expired, outcome)
        }
        assertEquals(emptyList<String>(), bridge.methods)
        assertEquals(null, store.claimed)
    }

    @Test
    fun aTapStartedInTimeIsHandled() {
        val bridge = tv()

        runner(bridge).run(WidgetAction.STOP, SELECTED, "tap", tapAgeMillis = WidgetActionRunner.MAX_TAP_AGE_MILLIS)

        assertEquals(listOf("get_status", "stop"), bridge.methods)
    }

    @Test
    fun aCommandIsNotClaimedWhenTheCheckJustBeforeItFails() {
        val bridge = tv()

        val outcome = runner(bridge).run(WidgetAction.STOP, SELECTED, "tap", mayCommand = { false })

        assertEquals(WidgetOutcome.Expired, outcome)
        assertEquals(listOf("get_status"), bridge.methods)
        assertEquals(null, store.claimed)
    }

    @Test
    fun refreshNeverAsksToClaimACommand() {
        val bridge = tv()

        runner(bridge).run(WidgetAction.REFRESH, SELECTED, "tap", mayCommand = { error("not asked") })

        assertEquals(listOf("get_status"), bridge.methods)
    }

    // --- C7: the command names its order, the tap, as the bridge's requestId ---------------

    @Test
    fun theCommandCarriesItsTapAsTheApplicationRequestId() {
        val bridge = tv(status(playback = "playing"))
        val tap = "0f8fad5b-d9cb-469f-a165-70867728950e"

        runner(bridge).run(WidgetAction.PLAY_PAUSE, SELECTED, tap)

        val command = bridge.lines.single { it.getString("method") == "pause" }
        assertEquals(tap, command.getString("requestId"))
        // Status reads are not the order: each is its own bridge message.
        assertTrue(bridge.lines.filter { it.getString("method") == "get_status" }.none { it.has("requestId") })
    }

    @Test
    fun twoTapsAreTwoOrdersAndARerunOfOneTapSendsNothingMore() {
        val bridge = tv(status(playback = "playing"))
        val widget = runner(bridge)

        widget.run(WidgetAction.PLAY_PAUSE, SELECTED, "tap-a")
        widget.run(WidgetAction.PLAY_PAUSE, SELECTED, "tap-b")
        widget.run(WidgetAction.PLAY_PAUSE, SELECTED, "tap-a") // Android runs the job again

        val orders = bridge.lines.filter { it.getString("method") == "pause" }.map { it.getString("requestId") }
        assertEquals(listOf("tap-a", "tap-b"), orders) // TapGuard's existing claim: no resend
    }
}
