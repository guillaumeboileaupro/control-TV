package io.github.guillaumeboileaupro.controltv

import org.json.JSONObject

/**
 * Keeps the TV the home-screen widget controls.
 *
 * The target is the TV the person explicitly chose in the app's window: [select] stores its
 * stable device id the moment it is chosen, whether or not its first status read succeeds.
 * Nothing else changes the target: the window's bridge traffic ([observe]) only refreshes
 * display names (from its discoveries), and the widget's own requests never reach this class.
 * Selecting sends nothing to any TV.
 */
class SelectionRecorder(private val store: WidgetStore) {
    /** Returns true when the stored target changed. */
    fun select(deviceId: String, name: String): Boolean {
        require(deviceId.isNotBlank() && deviceId.length <= MAX_LENGTH) { "not a device id" }
        val selection = Selection(deviceId, name.take(MAX_LENGTH))
        if (selection == store.selection()) return false
        store.saveSelection(selection)
        return true
    }

    /**
     * Learns display names from the window's discoveries; never changes which TV is the
     * target. Returns true when the target's displayed name changed. Never throws.
     */
    fun observe(requestLine: String, responseLine: String): Boolean = runCatching {
        val request = JSONObject(requestLine)
        val response = JSONObject(responseLine)
        if (request.optString("method") != "discover_devices" || !response.optBoolean("ok", false)) {
            return false
        }
        val devices = response.getJSONObject("result").getJSONArray("devices")
        val names = (0 until devices.length()).associate {
            val device = devices.getJSONObject(it)
            device.getString("id") to device.optString("friendlyName", "").take(MAX_LENGTH)
        }
        store.saveNames(names)
        val selected = store.selection() ?: return false
        val name = names[selected.deviceId]?.takeIf { it.isNotBlank() && it != selected.name } ?: return false
        store.saveSelection(selected.copy(name = name))
        true
    }.getOrDefault(false)

    private companion object {
        const val MAX_LENGTH = 200
    }
}
