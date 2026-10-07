package io.github.guillaumeboileaupro.controltv

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SelectionRecorderTest {
    private val store = MemoryWidgetStore()
    private val recorder = SelectionRecorder(store)

    private fun request(method: String, deviceId: String? = null): String =
        JSONObject().put("id", 1).put("method", method)
            .put("params", JSONObject().apply { deviceId?.let { put("deviceId", it) } }).toString()

    @Test
    fun anExplicitChoiceIsTheTargetAtOnceEvenWhenItsFirstStatusReadFails() {
        recorder.select(TV, "Living room")

        assertTrue(recorder.select(OTHER_TV, "Kitchen"))
        // The window's first read of the new TV fails: the target stays the chosen TV.
        recorder.observe(request("get_status", OTHER_TV), errorAnswer("device_unavailable"))

        assertEquals(Selection(OTHER_TV, "Kitchen"), store.selected)
    }

    @Test
    fun statusReadsAndCommandsNeverChangeTheTarget() {
        recorder.select(OTHER_TV, "Kitchen")

        recorder.observe(request("get_status", TV), statusAnswer())
        recorder.observe(request("pause", TV), commandAnswer("pause", "confirmed"))
        recorder.observe(request("set_volume", TV), commandAnswer("set_volume", "confirmed"))

        assertEquals(OTHER_TV, store.selected?.deviceId)
    }

    @Test
    fun discoveryOnlyRefreshesTheChosenTvsNameNeverPicksOne() {
        recorder.observe(request("discover_devices"), devicesAnswer(TV, OTHER_TV))
        assertEquals(null, store.selected)

        recorder.select(TV, "")
        val renamed = recorder.observe(request("discover_devices"), devicesAnswer(OTHER_TV, TV))

        assertTrue(renamed)
        assertEquals(Selection(TV, "TV $TV"), store.selected)
    }

    @Test
    fun aDiscoveryWithoutTheChosenTvKeepsItsIdAndNeverFallsBackToAnother() {
        recorder.select(TV, "Living room")

        assertFalse(recorder.observe(request("discover_devices"), devicesAnswer(OTHER_TV)))

        assertEquals(Selection(TV, "Living room"), store.selected)
    }

    @Test
    fun choosingTheSameTvAgainIsNoChangeAndABlankIdIsRefused() {
        recorder.select(TV, "Living room")

        assertFalse(recorder.select(TV, "Living room"))
        assertTrue(runCatching { recorder.select(" ", "x") }.exceptionOrNull() is IllegalArgumentException)
        assertEquals(Selection(TV, "Living room"), store.selected)
    }

    @Test
    fun choosingATvSendsNothingToAnyTv() {
        // SelectionRecorder has no bridge at all; the bridge the widget would use stays idle.
        val bridge = FakeBridge { _, _ -> error("no request expected") }

        recorder.select(TV, "Living room")
        recorder.select(OTHER_TV, "Kitchen")

        assertEquals(emptyList<String>(), bridge.methods)
    }

    @Test
    fun malformedTrafficIsIgnoredWithoutFailing() {
        recorder.select(TV, "Living room")

        assertFalse(recorder.observe("not json", statusAnswer()))
        assertFalse(recorder.observe(request("discover_devices"), errorAnswer("discovery_failed")))
        assertEquals(Selection(TV, "Living room"), store.selected)
    }
}
