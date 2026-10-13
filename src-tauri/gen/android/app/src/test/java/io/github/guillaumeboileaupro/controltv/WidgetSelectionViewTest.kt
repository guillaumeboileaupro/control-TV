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
        return WidgetJob(store, TimingOut(), { now }).run(TapSpec(action, "tap-1", now))
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
