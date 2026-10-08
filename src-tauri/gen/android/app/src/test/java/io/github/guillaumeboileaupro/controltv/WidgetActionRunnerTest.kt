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

        assertEquals(WidgetOutcome.StatusUnreadable, runner(discoveryFails).run(WidgetAction.MUTE, SELECTED, "a"))
        assertEquals(WidgetOutcome.StatusUnreadable, runner(statusFails).run(WidgetAction.MUTE, SELECTED, "b"))
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

    @Test
    fun muteSendsTheAbsoluteOppositeOfTheReportedStateAndNothingWhenItIsUnknown() {
        val unmuted = tv(status(muted = false))
        val muted = tv(status(muted = true))
        val unknown = tv(status(muted = null))

        runner(unmuted).run(WidgetAction.MUTE, SELECTED, "a")
        runner(muted).run(WidgetAction.MUTE, SELECTED, "b")
        val outcome = runner(unknown).run(WidgetAction.MUTE, SELECTED, "c")

        assertEquals(true, commandOf(unmuted)?.second?.getBoolean("muted"))
        assertEquals(false, commandOf(muted)?.second?.getBoolean("muted"))
        assertTrue(outcome is WidgetOutcome.NoCommand)
        assertEquals(emptyList<Pair<String, JSONObject>>(), unknown.commands)
    }

    @Test
    fun volumeMovesTenPointsFromTheReportedLevelWithinZeroToOne() {
        val cases = listOf(
            Triple(0.47, WidgetAction.VOLUME_UP, 0.57),
            Triple(0.47, WidgetAction.VOLUME_DOWN, 0.37),
            Triple(0.95, WidgetAction.VOLUME_UP, 1.0),
            Triple(0.05, WidgetAction.VOLUME_DOWN, 0.0),
        )
        for ((level, action, expected) in cases) {
            val bridge = tv(status(level = level))

            runner(bridge).run(action, SELECTED, "tap-$level-$action")

            val (method, params) = commandOf(bridge)!!
            assertEquals("set_volume", method)
            assertEquals("$level $action", expected, params.getDouble("level"), 1e-9)
        }
    }

    @Test
    fun volumeSendsNothingWithoutAReportedAdjustableLevelOrAtTheLimit() {
        val cases = listOf(
            status(level = null) to WidgetAction.VOLUME_UP,
            status(controlType = "fixed") to WidgetAction.VOLUME_DOWN,
            status(level = 1.0) to WidgetAction.VOLUME_UP,
            status(level = 0.0) to WidgetAction.VOLUME_DOWN,
        )
        for ((state, action) in cases) {
            val bridge = tv(state)

            val outcome = runner(bridge).run(action, SELECTED, "tap")

            assertTrue(outcome is WidgetOutcome.NoCommand)
            assertEquals(emptyList<Pair<String, JSONObject>>(), bridge.commands)
        }
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
        runner(crashing).run(WidgetAction.MUTE, SELECTED, "tap-1")
        val rerun = tv()

        val outcome = runner(rerun).run(WidgetAction.MUTE, SELECTED, "tap-1")

        assertEquals(1, crashing.commands.size)
        assertEquals(WidgetOutcome.MaybeSent("set_muted"), outcome)
        assertEquals(listOf("get_status"), rerun.methods)
    }

    @Test
    fun aTapRunAgainBeforeItsCommandWasHandedOverStillSendsItOnce() {
        val readFailed = FakeBridge { _, _ -> throw IllegalStateException("process died during the read") }
        runner(readFailed).run(WidgetAction.MUTE, SELECTED, "tap-1")
        val rerun = tv()

        runner(rerun).run(WidgetAction.MUTE, SELECTED, "tap-1")

        assertEquals(emptyList<Pair<String, JSONObject>>(), readFailed.commands)
        assertEquals(listOf("set_muted"), rerun.commands.map { it.first })
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
}
