package io.github.guillaumeboileaupro.controltv

/** One TV row of the widget's picker: what is shown and said, never an id or an address. */
data class PickerRow(
    val tv: PickableTv,
    val name: String,
    val subtitle: String?,
    val current: Boolean,
    val spoken: String,
)

/** What the picker shows, as plain words, for each moment of its life. */
data class PickerScreen(
    val heading: String,
    val message: String?,
    val note: String?,
    val searching: Boolean,
    val rows: List<PickerRow>,
    val canSearchAgain: Boolean,
)

/**
 * The picker's states in the main app's words and hierarchy: a heading, at most one short
 * line, the TVs found (the current one marked by a check and a tint, and said as such), and
 * Search again when it can help. No id, address, discovery detail or implementation term.
 */
object PickerScreens {
    const val TITLE = "Choose a TV"
    const val UNNAMED = "Unnamed TV"

    fun searching(): PickerScreen =
        PickerScreen(TITLE, "Looking for TVs on this network…", null, searching = true, rows = emptyList(), canSearchAgain = false)

    fun of(found: PickerDiscovery): PickerScreen = when (found) {
        PickerDiscovery.Busy -> problem("control-TV is busy", "Try again in a moment.")
        PickerDiscovery.Unavailable -> problem("control-TV couldn't start", "Close this and try again.")
        PickerDiscovery.Failed -> problem("Couldn't look for TVs", "Check the Wi-Fi connection, then search again.")
        is PickerDiscovery.Found -> when {
            found.tvs.isEmpty() -> problem(
                "No TV found",
                "Make sure the TV is on and on the same Wi-Fi as this phone.",
                note = missingNote(found),
            )
            else -> PickerScreen(
                heading = TITLE,
                message = "Tap the TV this widget should control.",
                note = missingNote(found),
                searching = false,
                rows = found.tvs.map { row(it, current = it.deviceId == found.chosenId) },
                canSearchAgain = true,
            )
        }
    }

    private fun problem(heading: String, message: String, note: String? = null) =
        PickerScreen(heading, message, note, searching = false, rows = emptyList(), canSearchAgain = true)

    private fun missingNote(found: PickerDiscovery.Found): String? =
        if (found.chosenMissing) "Your current TV wasn't found. It stays chosen until you pick another one." else null

    private fun row(tv: PickableTv, current: Boolean): PickerRow {
        val name = tv.name.ifBlank { UNNAMED }
        val subtitle = tv.model?.takeIf { it.isNotBlank() }
        val spoken = listOfNotNull(name, subtitle, if (current) "current TV" else null).joinToString(", ")
        return PickerRow(tv, name, subtitle, current, spoken)
    }
}
