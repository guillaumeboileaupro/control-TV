package io.github.guillaumeboileaupro.controltv

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicLong

/**
 * The widget from the pending-intent tap to the shared bridge: the tap handler (what the
 * receiver does), the background job (what WorkManager runs, possibly twice) and the real
 * [BridgeRelay] the window shares, with a scripted shared layer behind it.
 */
class WidgetBoundaryTest {
    private val store = MemoryWidgetStore().apply { selected = SELECTED }
    private val clock = AtomicLong(1_000_000L)
    private val queued = mutableListOf<TapSpec>()
    private var tokens = 0
    private val handler = WidgetTapHandler(
        store = store,
        now = { clock.get() },
        newToken = { "token-${++tokens}" },
        enqueue = { queued += it },
        render = {},
    )

    private fun tvBridge() = FakeBridge { method, _ ->
        when (method) {
            "get_status" -> statusAnswer(status(playback = "playing"))
            "discover_devices" -> devicesAnswer(TV)
            else -> commandAnswer(method, "confirmed", status(playback = "paused"))
        }
    }

    // --- Tap identity: the same interaction delivered twice is one tap ----------------------

    @Test
    fun theSameTapDeliveredTwiceIsAcceptedOnceAndRunsOneCommand() {
        val token = store.armedToken { "token-0" }
        val bridge = tvBridge()
        val relay = BridgeRelay(bridge)

        assertTrue(handler.handle("PLAY_PAUSE", token))
        assertFalse(handler.handle("PLAY_PAUSE", token))
        for (tap in queued) WidgetJob(store, relay, clock::get).run(tap)

        assertEquals(1, queued.size)
        assertEquals(token, queued.single().tapId)
        assertEquals(listOf("pause"), bridge.commands.map { it.first })
        relay.shutdown()
    }

    @Test
    fun aStaleOrForeignTokenIsIgnoredAndAFreshDrawingAcceptsTheNextTap() {
        val first = store.armedToken { "token-0" }

        assertFalse(handler.handle("STOP", "not-armed"))
        assertTrue(handler.handle("STOP", first))
        val next = store.armedToken { error("already armed") }

        assertTrue(next != first)
        assertTrue(handler.handle("STOP", next))
        assertEquals(2, queued.size)
    }

    @Test
    fun anUnknownActionOrAMissingTokenIsIgnoredWithoutConsumingTheToken() {
        val token = store.armedToken { "token-0" }

        assertFalse(handler.handle("REBOOT", token))
        assertFalse(handler.handle(null, token))
        assertFalse(handler.handle("STOP", null))
        assertFalse(handler.handle("STOP", ""))

        assertEquals(emptyList<TapSpec>(), queued)
        assertEquals(token, store.armed)
    }

    @Test
    fun withNoChosenTvATapQueuesNothing() {
        store.selected = null
        val token = store.armedToken { "token-0" }

        assertTrue(handler.handle("MUTE", token))

        assertEquals(emptyList<TapSpec>(), queued)
        assertEquals(WidgetPresentation.CHOOSE_TV, store.lastView?.line)
    }

    // --- The job: WorkManager re-execution, busy window, late taps -------------------------

    @Test
    fun aJobRunAgainByWorkManagerSendsItsCommandOnlyOnce() {
        val bridge = tvBridge()
        val relay = BridgeRelay(bridge)
        val tap = TapSpec(WidgetAction.MUTE, "token-1", clock.get())

        val first = WidgetJob(store, relay, clock::get).run(tap)
        val again = WidgetJob(store, relay, clock::get).run(tap)

        assertTrue(first is WidgetOutcome.Sent)
        assertEquals(WidgetOutcome.MaybeSent("set_muted"), again)
        assertEquals(listOf("set_muted"), bridge.commands.map { it.first })
        relay.shutdown()
    }

    @Test
    fun aJobWhoseCommandWasCutShortByAProcessDeathNeverSendsWhenRunAgain() {
        val dying = FakeBridge { method, _ ->
            if (method == "get_status") statusAnswer() else throw IllegalStateException("process died")
        }
        val tap = TapSpec(WidgetAction.STOP, "token-1", clock.get())
        WidgetJob(store, BridgeRelay(dying), clock::get).run(tap)
        val afterRestart = tvBridge()

        val outcome = WidgetJob(store, BridgeRelay(afterRestart), clock::get).run(tap)

        assertEquals(1, dying.commands.size)
        assertEquals(WidgetOutcome.MaybeSent("stop"), outcome)
        assertEquals(emptyList<Pair<String, JSONObject>>(), afterRestart.commands)
    }

    @Test
    fun aTapWhileTheWindowUsesTheBridgeIsTurnedDownAndNeverEntersPython() {
        val release = CountDownLatch(1)
        val started = CountDownLatch(1)
        val bridge = FakeBridge { method, _ ->
            if (method == "discover_devices") { started.countDown(); release.await(5, TimeUnit.SECONDS) }
            devicesAnswer(TV)
        }
        val relay = BridgeRelay(bridge)
        relay.submit(
            JSONObject().put("id", 1).put("method", "discover_devices").put("params", JSONObject()).toString(),
            onResponse = {},
            onFailure = {},
        )
        assertTrue(started.await(5, TimeUnit.SECONDS))

        val outcome = WidgetJob(store, relay, clock::get).run(TapSpec(WidgetAction.STOP, "token-1", clock.get()))
        release.countDown()
        Thread.sleep(200)

        assertEquals(WidgetOutcome.Busy, outcome)
        assertEquals(listOf("discover_devices"), bridge.methods)
        assertEquals(null, store.claimed)
        relay.shutdown()
    }

    @Test
    fun aTapThatAgesPastItsLimitDuringTheReadsSendsNoCommand() {
        // The status read takes 70 s: the tap was young when the job started, but the check
        // just before the command claim finds it past its 60 s lifetime.
        val bridge = FakeBridge { method, _ ->
            clock.addAndGet(70_000)
            if (method == "get_status") statusAnswer() else commandAnswer(method, "confirmed")
        }
        val tap = TapSpec(WidgetAction.PLAY_PAUSE, "token-1", clock.get())

        val outcome = WidgetJob(store, BridgeRelay(bridge), clock::get).run(tap)

        assertEquals(WidgetOutcome.Expired, outcome)
        assertEquals(listOf("get_status"), bridge.methods)
        assertEquals(null, store.claimed)
    }

    @Test
    fun aJobStartedAfterTheTapsLifetimeDoesNothing() {
        val bridge = tvBridge()
        val tap = TapSpec(WidgetAction.STOP, "token-1", clock.get() - WidgetActionRunner.MAX_TAP_AGE_MILLIS - 1)

        val outcome = WidgetJob(store, BridgeRelay(bridge), clock::get).run(tap)

        assertEquals(WidgetOutcome.Expired, outcome)
        assertEquals(emptyList<String>(), bridge.methods)
    }

    @Test
    fun aJobForAChosenTvMissingFromDiscoverySendsNothingAndNeverUsesAnother() {
        val bridge = FakeBridge { method, _ ->
            when (method) {
                "get_status" -> errorAnswer("device_not_found")
                else -> devicesAnswer(OTHER_TV)
            }
        }

        val outcome = WidgetJob(store, BridgeRelay(bridge), clock::get)
            .run(TapSpec(WidgetAction.STOP, "token-1", clock.get()))

        assertEquals(WidgetOutcome.NotFound, outcome)
        assertEquals(listOf("get_status", "discover_devices"), bridge.methods)
    }

    @Test
    fun aJobWithNoChosenTvSendsNothing() {
        store.selected = null
        val bridge = tvBridge()

        val outcome = WidgetJob(store, BridgeRelay(bridge), clock::get)
            .run(TapSpec(WidgetAction.PLAY_PAUSE, "token-1", clock.get()))

        assertEquals(WidgetOutcome.NoSelection, outcome)
        assertEquals(emptyList<String>(), bridge.methods)
    }
}
