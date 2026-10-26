package io.github.guillaumeboileaupro.controltv

import android.content.Context
import android.util.Log
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform

/**
 * The one embedded control bridge of the app process, shared by the window (through the
 * Tauri plugin) and the home-screen widget. The embedded Python keeps one process-wide
 * `ControlService`, which is single-flight; one relay with one worker thread for the whole
 * process keeps every request, from either side, one at a time.
 *
 * It does not need the Tauri activity: a widget tap in a process started without the window
 * starts Python here.
 */
object EmbeddedBridge {
    private const val TAG = "control-tv"

    @Volatile
    private var shared: BridgeRelay? = null

    fun relay(context: Context): BridgeRelay =
        shared ?: synchronized(this) {
            shared ?: BridgeRelay(
                handler = EmbeddedHandler(
                    load = handleOf(context.applicationContext),
                    log = { Log.i(TAG, it) },
                ),
            )
                .also { shared = it }
        }

    /** Starts Python if needed and imports `control_tv.embedded`; nothing is sent here. */
    fun embedded(context: Context): PyObject {
        if (!Python.isStarted()) {
            Python.start(AndroidPlatform(context.applicationContext))
        }
        val module = Python.getInstance().getModule("control_tv.embedded")
        // Debug builds only: the owner's acceptance mode, applied once before the first request.
        // The release build's AcceptanceTrace does nothing (src/release).
        AcceptanceTrace.configure(context.applicationContext, module)
        return module
    }

    private fun handleOf(context: Context): () -> (String) -> String = {
        val embedded = embedded(context)
        val handle: (String) -> String = { line -> embedded.callAttr("handle", line).toString() }
        handle
    }
}
