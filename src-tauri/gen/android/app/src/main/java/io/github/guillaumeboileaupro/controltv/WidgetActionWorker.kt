package io.github.guillaumeboileaupro.controltv

import android.content.Context
import android.net.wifi.WifiManager
import android.util.Log
import androidx.work.Worker
import androidx.work.WorkerParameters

/**
 * Handles one widget tap in the background (WorkManager: the app process may not be running,
 * and a discovery plus a confirmed command can outlast a broadcast). It goes through the
 * app's one embedded bridge ([EmbeddedBridge]), the same shared Python control layer as the
 * window, holding the Wi-Fi multicast lock only while it runs (a discovery needs it).
 *
 * It always ends with success: WorkManager never retries it. If Android runs it again after
 * a process death, [TapGuard] keeps the tap from sending its command a second time.
 */
class WidgetActionWorker(context: Context, params: WorkerParameters) : Worker(context, params) {
    override fun doWork(): Result {
        val context = applicationContext
        val store = PreferencesWidgetStore(context)
        val action = inputData.getString(KEY_ACTION)
            ?.let { name -> WidgetAction.entries.firstOrNull { it.name == name } }
        val tapId = inputData.getString(KEY_TAP)
        val selection = store.selection()
        val lock = (context.getSystemService(Context.WIFI_SERVICE) as WifiManager)
            .createMulticastLock("control-tv-widget")
            .apply { setReferenceCounted(false) }
        try {
            if (action == null || tapId == null) {
                store.saveView(WidgetPresentation.present(WidgetOutcome.StatusUnreadable, selection))
                return Result.success()
            }
            lock.acquire()
            val relay = EmbeddedBridge.relay(context)
            val runner = WidgetActionRunner(
                bridge = { line -> relay.call(line, CALL_TIMEOUT_MILLIS) },
                guard = TapGuard(store),
            )
            val tappedAt = inputData.getLong(KEY_TAPPED_AT, 0L)
            val outcome = runner.run(action, selection, tapId, System.currentTimeMillis() - tappedAt)
            Log.i(TAG, "widget ${action.name}: ${outcome::class.simpleName}")
            store.saveView(WidgetPresentation.present(outcome, selection))
        } catch (error: Exception) {
            // The runner reports every bridge outcome itself; this is a local failure (storage).
            Log.e(TAG, "widget tap failed: ${error::class.simpleName}")
            store.saveView(WidgetPresentation.present(WidgetOutcome.MaybeSent("command"), selection))
        } finally {
            if (lock.isHeld) lock.release()
            ControlTvWidget.renderAll(context)
        }
        return Result.success()
    }

    companion object {
        const val KEY_ACTION = "action"
        const val KEY_TAP = "tap"
        const val KEY_TAPPED_AT = "tapped_at"
        private const val TAG = "control-tv"

        // Longer than any single shared-layer call (discovery 5 s, a command's confirmation
        // window); past it the answer is ambiguous and nothing is resent.
        const val CALL_TIMEOUT_MILLIS = 45_000L
    }
}
