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
        volumeFixed: Boolean = false,
    ) = TvState(connected, playback, title, null, level, volumeFixed, muted)

    @Test
    fun withoutASelectedTvItAsksToChooseOneInTheApp() {
        val view = WidgetPresentation.present(WidgetOutcome.NoSelection, null)

        assertEquals("control-TV", view.title)
        assertEquals("Tap here to choose a TV.", view.line)
    }

    @Test
    fun theSummaryStatesOnlyWhatTheTvReported() {
        assertEquals("Playing · A film · 45%", WidgetPresentation.summary(state()))
        assertEquals("Paused · A film · Muted", WidgetPresentation.summary(state(playback = "paused", muted = true)))
        assertEquals("Nothing playing · 45%", WidgetPresentation.summary(state(playback = null)))
        assertEquals("Playing · A film", WidgetPresentation.summary(state(level = null, muted = null)))
        assertEquals("Not connected.", WidgetPresentation.summary(state(connected = false)))
        assertEquals("Status undetermined · 45%", WidgetPresentation.summary(state(playback = "future_state")))
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
    fun confirmedCastMuteOnAFixedVolumeTvDoesNotClaimThePhysicalSoundChanged() {
        val outcome = WidgetOutcome.Sent(
            "set_muted",
            "confirmed",
            state(muted = true, volumeFixed = true),
        )

        val view = WidgetPresentation.present(outcome, SELECTED)

        assertEquals("Playing · A film · Muted (Cast) · TV sound may not change", view.line)
        assertEquals(true, view.muted)
    }

    @Test
    fun confirmedMuteOnAnAdjustableCastVolumeKeepsTheConciseReportedState() {
        val outcome = WidgetOutcome.Sent("set_muted", "confirmed", state(muted = true))

        val view = WidgetPresentation.present(outcome, SELECTED)

        assertEquals("Playing · A film · Muted", view.line)
        assertEquals(true, view.muted)
    }

    @Test
    fun sentButUnconfirmedMuteDoesNotClaimEitherReportedOrPhysicalMute() {
        val outcome = WidgetOutcome.Sent(
            "set_muted",
            "unconfirmed",
            state(muted = false, volumeFixed = true),
        )

        val view = WidgetPresentation.present(outcome, SELECTED)

        assertEquals("Sent, but the TV hasn't shown it yet. Tap ↻ to check.", view.line)
        assertEquals(false, view.muted)
    }

    @Test
    fun muteFailureBeforeSendSaysNothingWasSent() {
        val view = WidgetPresentation.present(WidgetOutcome.NotSent("unsupported_operation"), SELECTED)

        assertEquals("The TV can't do that now. Nothing was sent.", view.line)
        assertEquals(null, view.muted)
    }

    @Test
    fun unavailableAndAmbiguousMuteOutcomesNeverClaimMute() {
        val unavailable = WidgetPresentation.present(WidgetOutcome.Unavailable, SELECTED)
        val ambiguous = WidgetPresentation.present(WidgetOutcome.MaybeSent("set_muted"), SELECTED)

        assertEquals("control-TV couldn't start. Nothing was sent.", unavailable.line)
        assertEquals("It may or may not have reached the TV. Tap ↻ to check.", ambiguous.line)
        assertEquals(null, unavailable.muted)
        assertEquals(null, ambiguous.muted)
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
            WidgetOutcome.NotSent("pairing_required"),
            WidgetOutcome.MaybeSent("set_volume"),
            WidgetOutcome.MaybeSent(WidgetActionRunner.PRESS_KEY),
            WidgetOutcome.NoCommand(WidgetActionRunner.REASON_NOTHING_PLAYING, state()),
        ) + WidgetAction.entries.filter { it.key != null }.map { WidgetOutcome.KeySent(it, "not_checked") }
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
        val previous = WidgetView("Living room", "Playing", playing = true, muted = false, deviceId = TV)

        val busy = WidgetPresentation.busy(previous, SELECTED)

        assertEquals(
            WidgetView("Living room", "Working…", playing = true, muted = false, busy = true, deviceId = TV),
            busy,
        )
    }

    @Test
    fun aKeyIsWordedAsSentWithItsNameNeverAsDoneAndAnUnpairedTvSaysSo() {
        assertEquals("Volume + sent.", WidgetPresentation.present(WidgetOutcome.KeySent(WidgetAction.VOLUME_UP, "confirmed"), SELECTED).line)
        assertEquals("Google Assistant sent.", WidgetPresentation.present(WidgetOutcome.KeySent(WidgetAction.ASSISTANT, "not_checked"), SELECTED).line)
        assertEquals(
            "Pair this TV in control-TV first. Nothing was sent.",
            WidgetPresentation.present(WidgetOutcome.NotSent("pairing_required"), SELECTED).line,
        )
        val labels = WidgetAction.entries.filter { it.key != null }.map(WidgetPresentation::keyLabel)
        assertEquals(labels.size, labels.toSet().size)
    }
}
