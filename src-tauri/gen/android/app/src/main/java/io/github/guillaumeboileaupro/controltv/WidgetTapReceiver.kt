package io.github.guillaumeboileaupro.controltv

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import androidx.work.WorkManager
import java.util.UUID

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
        val handler = WidgetTapHandler(
            store = PreferencesWidgetStore(context),
            now = System::currentTimeMillis,
            newToken = { UUID.randomUUID().toString() },
            enqueue = { tap ->
                WorkManager.getInstance(context)
                    .enqueueUniqueWork(WidgetWork.uniqueName(tap.tapId), WidgetWork.POLICY, WidgetWork.request(tap))
            },
            render = { ControlTvWidget.renderAll(context) },
            log = { Log.i(TAG, it) },
        )
        handler.handle(intent.getStringExtra(EXTRA_ACTION), intent.getStringExtra(EXTRA_TOKEN))
    }

    companion object {
        const val ACTION_TAP = "io.github.guillaumeboileaupro.controltv.WIDGET_TAP"
        const val EXTRA_ACTION = "action"
        const val EXTRA_TOKEN = "token"
        private const val TAG = "control-tv"
    }
}
