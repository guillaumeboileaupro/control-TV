package io.github.guillaumeboileaupro.controltv

/** One widget button's picture and spoken label. */
data class ButtonLook(val drawable: Int, val description: Int)

/**
 * The play/pause and mute buttons follow only what the TV last reported. A state that is not
 * known (after a command whose outcome is ambiguous, a failure, or nothing read yet) gets a
 * neutral look, never the opposite of an assumed state: a tap then reads the TV first anyway.
 */
object WidgetIcons {
    fun playPause(playing: Boolean?): ButtonLook = when (playing) {
        true -> ButtonLook(R.drawable.ic_widget_pause, R.string.widget_pause)
        false -> ButtonLook(R.drawable.ic_widget_play, R.string.widget_play)
        null -> ButtonLook(R.drawable.ic_widget_play_pause, R.string.widget_play_pause)
    }

    fun mute(muted: Boolean?): ButtonLook = when (muted) {
        true -> ButtonLook(R.drawable.ic_widget_volume_off, R.string.widget_unmute)
        false -> ButtonLook(R.drawable.ic_widget_volume, R.string.widget_mute)
        null -> ButtonLook(R.drawable.ic_widget_speaker, R.string.widget_mute_unmute)
    }
}
