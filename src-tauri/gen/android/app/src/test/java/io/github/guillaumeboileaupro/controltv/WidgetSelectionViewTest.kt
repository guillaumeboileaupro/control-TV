package io.github.guillaumeboileaupro.controltv

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * The widget never shows one TV while its buttons target another, and a refresh that times out
 * is never worded as a command that may have been sent (Codex review of #33, 5bfd753).
 */
class WidgetSelectionViewTest {
    private val other = Selection("tv-2", "Bedroom")

    @Test
    fun aViewIsTaggedWithTheTvItDescribes() {
        val view = WidgetPresentation.present(WidgetOutcome.StatusUnreadable, SELECTED)

        assertEquals(TV, view.deviceId)
        assertEquals("tv-2", WidgetPresentation.busy(null, other).deviceId)
    }

    @Test
    fun anotherTvsViewIsNotCarriedIntoTheBusyView() {
        val playingOnA = WidgetView("Living room", "Playing", playing = true, muted = true, deviceId = TV)

        val busyOnB = WidgetPresentation.busy(playingOnA, other)

        assertEquals("Bedroom", busyOnB.title)
        assertNull(busyOnB.playing)
        assertNull(busyOnB.muted)
        assertEquals("tv-2", busyOnB.deviceId)
    }

    @Test
    fun theSameTvsViewKeepsItsStateWhileBusy() {
        val playingOnA = WidgetView("Living room", "Playing", playing = true, muted = false, deviceId = TV)

        val busy = WidgetPresentation.busy(playingOnA, SELECTED)

        assertEquals(true, busy.playing)
        assertEquals(false, busy.muted)
    }

    @Test
    fun noSelectionHasNoTv() {
        assertNull(WidgetPresentation.present(WidgetOutcome.NoSelection, SELECTED).deviceId)
        assertNull(WidgetPresentation.present(WidgetOutcome.Busy, null).deviceId)
    }

    private class TimingOut : TransactionRunner {
        override fun <T> tryTransaction(
            timeoutMillis: Long,
            body: (BridgeTransaction) -> T,
        ): TransactionResult<T> = TransactionResult.TimedOutAfterStart
    }

    private fun run(action: WidgetAction): WidgetOutcome {
        val store = MemoryWidgetStore().apply { selected = SELECTED }
        val now = 1_000_000L
        return WidgetJob(store, TimingOut(), { now }).run(TapSpec(action, "tap-1", now, deviceId = TV))
    }

    @Test
    fun aRefreshThatTimesOutIsUnreadableNotAPossibleCommand() {
        assertEquals(WidgetOutcome.StatusUnreadable, run(WidgetAction.REFRESH))
    }

    @Test
    fun aCommandThatTimesOutStaysAmbiguous() {
        for (action in listOf(WidgetAction.PLAY_PAUSE, WidgetAction.STOP, WidgetAction.MUTE)) {
            assertEquals(WidgetOutcome.MaybeSent("command"), run(action))
        }
    }
}

/** A tap whose background job could not be recorded never leaves the widget on "Working…". */
class WidgetEnqueueFailureTest {
    @Test
    fun aJobThatCannotBeRecordedSaysNothingWasSent() {
        val store = MemoryWidgetStore().apply { selected = SELECTED }
        val token = store.armedToken { "token-0" }
        var renders = 0
        val handler = WidgetTapHandler(
            store = store,
            now = { 1_000_000L },
            newToken = { "token-1" },
            enqueue = { throw IllegalStateException("WorkManager unavailable") },
            render = { renders++ },
        )

        val accepted = handler.handle("PLAY_PAUSE", token)

        assertEquals(true, accepted)
        assertEquals("control-TV couldn't start. Nothing was sent.", store.lastView?.line)
        assertEquals(false, store.lastView?.busy)
        assertEquals(2, renders)
    }
}

/** A tap made for one TV never reaches another (Codex review of #33, ae14c42). */
class WidgetTapTargetTest {
    private class NeverRuns : TransactionRunner {
        var calls = 0
        override fun <T> tryTransaction(
            timeoutMillis: Long,
            body: (BridgeTransaction) -> T,
        ): TransactionResult<T> {
            calls++
            return TransactionResult.Busy
        }
    }

    @Test
    fun theTapRecordsTheTvSelectedWhenTapped() {
        val store = MemoryWidgetStore().apply { selected = SELECTED }
        val queued = mutableListOf<TapSpec>()
        val handler = WidgetTapHandler(store, { 1_000_000L }, { "token-1" }, { queued += it }, {})

        handler.handle("STOP", store.armedToken { "token-0" })

        assertEquals(TV, queued.single().deviceId)
    }

    @Test
    fun aJobForATvNoLongerSelectedSendsNothing() {
        val store = MemoryWidgetStore().apply { selected = Selection("tv-2", "Bedroom") }
        val bridge = NeverRuns()
        val now = 1_000_000L

        val outcome = WidgetJob(store, bridge, { now }).run(TapSpec(WidgetAction.STOP, "t", now, deviceId = TV))

        assertEquals(WidgetOutcome.SelectionChanged, outcome)
        assertEquals(0, bridge.calls)
        assertEquals(
            "The TV changed before that tap ran. Nothing was sent.",
            WidgetPresentation.present(outcome, store.selected).line,
        )
    }

    @Test
    fun aJobQueuedWithoutATvSendsNothing() {
        val store = MemoryWidgetStore().apply { selected = SELECTED }
        val bridge = NeverRuns()

        val outcome = WidgetJob(store, bridge, { 1L }).run(TapSpec(WidgetAction.STOP, "t", 1L, deviceId = null))

        assertEquals(WidgetOutcome.SelectionChanged, outcome)
        assertEquals(0, bridge.calls)
    }
}
