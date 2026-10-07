package io.github.guillaumeboileaupro.controltv

import android.content.Context
import android.content.SharedPreferences

/** The TV the window last worked with: what the home-screen widget controls. */
data class Selection(val deviceId: String, val name: String)

/** What the widget shows, kept so it can be drawn again without any network access. */
data class WidgetView(
    val title: String,
    val line: String,
    // The last state the TV reported; null when not known. They pick the icons only.
    val playing: Boolean? = null,
    val muted: Boolean? = null,
    val busy: Boolean = false,
)

/** The widget's small persistent state (app-private preferences). */
interface WidgetStore {
    fun selection(): Selection?
    fun saveSelection(selection: Selection)

    /** Device names from the window's last discovery, by stable device id. */
    fun names(): Map<String, String>
    fun saveNames(names: Map<String, String>)

    fun view(): WidgetView?
    fun saveView(view: WidgetView)

    /**
     * Replaces the record of claimed taps ([ClaimedTaps]) with what [update] returns for the
     * stored one, atomically (no other claim in between) and durably (on disk before it
     * returns, so a process death right after it keeps the claim). [update] returning null
     * leaves the record unchanged and makes this return false.
     */
    fun updateClaimedTaps(update: (String?) -> String?): Boolean

    /** The token the widget's buttons carry now (one per drawing); armed if none yet. */
    fun armedToken(newToken: () -> String): String

    /**
     * Consumes [token] if it is the armed one, arming [next] instead, atomically and durably:
     * the same widget interaction delivered twice carries the same token and is accepted once.
     */
    fun consumeToken(token: String, next: String): Boolean
}

class PreferencesWidgetStore(context: Context) : WidgetStore {
    private val prefs: SharedPreferences =
        context.applicationContext.getSharedPreferences("control_tv_widget", Context.MODE_PRIVATE)

    override fun selection(): Selection? {
        val id = prefs.getString("selected_id", null) ?: return null
        return Selection(id, prefs.getString("selected_name", null) ?: "")
    }

    override fun saveSelection(selection: Selection) {
        prefs.edit().putString("selected_id", selection.deviceId).putString("selected_name", selection.name).apply()
    }

    override fun names(): Map<String, String> {
        val stored = prefs.getString("names", null) ?: return emptyMap()
        return runCatching {
            val json = org.json.JSONObject(stored)
            json.keys().asSequence().associateWith { json.getString(it) }
        }.getOrDefault(emptyMap())
    }

    override fun saveNames(names: Map<String, String>) {
        prefs.edit().putString("names", org.json.JSONObject(names).toString()).apply()
    }

    override fun view(): WidgetView? {
        val title = prefs.getString("view_title", null) ?: return null
        return WidgetView(
            title = title,
            line = prefs.getString("view_line", "") ?: "",
            playing = optionalBoolean("view_playing"),
            muted = optionalBoolean("view_muted"),
            busy = prefs.getBoolean("view_busy", false),
        )
    }

    override fun saveView(view: WidgetView) {
        prefs.edit()
            .putString("view_title", view.title)
            .putString("view_line", view.line)
            .putString("view_playing", view.playing?.toString())
            .putString("view_muted", view.muted?.toString())
            .putBoolean("view_busy", view.busy)
            .apply()
    }

    override fun updateClaimedTaps(update: (String?) -> String?): Boolean = synchronized(LOCK) {
        // An update from a version that kept only the last claimed tap: that tap stays claimed.
        val stored = prefs.getString("claimed_taps", null)
            ?: prefs.getString("claimed_tap", null)?.let { ClaimedTaps.claim(null, it, System.currentTimeMillis()) }
        val next = update(stored) ?: return false
        // commit(), not apply(): written to disk before the command is handed to the bridge.
        check(prefs.edit().putString("claimed_taps", next).remove("claimed_tap").commit()) {
            "could not record the widget tap"
        }
        true
    }

    override fun armedToken(newToken: () -> String): String = synchronized(LOCK) {
        prefs.getString("armed_token", null) ?: newToken().also {
            check(prefs.edit().putString("armed_token", it).commit()) { "could not arm the widget" }
        }
    }

    override fun consumeToken(token: String, next: String): Boolean = synchronized(LOCK) {
        if (prefs.getString("armed_token", null) != token) return false
        check(prefs.edit().putString("armed_token", next).commit()) { "could not record the widget tap" }
        true
    }

    private companion object {
        val LOCK = Any()
    }

    private fun optionalBoolean(key: String): Boolean? = prefs.getString(key, null)?.toBooleanStrictOrNull()
}
