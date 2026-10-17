package io.github.guillaumeboileaupro.controltv

import android.content.Context
import android.util.Log
import com.chaquo.python.PyObject

/**
 * The debug build's acceptance mode switch. Off by default; the owner turns it on in the
 * "control-TV Recette" screen, and it applies from the next start of the app (the embedded
 * service is built once per process). Nothing is collected while it is off.
 */
internal object AcceptanceTrace {
    private const val TAG = "control-tv-acceptance"
    private const val PREFERENCES = "acceptance-trace"
    private const val ENABLED = "enabled"

    @Volatile
    private var configured = false

    fun enabled(context: Context): Boolean =
        context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE).getBoolean(ENABLED, false)

    fun setEnabled(context: Context, enabled: Boolean) {
        context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
            .edit().putBoolean(ENABLED, enabled).apply()
    }

    /** Applies the mode once per process, before the first request reaches Python. */
    fun configure(context: Context, embedded: PyObject) {
        synchronized(this) {
            if (configured) return
            configured = true
            if (!enabled(context)) return
            try {
                val directory = AcceptanceTraceFiles.directory(context.filesDir)
                embedded.callAttr("configure_acceptance_trace", directory.absolutePath)
                Log.i(TAG, "acceptance trace on for this process")
            } catch (error: Exception) {
                // The screen shows the mode as not active: never a silent, partial trace.
                Log.e(TAG, "acceptance trace could not be switched on", error)
            }
        }
    }

    /** The embedded side's own status (`control_tv.embedded.acceptance_trace_status`). */
    fun status(context: Context): String =
        EmbeddedBridge.embedded(context).callAttr("acceptance_trace_status").toString()
}
