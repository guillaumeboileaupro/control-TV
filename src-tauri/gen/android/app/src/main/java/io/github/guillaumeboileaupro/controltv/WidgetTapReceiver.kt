package io.github.guillaumeboileaupro.controltv

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.workDataOf
import java.util.UUID

/**
 * Receives the widget's button taps (not exported: only this app's own pending intents reach
 * it) and hands them to [WidgetTapHandler], which accepts each widget interaction once. It
 * does no network work itself. An accepted tap with a TV selected becomes one background job,
 * as unique work kept while another tap's job is pending or running (WorkManager drops the
 * newer one), so taps cannot pile up into a burst of commands.
 */
class WidgetTapReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != ACTION_TAP) return
        val handler = WidgetTapHandler(
            store = PreferencesWidgetStore(context),
            now = System::currentTimeMillis,
            newToken = { UUID.randomUUID().toString() },
            enqueue = { tap -> enqueue(context, tap) },
            render = { ControlTvWidget.renderAll(context) },
        )
        handler.handle(intent.getStringExtra(EXTRA_ACTION), intent.getStringExtra(EXTRA_TOKEN))
    }

    private fun enqueue(context: Context, tap: TapSpec) {
        val work = OneTimeWorkRequestBuilder<WidgetActionWorker>()
            .setInputData(
                workDataOf(
                    WidgetActionWorker.KEY_ACTION to tap.action.name,
                    WidgetActionWorker.KEY_TAP to tap.tapId,
                    WidgetActionWorker.KEY_TAPPED_AT to tap.tappedAtMillis,
                ),
            )
            .build()
        WorkManager.getInstance(context).enqueueUniqueWork(UNIQUE_WORK, ExistingWorkPolicy.KEEP, work)
    }

    companion object {
        const val ACTION_TAP = "io.github.guillaumeboileaupro.controltv.WIDGET_TAP"
        const val EXTRA_ACTION = "action"
        const val EXTRA_TOKEN = "token"
        const val UNIQUE_WORK = "control-tv-widget-tap"
    }
}
