package io.github.guillaumeboileaupro.controltv

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class WidgetPresentationTest {
    private fun state(
        playback: String? = "playing",
        title: String? = "A film",
        level: Double? = 0.45,
        muted: Boolean? = false,
        connected: Boolean = true,
    ) = TvState(connected, playback, title, null, level, false, muted)

    @Test
    fun withoutASelectedTvItAsksToChooseOneInTheApp() {
        val view = WidgetPresentation.present(WidgetOutcome.NoSelection, null)

        assertEquals("control-TV", view.title)
        assertEquals("Open control-TV and choose a TV.", view.line)
    }

    @Test
    fun theSummaryStatesOnlyWhatTheTvReported() {
        assertEquals("Playing · A film · 45%", WidgetPresentation.summary(state()))
        assertEquals("Paused · A film · Muted", WidgetPresentation.summary(state(playback = "paused", muted = true)))
        assertEquals("Nothing playing · 45%", WidgetPresentation.summary(state(playback = null)))
        assertEquals("Playing · A film", WidgetPresentation.summary(state(level = null, muted = null)))
        assertEquals("Not connected.", WidgetPresentation.summary(state(connected = false)))
    }

    @Test
    fun onlyAConfirmedCommandShowsTheNewStateAsDone() {
        val confirmed = WidgetPresentation.present(WidgetOutcome.Sent("pause", "confirmed", state(playback = "paused")), SELECTED)
        val unconfirmed = WidgetPresentation.present(WidgetOutcome.Sent("pause", "unconfirmed", state()), SELECTED)
        val unchecked = WidgetPresentation.present(WidgetOutcome.Sent("pause", "not_checked", null), SELECTED)

        assertEquals("Paused · A film · 45%", confirmed.line)
        assertEquals(false, confirmed.playing)
        assertTrue(unconfirmed.line.startsWith("Sent, but the TV hasn't shown it yet"))
        assertEquals("Sent. Tap ↻ to check.", unchecked.line)
    }

    @Test
    fun anAmbiguousCommandSaysItMayOrMayNotHaveArrivedAndPointsToRefresh() {
        val view = WidgetPresentation.present(WidgetOutcome.MaybeSent("stop"), SELECTED)

        assertTrue(view.line.contains("may or may not have reached the TV"))
        assertTrue(view.line.contains("Tap ↻ to check"))
        assertFalse(view.line.contains("again", ignoreCase = true))
    }

    @Test
    fun theWordsNeverShowATechnicalCode() {
        val outcomes = listOf(
            WidgetOutcome.NotFound,
            WidgetOutcome.Expired,
            WidgetOutcome.Unavailable,
            WidgetOutcome.StatusUnreadable,
            WidgetOutcome.NotSent("unsupported_operation"),
            WidgetOutcome.NotSent("invalid_argument"),
            WidgetOutcome.MaybeSent("set_volume"),
            WidgetOutcome.NoCommand(WidgetActionRunner.REASON_VOLUME_MAX, state()),
        )
        for (outcome in outcomes) {
            val view = WidgetPresentation.present(outcome, SELECTED)
            assertFalse(view.line, Regex("[a-z]+_[a-z]+|python|bridge|cast|exception", RegexOption.IGNORE_CASE).containsMatchIn(view.line))
            assertEquals("Living room", view.title)
        }
    }

    @Test
    fun aTvWithoutANameIsCalledYourTv() {
        val view = WidgetPresentation.present(WidgetOutcome.NotFound, Selection(TV, ""))

        assertEquals("Your TV", view.title)
    }

    @Test
    fun whileWorkingItKeepsTheLastIconsAndSaysSo() {
        val previous = WidgetView("Living room", "Playing", playing = true, muted = false)

        val busy = WidgetPresentation.busy(previous, SELECTED)

        assertEquals(WidgetView("Living room", "Working…", playing = true, muted = false, busy = true), busy)
    }
}
