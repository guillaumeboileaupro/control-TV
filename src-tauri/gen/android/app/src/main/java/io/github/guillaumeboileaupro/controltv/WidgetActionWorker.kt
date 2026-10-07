package io.github.guillaumeboileaupro.controltv

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.net.wifi.WifiManager
import android.os.Build
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.work.ForegroundInfo
import androidx.work.Worker
import androidx.work.WorkerParameters

/**
 * Handles one widget tap in the background (WorkManager: the app process may not be running,
 * and a discovery plus a confirmed command can outlast a broadcast). The tap runs as one
 * non-queuing transaction on the app's one embedded bridge ([WidgetJob], [EmbeddedBridge]),
 * holding the Wi-Fi multicast lock only while it runs (a discovery needs it).
 *
 * It always ends with success: WorkManager never retries it. If Android runs it again (the
 * process died), the tap keeps its identity and [TapGuard] keeps it from sending twice.
 */
class WidgetActionWorker(context: Context, params: WorkerParameters) : Worker(context, params) {
    override fun doWork(): Result {
        val context = applicationContext
        val store = PreferencesWidgetStore(context)
        val selection = store.selection()
        val action = inputData.getString(KEY_ACTION)
            ?.let { name -> WidgetAction.entries.firstOrNull { it.name == name } }
        val tapId = inputData.getString(KEY_TAP)
        val lock = (context.getSystemService(Context.WIFI_SERVICE) as WifiManager)
            .createMulticastLock("control-tv-widget")
            .apply { setReferenceCounted(false) }
        Log.i(TAG, "widget job started: ${action?.name ?: "unknown action"}")
        try {
            if (action == null || tapId.isNullOrBlank()) {
                store.saveView(WidgetPresentation.present(WidgetOutcome.Expired, selection))
                return Result.success()
            }
            lock.acquire()
            val tap = TapSpec(action, tapId, inputData.getLong(KEY_TAPPED_AT, 0L))
            val outcome = WidgetJob(store, EmbeddedBridge.relay(context), System::currentTimeMillis) {
                Log.i(TAG, it)
            }.run(tap)
            Log.i(TAG, "widget job finished: ${action.name} -> ${outcome::class.simpleName}")
            store.saveView(WidgetPresentation.present(outcome, selection))
        } catch (error: Exception) {
            // WidgetJob reports every bridge outcome itself; this is a local failure (storage).
            Log.e(TAG, "widget tap failed: ${error::class.simpleName}")
            store.saveView(WidgetPresentation.present(WidgetOutcome.MaybeSent("command"), selection))
        } finally {
            if (lock.isHeld) lock.release()
            ControlTvWidget.renderAll(context)
        }
        return Result.success()
    }

    /**
     * Expedited work runs as a short foreground job on Android 7 to 11, which needs this
     * notification; from Android 12 expedited work runs without it.
     */
    override fun getForegroundInfo(): ForegroundInfo {
        val manager = applicationContext.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            manager.createNotificationChannel(
                NotificationChannel(CHANNEL, "Widget", NotificationManager.IMPORTANCE_LOW),
            )
        }
        val notification = NotificationCompat.Builder(applicationContext, CHANNEL)
            .setSmallIcon(R.drawable.ic_widget_refresh)
            .setContentTitle("control-TV")
            .setContentText("Talking to your TV…")
            .setOngoing(true)
            .build()
        return ForegroundInfo(NOTIFICATION_ID, notification)
    }

    companion object {
        private const val CHANNEL = "control-tv-widget"
        private const val NOTIFICATION_ID = 7011
        const val KEY_ACTION = "action"
        const val KEY_TAP = "tap"
        const val KEY_TAPPED_AT = "tapped_at"
        private const val TAG = "control-tv"
    }
}
