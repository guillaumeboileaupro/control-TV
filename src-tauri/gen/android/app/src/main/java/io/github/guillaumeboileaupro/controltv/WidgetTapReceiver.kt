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
 * it). It does no network work itself: with no TV selected it says so and stops; otherwise it
 * queues one background job for the tap. The job is unique and kept: a tap arriving while
 * another one is still being handled is ignored, never queued, so taps cannot pile up into
 * a burst of commands.
 */
class WidgetTapReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != ACTION_TAP) return
        val action = intent.getStringExtra(EXTRA_ACTION)
            ?.let { name -> WidgetAction.entries.firstOrNull { it.name == name } } ?: return
        val store = PreferencesWidgetStore(context)
        val selection = store.selection()
        if (selection == null) {
            store.saveView(WidgetPresentation.noSelection())
            ControlTvWidget.renderAll(context)
            return
        }
        store.saveView(WidgetPresentation.busy(store.view(), selection))
        ControlTvWidget.renderAll(context)
        val work = OneTimeWorkRequestBuilder<WidgetActionWorker>()
            .setInputData(
                workDataOf(
                    WidgetActionWorker.KEY_ACTION to action.name,
                    WidgetActionWorker.KEY_TAP to UUID.randomUUID().toString(),
                    WidgetActionWorker.KEY_TAPPED_AT to System.currentTimeMillis(),
                ),
            )
            .build()
        WorkManager.getInstance(context).enqueueUniqueWork(UNIQUE_WORK, ExistingWorkPolicy.KEEP, work)
    }

    companion object {
        const val ACTION_TAP = "io.github.guillaumeboileaupro.controltv.WIDGET_TAP"
        const val EXTRA_ACTION = "action"
        const val UNIQUE_WORK = "control-tv-widget-tap"
    }
}
