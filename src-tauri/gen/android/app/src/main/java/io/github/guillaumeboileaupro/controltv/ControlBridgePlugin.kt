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

private const val TAG = "control-tv"

@InvokeArg
class HandleArgs {
    lateinit var line: String
}

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

    // The process-wide bridge, shared with the home-screen widget (one request at a time).
    private val relay = EmbeddedBridge.relay(activity)
    private val widgetStore = PreferencesWidgetStore(activity.applicationContext)

    override fun load(webView: WebView) {
        holdMulticast()
        // Once, on the bridge worker before any request: proves in logcat that the embedded
        // Python imports control_tv and answers a ping. Versions only, no device data, no
        // Cast message; never retried.
        relay.execute(
            task = { Log.i(TAG, EmbeddedBridge.embedded(activity).callAttr("startup_diagnostic").toString()) },
            onFailure = { error -> Log.e(TAG, "embedded Python startup diagnostic failed", error) },
        )
    }

    override fun onResume() {
        holdMulticast()
    }

    override fun onPause() {
        multicast.release()
        Log.i(TAG, "multicast lock released (held=${multicast.isHeld})")
    }

    override fun onDestroy() {
        // The relay is process-wide (the widget uses it too): it is not shut down here.
        multicast.release()
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
            onResponse = { response ->
                // The window's own successful requests define the TV the widget controls.
                if (SelectionRecorder(widgetStore).observe(args.line, response)) {
                    ControlTvWidget.renderAll(activity.applicationContext)
                }
                invoke.resolve(JSObject().put("line", response))
            },
            onFailure = { error ->
                val code = rejectionCode(error)
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
