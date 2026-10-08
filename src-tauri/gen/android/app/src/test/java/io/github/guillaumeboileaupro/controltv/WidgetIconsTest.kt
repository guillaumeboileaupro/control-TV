package io.github.guillaumeboileaupro.controltv

import org.junit.Assert.assertEquals
import org.junit.Test

/** What the widget's play/pause and mute buttons actually show, from each outcome. */
class WidgetIconsTest {
    private val neutralPlayPause = ButtonLook(R.drawable.ic_widget_play_pause, R.string.widget_play_pause)
    private val neutralMute = ButtonLook(R.drawable.ic_widget_speaker, R.string.widget_mute_unmute)

    private fun state(playback: String? = "playing", muted: Boolean? = false) =
        TvState(true, playback, "A film", null, 0.4, false, muted)

    private fun looks(outcome: WidgetOutcome): Pair<ButtonLook, ButtonLook> {
        val view = WidgetPresentation.present(outcome, SELECTED)
        return WidgetIcons.playPause(view.playing) to WidgetIcons.mute(view.muted)
    }

    @Test
    fun aReportedStateShowsItsOwnIconsAndLabels() {
        assertEquals(ButtonLook(R.drawable.ic_widget_pause, R.string.widget_pause), WidgetIcons.playPause(true))
        assertEquals(ButtonLook(R.drawable.ic_widget_play, R.string.widget_play), WidgetIcons.playPause(false))
        assertEquals(ButtonLook(R.drawable.ic_widget_volume_off, R.string.widget_unmute), WidgetIcons.mute(true))
        assertEquals(ButtonLook(R.drawable.ic_widget_volume, R.string.widget_mute), WidgetIcons.mute(false))
    }

    @Test
    fun anAmbiguousPauseNeverShowsPlayingOrPausedAndAnAmbiguousMuteNeverShowsUnmuted() {
        assertEquals(neutralPlayPause to neutralMute, looks(WidgetOutcome.MaybeSent("pause")))
        assertEquals(neutralPlayPause to neutralMute, looks(WidgetOutcome.MaybeSent("set_muted")))
    }

    @Test
    fun anUncheckedCommandOrAFailureShowsNoStateEither() {
        for (outcome in listOf(
            WidgetOutcome.Sent("pause", "not_checked", null),
            WidgetOutcome.NotSent("unsupported_operation"),
            WidgetOutcome.StatusUnreadable,
            WidgetOutcome.NotFound,
            WidgetOutcome.Busy,
            WidgetOutcome.Expired,
            WidgetOutcome.Unavailable,
        )) {
            assertEquals(outcome.toString(), neutralPlayPause to neutralMute, looks(outcome))
        }
    }

    @Test
    fun onlyAStateTheTvReportedSetsTheIcons() {
        val confirmedPause = WidgetOutcome.Sent("pause", "confirmed", state(playback = "paused", muted = true))
        val unconfirmedWithReport = WidgetOutcome.Sent("pause", "unconfirmed", state(playback = "playing"))

        assertEquals(
            ButtonLook(R.drawable.ic_widget_play, R.string.widget_play) to
                ButtonLook(R.drawable.ic_widget_volume_off, R.string.widget_unmute),
            looks(confirmedPause),
        )
        // Not confirmed: the TV still reports playing, so the button still offers Pause.
        assertEquals(ButtonLook(R.drawable.ic_widget_pause, R.string.widget_pause), looks(unconfirmedWithReport).first)
    }

    @Test
    fun anUnreportedPlaybackOrMuteStateFromAReadStaysNeutral() {
        val read = WidgetOutcome.Status(state(playback = "something-new", muted = null))

        assertEquals(neutralPlayPause to neutralMute, looks(read))
    }
}
