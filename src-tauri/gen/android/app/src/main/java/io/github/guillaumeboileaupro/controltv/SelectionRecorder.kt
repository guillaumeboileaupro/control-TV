package io.github.guillaumeboileaupro.controltv

import org.json.JSONObject

/**
 * Records, from the window's own bridge traffic, which TV the widget controls.
 *
 * The window selects a TV by its stable device id and reads its status right away; every
 * command also names it. The last successful status read or command of the window therefore
 * is the selected TV. Its name comes from the window's last discovery. Failed requests change
 * nothing, and the widget's own requests are never recorded (it never calls this), so the
 * widget can neither pick a TV nor switch to another one by itself.
 */
class SelectionRecorder(private val store: WidgetStore) {
    /** Returns true when the selection changed. Never throws: it must not break the window. */
    fun observe(requestLine: String, responseLine: String): Boolean = runCatching {
        val request = JSONObject(requestLine)
        val response = JSONObject(responseLine)
        if (!response.optBoolean("ok", false)) return false
        val method = request.optString("method")
        val params = request.optJSONObject("params") ?: JSONObject()
        when {
            method == "discover_devices" -> {
                val devices = response.getJSONObject("result").getJSONArray("devices")
                val names = (0 until devices.length()).associate {
                    val device = devices.getJSONObject(it)
                    device.getString("id") to device.optString("friendlyName", "")
                }
                store.saveNames(names)
                false
            }
            method in SELECTING_METHODS && params.optString("deviceId").isNotEmpty() -> {
                val id = params.getString("deviceId")
                val previous = store.selection()
                val name = store.names()[id]
                    ?: previous?.takeIf { it.deviceId == id }?.name
                    ?: ""
                val selection = Selection(id, name)
                if (selection == previous) return false
                store.saveSelection(selection)
                true
            }
            else -> false
        }
    }.getOrDefault(false)

    private companion object {
        val SELECTING_METHODS =
            setOf("get_status", "play", "pause", "stop", "seek", "set_volume", "set_muted")
    }
}
