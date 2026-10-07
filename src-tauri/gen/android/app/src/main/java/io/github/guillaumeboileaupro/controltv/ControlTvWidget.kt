package io.github.guillaumeboileaupro.controltv

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.widget.RemoteViews

/**
 * The home-screen widget. It only draws the last known view (no network, no Python) and
 * points its buttons at [WidgetTapReceiver]. This provider is exported, as Android requires
 * for widget updates; taps go to the separate, non-exported receiver, so another app cannot
 * trigger a command by sending a broadcast here.
 */
class ControlTvWidget : AppWidgetProvider() {
    override fun onUpdate(context: Context, manager: AppWidgetManager, appWidgetIds: IntArray) {
        render(context, manager, appWidgetIds)
    }

    companion object {
        /** Draws every control-TV widget from the stored view. */
        fun renderAll(context: Context) {
            val manager = AppWidgetManager.getInstance(context)
            val ids = manager.getAppWidgetIds(ComponentName(context, ControlTvWidget::class.java))
            if (ids.isNotEmpty()) render(context, manager, ids)
        }

        private fun render(context: Context, manager: AppWidgetManager, ids: IntArray) {
            val store = PreferencesWidgetStore(context)
            val selection = store.selection()
            val view = when {
                selection == null -> WidgetPresentation.noSelection()
                else -> store.view() ?: WidgetView(
                    selection.name.ifBlank { WidgetPresentation.UNNAMED_TV },
                    "Tap ↻ to check the TV.",
                )
            }
            val views = RemoteViews(context.packageName, R.layout.widget_control_tv)
            views.setTextViewText(R.id.widget_title, view.title)
            views.setTextViewText(R.id.widget_line, view.line)
            views.setImageViewResource(
                R.id.widget_play_pause,
                if (view.playing == true) R.drawable.ic_widget_pause else R.drawable.ic_widget_play,
            )
            views.setContentDescription(
                R.id.widget_play_pause,
                context.getString(if (view.playing == true) R.string.widget_pause else R.string.widget_play),
            )
            views.setImageViewResource(
                R.id.widget_mute,
                if (view.muted == true) R.drawable.ic_widget_volume_off else R.drawable.ic_widget_volume,
            )
            views.setContentDescription(
                R.id.widget_mute,
                context.getString(if (view.muted == true) R.string.widget_unmute else R.string.widget_mute),
            )
            views.setOnClickPendingIntent(R.id.widget_header, openApp(context))
            for ((id, action) in BUTTONS) views.setOnClickPendingIntent(id, tap(context, action))
            manager.updateAppWidget(ids, views)
        }

        private val BUTTONS = listOf(
            R.id.widget_play_pause to WidgetAction.PLAY_PAUSE,
            R.id.widget_stop to WidgetAction.STOP,
            R.id.widget_mute to WidgetAction.MUTE,
            R.id.widget_volume_down to WidgetAction.VOLUME_DOWN,
            R.id.widget_volume_up to WidgetAction.VOLUME_UP,
            R.id.widget_refresh to WidgetAction.REFRESH,
        )

        private fun tap(context: Context, action: WidgetAction): PendingIntent {
            val intent = Intent(context, WidgetTapReceiver::class.java)
                .setAction(WidgetTapReceiver.ACTION_TAP)
                .putExtra(WidgetTapReceiver.EXTRA_ACTION, action.name)
            return PendingIntent.getBroadcast(
                context,
                action.ordinal,
                intent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
        }

        private fun openApp(context: Context): PendingIntent =
            PendingIntent.getActivity(
                context,
                0,
                Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
    }
}
