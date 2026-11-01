package io.github.guillaumeboileaupro.controltv

import android.app.Activity
import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.net.wifi.WifiManager
import android.util.Log
import android.webkit.WebView
import androidx.core.content.ContextCompat
import app.tauri.annotation.Command
import app.tauri.annotation.InvokeArg
import app.tauri.annotation.TauriPlugin
import app.tauri.plugin.Invoke
import app.tauri.plugin.JSObject
import app.tauri.plugin.Plugin
import java.util.concurrent.Executor
import java.util.concurrent.atomic.AtomicBoolean

private const val TAG = "control-tv"

@InvokeArg
class HandleArgs {
    lateinit var line: String
}

@InvokeArg
class SelectionArgs {
    lateinit var deviceId: String
    var name: String = ""
}

@InvokeArg
class VoiceArgs {
    lateinit var deviceId: String
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
    private val voiceStarting = AtomicBoolean(false)
    private val voiceStopRequested = AtomicBoolean(false)
    @Volatile private var voiceCapture: VoiceCapture? = null

    override fun load(webView: WebView) {
        holdMulticast()
        // Once, on the bridge worker before the first request: proves in logcat that the embedded
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
        voiceStopRequested.set(true)
        voiceCapture?.stop()
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
                // The window's discoveries refresh the widget's TV names (never its target).
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

    /**
     * Called by the window when the person explicitly chooses a TV: the widget's target
     * becomes that stable device id at once, before and whatever its first status read gives.
     * It only writes the app's private preferences; nothing reaches the bridge or a TV.
     */
    @Command
    fun rememberSelection(invoke: Invoke) {
        val args = invoke.parseArgs(SelectionArgs::class.java)
        val changed = try {
            SelectionRecorder(widgetStore).select(args.deviceId, args.name)
        } catch (error: IllegalArgumentException) {
            invoke.reject("not a device id", "invalid_argument")
            return
        }
        // No device id, name or network value in the log.
        Log.i(TAG, "widget TV chosen in the app (changed=$changed)")
        if (changed) ControlTvWidget.renderAll(activity.applicationContext)
        invoke.resolve()
    }

    /** Start one bounded push-to-talk session after Android grants RECORD_AUDIO explicitly. */
    @Command
    fun startVoice(invoke: Invoke) {
        val args = invoke.parseArgs(VoiceArgs::class.java)
        if (args.deviceId.isBlank()) {
            invoke.reject("not a device id", "invalid_argument")
            return
        }
        if (!voiceStarting.compareAndSet(false, true)) {
            invoke.reject("a voice session is already active", "command_rejected")
            return
        }
        voiceStopRequested.set(false)
        val begin: (Boolean) -> Unit = { granted ->
            if (!granted) {
                voiceStarting.set(false)
                invoke.reject("microphone permission was not granted", "permission_denied")
            } else {
                runVoice(args.deviceId, invoke)
            }
        }
        if (ContextCompat.checkSelfPermission(activity, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED) {
            begin(true)
        } else {
            (activity as MainActivity).requestMicrophonePermission(begin)
        }
    }

    /** Stop capture immediately; ending the TV session stays ordered after its last PCM chunk. */
    @Command
    fun stopVoice(invoke: Invoke) {
        voiceStopRequested.set(true)
        voiceCapture?.stop()
        invoke.resolve()
    }

    private fun runVoice(deviceId: String, invoke: Invoke) {
        try {
            relay.execute(
                task = {
                    val embedded = EmbeddedBridge.embedded(activity)
                    var sessionStarted = false
                    try {
                        embedded.callAttr("start_voice", deviceId)
                        sessionStarted = true
                        var ended = VoiceEnd.STOPPED
                        val capture = VoiceCapture(
                            microphone = ::AudioRecordMicrophone,
                            permitted = {
                                ContextCompat.checkSelfPermission(activity, Manifest.permission.RECORD_AUDIO) ==
                                    PackageManager.PERMISSION_GRANTED
                            },
                            // The bridge worker owns the whole session. Other commands wait, the widget
                            // reports busy, and PCM chunks cannot interleave with another Python call.
                            executor = Executor { it.run() },
                            onChunk = { embedded.callAttr("send_voice", deviceId, it) },
                            onEnd = { end ->
                                ended = end
                                Log.i(TAG, "voice capture ended (${end.name.lowercase()})")
                            },
                        )
                        voiceCapture = capture
                        if (voiceStopRequested.get()) capture.stop()
                        if (!capture.start()) {
                            throw IllegalStateException("microphone capture did not start")
                        }
                        if (ended == VoiceEnd.MICROPHONE_ERROR) {
                            throw IllegalStateException("microphone capture or TV voice transport failed")
                        }
                    } finally {
                        voiceCapture = null
                        if (sessionStarted) embedded.callAttr("stop_voice", deviceId)
                        voiceStarting.set(false)
                    }
                    invoke.resolve()
                },
                onFailure = { error -> rejectVoice(invoke, error) },
            )
        } catch (error: Exception) {
            rejectVoice(invoke, error)
        }
    }

    private fun rejectVoice(invoke: Invoke, error: Throwable) {
        voiceCapture?.stop()
        voiceCapture = null
        voiceStarting.set(false)
        Log.e(TAG, "voice session failed", error)
        invoke.reject(error.message ?: "voice session failed", "voice_failed")
    }
}

private class AndroidMulticastLock(private val lock: WifiManager.MulticastLock) : MulticastLockApi {
    override val isHeld: Boolean
        get() = lock.isHeld

    override fun acquire() = lock.acquire()

    override fun release() = lock.release()
}
