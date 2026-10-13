package io.github.guillaumeboileaupro.controltv

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** What the redesigned widget TV picker shows and says, in each of its states. */
class PickerScreenTest {
    private val kitchen = PickableTv("tv-kitchen-uuid", "Kitchen", "Chromecast")
    private val living = PickableTv("tv-living-uuid", "Living room", "Google TV Streamer")
    private val unnamed = PickableTv("tv-unnamed-uuid", " ", null)

    private fun found(vararg tvs: PickableTv, chosen: String? = null, missing: Boolean = false) =
        PickerScreens.of(PickerDiscovery.Found(tvs.toList(), chosen, missing))

    private fun allText(screen: PickerScreen): String =
        listOfNotNull(screen.heading, screen.message, screen.note).plus(
            screen.rows.flatMap { listOfNotNull(it.name, it.subtitle, it.spoken) },
        ).joinToString("\n")

    @Test
    fun whileSearchingItShowsAProgressLineAndNothingToChooseOrRepeat() {
        val screen = PickerScreens.searching()

        assertEquals("Choose a TV", screen.heading)
        assertEquals("Looking for TVs on this network…", screen.message)
        assertTrue(screen.searching)
        assertEquals(emptyList<PickerRow>(), screen.rows)
        assertFalse(screen.canSearchAgain)
    }

    @Test
    fun theTvsFoundAreListedWithTheirNameAndModelAndTheCurrentOneMarked() {
        val screen = found(kitchen, living, chosen = living.deviceId)

        assertEquals("Choose a TV", screen.heading)
        assertEquals("Tap the TV this widget should control.", screen.message)
        assertEquals(listOf("Kitchen", "Living room"), screen.rows.map { it.name })
        assertEquals(listOf("Chromecast", "Google TV Streamer"), screen.rows.map { it.subtitle })
        assertEquals(listOf(false, true), screen.rows.map { it.current })
        assertEquals(null, screen.note)
        assertTrue(screen.canSearchAgain)
        assertFalse(screen.searching)
    }

    @Test
    fun eachRowIsSpokenAsOneLabelAndTheCurrentTvIsSaidAsSuch() {
        val screen = found(kitchen, living, chosen = living.deviceId)

        assertEquals("Kitchen, Chromecast", screen.rows[0].spoken)
        assertEquals("Living room, Google TV Streamer, current TV", screen.rows[1].spoken)
    }

    @Test
    fun aTvWithoutANameOrModelIsStillListedPlainly() {
        val row = found(unnamed).rows.single()

        assertEquals("Unnamed TV", row.name)
        assertEquals(null, row.subtitle)
        assertEquals("Unnamed TV", row.spoken)
    }

    @Test
    fun aCurrentTvThatWasNotFoundStaysChosenAndSaysSo() {
        val screen = found(kitchen, chosen = "tv-away-uuid", missing = true)

        assertEquals("Your current TV wasn't found. It stays chosen until you pick another one.", screen.note)
        assertEquals(listOf(false), screen.rows.map { it.current })
    }

    @Test
    fun noTvBusyAndFailureStatesHaveAHeadingAHintAndSearchAgain() {
        val cases = mapOf(
            found() to ("No TV found" to "Make sure the TV is on and on the same Wi-Fi as this phone."),
            PickerScreens.of(PickerDiscovery.Busy) to ("control-TV is busy" to "Try again in a moment."),
            PickerScreens.of(PickerDiscovery.Failed) to
                ("Couldn't look for TVs" to "Check the Wi-Fi connection, then search again."),
            PickerScreens.of(PickerDiscovery.Unavailable) to ("control-TV couldn't start" to "Close this and try again."),
        )
        for ((screen, words) in cases) {
            assertEquals(words.first, screen.heading)
            assertEquals(words.second, screen.message)
            assertEquals(emptyList<PickerRow>(), screen.rows)
            assertTrue(screen.canSearchAgain)
            assertFalse(screen.searching)
        }
    }

    @Test
    fun noTvFoundStillSaysTheCurrentTvStaysChosen() {
        val screen = found(chosen = "tv-away-uuid", missing = true)

        assertEquals("No TV found", screen.heading)
        assertTrue(screen.note!!.contains("stays chosen"))
    }

    @Test
    fun nothingShownOrSpokenCarriesAnIdAnAddressOrAnImplementationTerm() {
        val screens = listOf(
            PickerScreens.searching(),
            found(kitchen, living, unnamed, chosen = living.deviceId),
            found(kitchen, chosen = "tv-away-uuid", missing = true),
            PickerScreens.of(PickerDiscovery.Busy),
            PickerScreens.of(PickerDiscovery.Failed),
            PickerScreens.of(PickerDiscovery.Unavailable),
        )
        val forbidden = Regex(
            "uuid|\\d+\\.\\d+\\.\\d+\\.\\d+|8009|python|bridge|\\bcast\\b|zeroconf|mdns|discover_devices|[a-z]+_[a-z]+",
            RegexOption.IGNORE_CASE,
        )
        for (screen in screens) {
            val text = allText(screen)
            assertFalse(text, forbidden.containsMatchIn(text))
        }
    }

    @Test
    fun theRowStillCarriesTheTvToStoreWhenChosen() {
        val row = found(kitchen, living).rows[1]

        assertEquals(living, row.tv)
    }
}
