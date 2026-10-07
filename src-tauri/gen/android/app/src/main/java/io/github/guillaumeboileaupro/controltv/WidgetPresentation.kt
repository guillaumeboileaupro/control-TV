package io.github.guillaumeboileaupro.controltv

import kotlin.math.roundToInt

/**
 * The words the widget shows, plain and short, with no technical code. A command is worded
 * as done only when the TV reported the requested state ("confirmed"); otherwise the widget
 * says what is known and points to the refresh button, never to sending again.
 */
object WidgetPresentation {
    const val APP_NAME = "control-TV"
    const val UNNAMED_TV = "Your TV"
    const val CHOOSE_TV = "Open control-TV and choose a TV."
    const val WORKING = "Working…"

    const val NO_TV_CHOSEN = "No TV chosen yet. Open control-TV and choose one."

    fun noSelection(): WidgetView = WidgetView(title = APP_NAME, line = CHOOSE_TV)

    /** After a tap with no TV chosen: worded differently from the first drawing, so the tap
     *  visibly did something. */
    fun noSelectionAfterTap(): WidgetView = WidgetView(title = APP_NAME, line = NO_TV_CHOSEN)

    fun busy(previous: WidgetView?, selection: Selection): WidgetView =
        (previous ?: WidgetView(title(selection), "")).copy(title = title(selection), line = WORKING, busy = true)

    fun present(outcome: WidgetOutcome, selection: Selection?): WidgetView {
        if (selection == null) return noSelection()
        val title = title(selection)
        return when (outcome) {
            WidgetOutcome.NoSelection -> noSelection()
            WidgetOutcome.Expired -> WidgetView(title, "That tap waited too long. Nothing was sent.")
            WidgetOutcome.Busy -> WidgetView(title, "control-TV was busy. Nothing was sent. Tap again.")
            WidgetOutcome.NotFound -> WidgetView(title, "TV not found on this network. Nothing was sent.")
            WidgetOutcome.Unavailable -> WidgetView(title, "control-TV couldn't start. Nothing was sent.")
            WidgetOutcome.StatusUnreadable -> WidgetView(title, "Couldn't reach the TV. Nothing was sent.")
            is WidgetOutcome.Status -> stateView(title, outcome.state)
            is WidgetOutcome.NoCommand -> stateView(title, outcome.state).copy(line = outcome.reason)
            is WidgetOutcome.NotSent -> WidgetView(
                title,
                if (outcome.code == "unsupported_operation") "The TV can't do that now. Nothing was sent."
                else "Nothing was sent.",
            )
            is WidgetOutcome.MaybeSent -> WidgetView(title, MAYBE_SENT)
            is WidgetOutcome.Sent -> when (outcome.confirmation) {
                "confirmed" -> outcome.observed?.let { stateView(title, it) } ?: WidgetView(title, "Done.")
                "unconfirmed" -> WidgetView(
                    title,
                    "Sent, but the TV hasn't shown it yet. Tap ↻ to check.",
                    playing = outcome.observed?.let(::isPlaying),
                    muted = outcome.observed?.muted,
                )
                // Not checked: nothing reported, so no state is shown (neutral icons).
                else -> WidgetView(title, "Sent. Tap ↻ to check.")
            }
        }
    }

    /** "Playing · Title · 45%", "Paused · Muted", "Nothing playing · 30%". */
    fun summary(state: TvState): String {
        if (!state.connected) return "Not connected."
        val playback = when (state.playback) {
            "playing" -> "Playing"
            "buffering" -> "Loading"
            "paused" -> "Paused"
            null, "idle" -> "Nothing playing"
            else -> "Status unknown"
        }
        val parts = mutableListOf(playback)
        if (state.playback in setOf("playing", "buffering", "paused")) state.title?.let { parts += it }
        when {
            state.muted == true -> parts += "Muted"
            state.volumeLevel != null -> parts += "${(state.volumeLevel * 100).roundToInt()}%"
        }
        return parts.joinToString(" · ")
    }

    private const val MAYBE_SENT = "It may or may not have reached the TV. Tap ↻ to check."

    private fun stateView(title: String, state: TvState) =
        WidgetView(title, summary(state), playing = isPlaying(state), muted = state.muted)

    private fun isPlaying(state: TvState): Boolean? = when (state.playback) {
        "playing", "buffering" -> true
        "paused", "idle", null -> false
        else -> null
    }

    private fun title(selection: Selection) = selection.name.ifBlank { UNNAMED_TV }
}
