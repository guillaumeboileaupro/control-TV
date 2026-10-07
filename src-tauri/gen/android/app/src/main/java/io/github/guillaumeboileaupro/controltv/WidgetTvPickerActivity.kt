package io.github.guillaumeboileaupro.controltv

import android.app.Activity
import android.content.Context
import android.net.wifi.WifiManager
import android.os.Bundle
import android.util.Log
import android.view.View
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import java.util.concurrent.Executors

/**
 * The home-screen widget's own TV picker: a small native screen, opened only by the widget
 * (not exported), that works with the main app closed and never opens it. It lists the TVs
 * the shared layer discovers ([TvPicker]); tapping one makes it the widget's TV and closes;
 * Cancel (or Back) changes nothing. Nothing here sends a Cast command.
 */
class WidgetTvPickerActivity : Activity() {
    private val background = Executors.newSingleThreadExecutor()
    private lateinit var picker: TvPicker
    private lateinit var status: TextView
    private lateinit var list: LinearLayout

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_tv_picker)
        status = findViewById(R.id.picker_status)
        list = findViewById(R.id.picker_list)
        findViewById<Button>(R.id.picker_cancel).setOnClickListener {
            Log.i(TAG, "widget picker cancelled")
            finish()
        }
        findViewById<Button>(R.id.picker_retry).setOnClickListener { discover() }
        picker = TvPicker(PreferencesWidgetStore(this), EmbeddedBridge.relay(this)) { Log.i(TAG, it) }
        Log.i(TAG, "widget picker opened")
        discover()
    }

    override fun onDestroy() {
        background.shutdown()
        super.onDestroy()
    }

    private fun discover() {
        status.setText(R.string.picker_searching)
        list.removeAllViews()
        findViewById<View>(R.id.picker_retry).visibility = View.GONE
        background.execute {
            val lock = (applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager)
                .createMulticastLock("control-tv-widget-picker")
                .apply { setReferenceCounted(false) }
            val found = try {
                lock.acquire()
                picker.discover()
            } finally {
                if (lock.isHeld) lock.release()
            }
            runOnUiThread { if (!isFinishing && !isDestroyed) show(found) }
        }
    }

    private fun show(found: PickerDiscovery) {
        val retry = findViewById<View>(R.id.picker_retry)
        when (found) {
            PickerDiscovery.Busy -> { status.setText(R.string.picker_busy); retry.visibility = View.VISIBLE }
            PickerDiscovery.Unavailable -> { status.setText(R.string.picker_unavailable); retry.visibility = View.VISIBLE }
            PickerDiscovery.Failed -> { status.setText(R.string.picker_failed); retry.visibility = View.VISIBLE }
            is PickerDiscovery.Found -> {
                status.text = when {
                    found.tvs.isEmpty() -> getString(R.string.picker_none)
                    found.chosenMissing -> getString(R.string.picker_chosen_missing)
                    else -> getString(R.string.picker_choose)
                }
                retry.visibility = View.VISIBLE
                for (tv in found.tvs) list.addView(button(tv, chosen = tv.deviceId == found.chosenId))
            }
        }
    }

    private fun button(tv: PickableTv, chosen: Boolean): Button {
        val label = tv.name.ifBlank { getString(R.string.picker_unnamed) }
        return Button(this).apply {
            text = if (chosen) getString(R.string.picker_current, label) else label
            isAllCaps = false
            setOnClickListener {
                if (picker.choose(tv)) ControlTvWidget.renderAll(applicationContext)
                finish()
            }
        }
    }

    private companion object {
        const val TAG = "control-tv"
    }
}
