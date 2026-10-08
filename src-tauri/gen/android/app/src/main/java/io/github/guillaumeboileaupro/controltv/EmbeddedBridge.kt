package io.github.guillaumeboileaupro.controltv

import android.content.Context
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
    @Volatile
    private var shared: BridgeRelay? = null

    fun relay(context: Context): BridgeRelay =
        shared ?: synchronized(this) {
            shared ?: BridgeRelay(handler = EmbeddedHandler(load = handleOf(context.applicationContext)))
                .also { shared = it }
        }

    /** Starts Python if needed and imports `control_tv.embedded`; nothing is sent here. */
    fun embedded(context: Context): PyObject {
        if (!Python.isStarted()) {
            Python.start(AndroidPlatform(context.applicationContext))
        }
        return Python.getInstance().getModule("control_tv.embedded")
    }

    private fun handleOf(context: Context): () -> (String) -> String = {
        val embedded = embedded(context)
        val handle: (String) -> String = { line -> embedded.callAttr("handle", line).toString() }
        handle
    }
}
