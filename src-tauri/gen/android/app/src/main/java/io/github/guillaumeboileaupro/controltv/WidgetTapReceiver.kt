package io.github.guillaumeboileaupro.controltv

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import androidx.work.WorkManager
import java.util.UUID
import java.util.concurrent.TimeUnit

/**
 * Receives the widget's button taps (not exported: only this app's own pending intents reach
 * it) and hands them to [WidgetTapHandler], which accepts each widget interaction once. It
 * does no network work itself: an accepted tap with a TV chosen becomes one expedited
 * background job ([WidgetWork]). Logged under the tag "control-tv", without tokens, devices
 * or network values.
 */
class WidgetTapReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != ACTION_TAP) return
        val appContext = context.applicationContext
        val handler = WidgetTapHandler(
            store = PreferencesWidgetStore(appContext),
            now = System::currentTimeMillis,
            newToken = { UUID.randomUUID().toString() },
            enqueue = { tap ->
                // Wait until WorkManager has recorded the job: a process started only for this
                // tap may be reclaimed as soon as the broadcast ends, and the token is spent.
                WorkManager.getInstance(appContext)
                    .enqueueUniqueWork(WidgetWork.uniqueName(tap.tapId), WidgetWork.POLICY, WidgetWork.request(tap))
                    .result
                    .get(ENQUEUE_TIMEOUT_SECONDS, TimeUnit.SECONDS)
            },
            render = { ControlTvWidget.renderAll(appContext) },
            log = { Log.i(TAG, it) },
        )
        val action = intent.getStringExtra(EXTRA_ACTION)
        val token = intent.getStringExtra(EXTRA_TOKEN)
        // The broadcast stays alive until the tap is handled, off the main thread (waiting for
        // the enqueue must not block it), within the receiver's time limit.
        val pending = goAsync()
        Thread({
            try {
                handler.handle(action, token)
            } catch (error: Exception) {
                Log.e(TAG, "widget tap failed: ${error::class.simpleName}")
            } finally {
                pending.finish()
            }
        }, "control-tv-widget-tap").start()
    }

    companion object {
        const val ACTION_TAP = "io.github.guillaumeboileaupro.controltv.WIDGET_TAP"
        const val EXTRA_ACTION = "action"
        const val EXTRA_TOKEN = "token"
        // Below the ~10 s a broadcast may keep running after goAsync().
        const val ENQUEUE_TIMEOUT_SECONDS = 8L
        private const val TAG = "control-tv"
    }
}
