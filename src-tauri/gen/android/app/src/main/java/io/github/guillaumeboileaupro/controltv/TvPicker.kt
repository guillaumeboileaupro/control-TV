package io.github.guillaumeboileaupro.controltv

import org.json.JSONObject

/** A TV the picker can offer: its stable id (the target) and what it is called (display). */
data class PickableTv(val deviceId: String, val name: String, val model: String?)

/** What a discovery from the widget's TV picker found. */
sealed interface PickerDiscovery {
    /**
     * [chosenMissing]: a TV is chosen but was not found this time; it stays chosen (it may be
     * off or away) and is never replaced by another one.
     */
    data class Found(val tvs: List<PickableTv>, val chosenId: String?, val chosenMissing: Boolean) : PickerDiscovery
    object Busy : PickerDiscovery
    object Unavailable : PickerDiscovery
    object Failed : PickerDiscovery
}

/**
 * The widget's own TV picker, free of Android types so it can be tested.
 *
 * Discovery is the shared layer's own `discover_devices` (a read: no Cast command), sent as
 * one non-queuing transaction on the app's single bridge, exactly like a widget tap: if the
 * window is using the bridge it is turned down, never queued. The picker has no discovery,
 * network or Cast logic of its own. Choosing stores the TV's stable id the same way the
 * window does ([SelectionRecorder]); cancelling stores nothing.
 */
class TvPicker(
    private val store: WidgetStore,
    private val bridge: TransactionRunner,
    private val log: (String) -> Unit = {},
) {
    fun discover(): PickerDiscovery {
        val result = try {
            bridge.tryTransaction(DISCOVERY_TIMEOUT_MILLIS) { transaction -> transaction.call(DISCOVER_REQUEST) }
        } catch (error: PythonUnavailable) {
            log("widget picker: control-TV could not start")
            return PickerDiscovery.Unavailable
        } catch (error: Exception) {
            log("widget picker: discovery failed")
            return PickerDiscovery.Failed
        }
        val line = when (result) {
            is TransactionResult.Done -> result.value
            TransactionResult.Busy -> {
                log("widget picker: busy, discovery not started")
                return PickerDiscovery.Busy
            }
            else -> {
                log("widget picker: discovery timed out")
                return PickerDiscovery.Failed
            }
        }
        val tvs = runCatching { parse(line) }.getOrNull()
        if (tvs == null) {
            log("widget picker: discovery failed")
            return PickerDiscovery.Failed
        }
        // The window's discoveries refresh the chosen TV's name the same way.
        SelectionRecorder(store).observe(DISCOVER_REQUEST, line)
        val chosen = store.selection()?.deviceId
        val missing = chosen != null && tvs.none { it.deviceId == chosen }
        log("widget picker: ${tvs.size} TV(s) found, chosen TV ${if (missing) "not found" else "listed"}")
        return PickerDiscovery.Found(tvs, chosen, missing)
    }

    /** The person picked [tv]: it becomes the widget's target. True when it changed. */
    fun choose(tv: PickableTv): Boolean {
        val changed = SelectionRecorder(store).select(tv.deviceId, tv.name)
        log("widget TV chosen in the widget picker (changed=$changed)")
        return changed
    }

    private fun parse(line: String): List<PickableTv>? {
        val response = JSONObject(line)
        if (!response.optBoolean("ok", false)) return null
        val devices = response.getJSONObject("result").getJSONArray("devices")
        return (0 until devices.length()).map {
            val device = devices.getJSONObject(it)
            PickableTv(
                deviceId = device.getString("id"),
                name = device.optString("friendlyName", ""),
                model = if (device.isNull("modelName")) null else device.optString("modelName"),
            )
        }
    }

    companion object {
        // The shared layer's own discovery request, the same one the window sends.
        const val DISCOVER_REQUEST = """{"id":1,"method":"discover_devices","params":{}}"""
        const val DISCOVERY_TIMEOUT_MILLIS = 30_000L
    }
}
