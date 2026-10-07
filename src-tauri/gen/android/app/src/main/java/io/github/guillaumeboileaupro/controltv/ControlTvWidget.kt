package io.github.guillaumeboileaupro.controltv

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.util.Log
import android.widget.RemoteViews
import java.util.UUID

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

    override fun onReceive(context: Context, intent: Intent) {
        if (redrawsAfter(intent.action)) {
            Log.i(TAG, "widget redrawn after an app update")
            renderAll(context)
            return
        }
        super.onReceive(context, intent)
    }

    /** What a tap on one of the widget's views does. */
    sealed interface WidgetClick {
        data class Open(val activity: Class<*>, val requestCode: Int, val flags: Int) : WidgetClick
        data class Tap(val action: WidgetAction) : WidgetClick
    }

    companion object {
        private const val TAG = "control-tv"

        /** The one layout the widget is drawn with. */
        val LAYOUT: Int = R.layout.widget_control_tv

        /**
         * An app update renumbers resources whenever one is added: the launcher's copy of the
         * widget, drawn by the previous version, then names a layout and views that are now
         * something else (a blank widget whose taps reach none of ours). The system tells the
         * updated app at once, with this protected broadcast: the widget is redrawn then.
         */
        fun redrawsAfter(action: String?): Boolean = action == Intent.ACTION_MY_PACKAGE_REPLACED

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
            val views = RemoteViews(context.packageName, LAYOUT)
            views.setTextViewText(R.id.widget_title, view.title)
            views.setTextViewText(R.id.widget_line, view.line)
            val playPause = WidgetIcons.playPause(view.playing)
            views.setImageViewResource(R.id.widget_play_pause, playPause.drawable)
            views.setContentDescription(R.id.widget_play_pause, context.getString(playPause.description))
            val mute = WidgetIcons.mute(view.muted)
            views.setImageViewResource(R.id.widget_mute, mute.drawable)
            views.setContentDescription(R.id.widget_mute, context.getString(mute.description))
            // Every drawing arms one token; each button's intent carries it (see WidgetTapHandler).
            val token = store.armedToken { UUID.randomUUID().toString() }
            for ((id, click) in CLICKS) {
                val intent = when (click) {
                    is WidgetClick.Open -> open(context, click)
                    is WidgetClick.Tap -> tap(context, click.action, token)
                }
                views.setOnClickPendingIntent(id, intent)
            }
            manager.updateAppWidget(ids, views)
        }

        /** The activity the widget's TV name opens: its own picker, never the main app. */
        val PICKER: Class<*> = WidgetTvPickerActivity::class.java

        /**
         * Every view of [LAYOUT] that reacts to a tap: the logo opens the app, the TV name and
         * state open the widget's own TV picker (no main app needed), each button is a tap.
         */
        val CLICKS: List<Pair<Int, WidgetClick>> = listOf(
            R.id.widget_logo to WidgetClick.Open(MainActivity::class.java, 0, Intent.FLAG_ACTIVITY_NEW_TASK),
            R.id.widget_header to WidgetClick.Open(
                PICKER,
                1,
                Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK,
            ),
            R.id.widget_play_pause to WidgetClick.Tap(WidgetAction.PLAY_PAUSE),
            R.id.widget_stop to WidgetClick.Tap(WidgetAction.STOP),
            R.id.widget_mute to WidgetClick.Tap(WidgetAction.MUTE),
            R.id.widget_volume_down to WidgetClick.Tap(WidgetAction.VOLUME_DOWN),
            R.id.widget_volume_up to WidgetClick.Tap(WidgetAction.VOLUME_UP),
            R.id.widget_refresh to WidgetClick.Tap(WidgetAction.REFRESH),
        )

        // Explicit and immutable. The token is part of the intent's data, so each token gets
        // its own pending intent: an intent already handed out keeps its token for good (an
        // updated one would let a stale delivery carry a fresh token).
        private fun tap(context: Context, action: WidgetAction, token: String): PendingIntent {
            val intent = Intent(context, WidgetTapReceiver::class.java)
                .setAction(WidgetTapReceiver.ACTION_TAP)
                .setData(Uri.parse("controltv-widget://tap/${action.name}/$token"))
                .putExtra(WidgetTapReceiver.EXTRA_ACTION, action.name)
                .putExtra(WidgetTapReceiver.EXTRA_TOKEN, token)
            return PendingIntent.getBroadcast(context, action.ordinal, intent, PendingIntent.FLAG_IMMUTABLE)
        }

        // Explicit and immutable; the picker is not exported, so only this pending intent opens it.
        private fun open(context: Context, click: WidgetClick.Open): PendingIntent =
            PendingIntent.getActivity(
                context,
                click.requestCode,
                Intent(context, click.activity).addFlags(click.flags),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
    }
}
