package io.github.guillaumeboileaupro.controltv

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** The widget's own TV picker, over the real shared [BridgeRelay] and a scripted shared layer. */
class TvPickerTest {
    private val store = MemoryWidgetStore()
    private val logs = mutableListOf<String>()

    private fun discovering(vararg ids: String) = FakeBridge { method, _ ->
        if (method == "discover_devices") devicesAnswer(*ids) else error("the picker only discovers")
    }

    private fun picker(bridge: FakeBridge) = TvPicker(store, BridgeRelay(bridge)) { logs += it }

    @Test
    fun withNoTvChosenTheWidgetOffersToChooseOne() {
        val view = WidgetPresentation.present(WidgetOutcome.NoSelection, null)

        assertEquals("Tap here to choose a TV.", view.line)
        assertEquals("No TV chosen yet. Tap here to choose one.", WidgetPresentation.noSelectionAfterTap().line)
    }

    @Test
    fun theWidgetsTvNameOpensItsOwnPickerNotTheMainApp() {
        assertEquals(WidgetTvPickerActivity::class.java, ControlTvWidget.PICKER)
    }

    @Test
    fun discoveryIsTheSharedLayersReadOnlyDiscoveryAndNothingElse() {
        val bridge = discovering(TV, OTHER_TV)

        val found = picker(bridge).discover() as PickerDiscovery.Found

        assertEquals(listOf("discover_devices"), bridge.methods)
        assertEquals(listOf(TV, OTHER_TV), found.tvs.map { it.deviceId })
        assertEquals(null, store.selected)
        assertEquals(null, store.claimed)
    }

    @Test
    fun anExplicitChoiceIsStoredByStableIdAndAChangeIsStoredToo() {
        val picker = picker(discovering(TV, OTHER_TV))
        val found = picker.discover() as PickerDiscovery.Found

        assertTrue(picker.choose(found.tvs[0]))
        assertEquals(Selection(TV, "TV $TV"), store.selected)
        assertTrue(picker.choose(found.tvs[1]))
        assertEquals(Selection(OTHER_TV, "TV $OTHER_TV"), store.selected)
        assertFalse(picker.choose(found.tvs[1]))
    }

    @Test
    fun cancellingAfterADiscoverySendsNothingMoreAndKeepsTheChosenTv() {
        store.selected = SELECTED
        val bridge = discovering(TV, OTHER_TV)

        picker(bridge).discover()
        // Cancel: the activity just closes, nothing is chosen.

        // Same target (the discovery only refreshed its display name).
        assertEquals(TV, store.selected?.deviceId)
        assertEquals(listOf("discover_devices"), bridge.methods)
    }

    @Test
    fun aChosenTvMissingFromDiscoveryStaysChosenAndIsNeverReplaced() {
        store.selected = SELECTED

        val found = picker(discovering(OTHER_TV)).discover() as PickerDiscovery.Found

        assertTrue(found.chosenMissing)
        assertEquals(TV, found.chosenId)
        assertEquals(SELECTED, store.selected)
    }

    @Test
    fun theChosenTvIsMarkedWhenFound() {
        store.selected = SELECTED

        val found = picker(discovering(OTHER_TV, TV)).discover() as PickerDiscovery.Found

        assertFalse(found.chosenMissing)
        assertEquals(TV, found.chosenId)
    }

    @Test
    fun whileTheAppUsesTheBridgeThePickerIsTurnedDownAndNothingEntersPython() {
        val release = CountDownLatch(1)
        val started = CountDownLatch(1)
        val bridge = FakeBridge { _, _ -> started.countDown(); release.await(5, TimeUnit.SECONDS); statusAnswer() }
        val relay = BridgeRelay(bridge)
        relay.submit(
            JSONObject().put("id", 1).put("method", "get_status").put("params", JSONObject()).toString(),
            onResponse = {},
            onFailure = {},
        )
        assertTrue(started.await(5, TimeUnit.SECONDS))

        val found = TvPicker(store, relay).discover()
        release.countDown()
        Thread.sleep(200)

        assertEquals(PickerDiscovery.Busy, found)
        assertEquals(listOf("get_status"), bridge.methods)
        relay.shutdown()
    }

    @Test
    fun aFailedDiscoveryOrAPythonThatCannotStartChoosesNothing() {
        store.selected = SELECTED
        val failing = FakeBridge { _, _ -> errorAnswer("discovery_failed") }
        val noPython = FakeBridge { _, _ -> throw PythonUnavailable(IllegalStateException("no Python")) }

        assertEquals(PickerDiscovery.Failed, picker(failing).discover())
        assertEquals(PickerDiscovery.Unavailable, picker(noPython).discover())
        assertEquals(SELECTED, store.selected)
    }

    @Test
    fun theLogsNeverCarryADeviceIdANameOrAnAddress() {
        val picker = picker(discovering(TV, OTHER_TV))
        val found = picker.discover() as PickerDiscovery.Found
        picker.choose(found.tvs[0])

        for (line in logs) {
            for (secret in listOf(TV, OTHER_TV, "TV $TV", "192.168")) {
                assertFalse("\"$line\" contains a private value", line.contains(secret))
            }
        }
        assertTrue(logs.any { it.startsWith("widget TV chosen in the widget picker") })
    }
}
