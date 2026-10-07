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
    fun theWindowsStatusReadOfADiscoveredTvSelectsItWithItsName() {
        recorder.observe(request("discover_devices"), devicesAnswer(TV, OTHER_TV))

        val changed = recorder.observe(request("get_status", TV), statusAnswer())

        assertTrue(changed)
        assertEquals(Selection(TV, "TV $TV"), store.selected)
    }

    @Test
    fun aSuccessfulCommandAlsoSelectsItsTv() {
        recorder.observe(request("discover_devices"), devicesAnswer(TV, OTHER_TV))

        recorder.observe(request("set_volume", OTHER_TV), commandAnswer("set_volume", "confirmed"))

        assertEquals(OTHER_TV, store.selected?.deviceId)
    }

    @Test
    fun aFailedRequestNeverChangesTheSelection() {
        store.selected = SELECTED

        for (code in listOf("device_unavailable", "device_not_found", "timeout")) {
            assertFalse(recorder.observe(request("get_status", OTHER_TV), errorAnswer(code)))
        }
        assertEquals(SELECTED, store.selected)
    }

    @Test
    fun theSameTvReadAgainIsNotAChangeAndKeepsItsName() {
        store.selected = SELECTED

        assertFalse(recorder.observe(request("get_status", TV), statusAnswer()))
        assertEquals(SELECTED, store.selected)
    }

    @Test
    fun malformedOrUnrelatedTrafficIsIgnoredWithoutFailing() {
        store.selected = SELECTED

        assertFalse(recorder.observe("not json", statusAnswer()))
        assertFalse(recorder.observe(request("ping"), statusAnswer()))
        assertFalse(recorder.observe(request("get_status"), statusAnswer()))
        assertEquals(SELECTED, store.selected)
    }
}
