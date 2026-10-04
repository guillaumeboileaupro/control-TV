package io.github.guillaumeboileaupro.controltv

import android.app.Activity
import android.content.Context
import android.net.wifi.WifiManager
import android.util.Log
import android.webkit.WebView
import app.tauri.annotation.Command
import app.tauri.annotation.InvokeArg
import app.tauri.annotation.TauriPlugin
import app.tauri.plugin.Invoke
import app.tauri.plugin.JSObject
import app.tauri.plugin.Plugin
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform

private const val TAG = "control-tv"

@InvokeArg
class HandleArgs {
    lateinit var line: String
}

/** Thrown when the embedded Python runtime cannot start: no request reached the bridge. */
class PythonUnavailable(cause: Throwable) : Exception("embedded Python failed to start: $cause", cause)

/**
 * The Android side of the control bridge: one request line in, one response line out,
 * answered by the shared Python control layer embedded in the app (`control_tv.embedded`),
 * the same `bridge.handle_line` and `ControlService` the desktop bridge process runs.
 *
 * Python runs inside the app process, so unlike the desktop bridge it cannot be killed and
 * replaced; the Rust shell's request claim and timeouts still apply around every call.
 */
@TauriPlugin
class ControlBridgePlugin(private val activity: Activity) : Plugin(activity) {
    private val multicast = MulticastLockHolder(
        AndroidMulticastLock(
            (activity.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager)
                .createMulticastLock("control-tv-discovery")
                .apply { setReferenceCounted(false) },
        ),
    )

    private val relay = BridgeRelay(handler = { line ->
        val python = try {
            if (!Python.isStarted()) {
                Python.start(AndroidPlatform(activity.applicationContext))
            }
            Python.getInstance()
        } catch (error: Throwable) {
            throw PythonUnavailable(error)
        }
        python.getModule("control_tv.embedded").callAttr("handle", line).toString()
    })

    override fun load(webView: WebView) {
        holdMulticast()
    }

    override fun onResume() {
        holdMulticast()
    }

    override fun onPause() {
        multicast.release()
        Log.i(TAG, "multicast lock released (held=${multicast.isHeld})")
    }

    override fun onDestroy() {
        multicast.release()
        relay.shutdown()
    }

    private fun holdMulticast() {
        multicast.acquire()
        Log.i(TAG, "multicast lock acquired (held=${multicast.isHeld})")
    }

    @Command
    fun handle(invoke: Invoke) {
        val args = invoke.parseArgs(HandleArgs::class.java)
        relay.submit(
            args.line,
            onResponse = { response -> invoke.resolve(JSObject().put("line", response)) },
            onFailure = { error ->
                val code = if (error is PythonUnavailable) "backend_unavailable" else "bridge_transport"
                Log.e(TAG, "bridge request failed ($code)", error)
                invoke.reject(error.message ?: error.toString(), code)
            },
        )
    }
}

private class AndroidMulticastLock(private val lock: WifiManager.MulticastLock) : MulticastLockApi {
    override val isHeld: Boolean
        get() = lock.isHeld

    override fun acquire() = lock.acquire()

    override fun release() = lock.release()
}
