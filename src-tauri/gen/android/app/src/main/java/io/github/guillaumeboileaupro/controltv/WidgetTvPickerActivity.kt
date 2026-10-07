package io.github.guillaumeboileaupro.controltv

import android.app.Activity
import android.content.Context
import android.graphics.Typeface
import android.net.wifi.WifiManager
import android.os.Bundle
import android.util.Log
import android.util.TypedValue
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import java.util.concurrent.Executors

/**
 * The home-screen widget's own TV picker: a compact control-TV panel, opened only by the
 * widget (not exported), that works with the main app closed and never opens it. It lists the
 * TVs the shared layer discovers ([TvPicker]); tapping one makes it the widget's TV and
 * closes; Cancel, Back or a tap outside the panel changes nothing. Nothing here sends a Cast
 * command. What it shows comes from [PickerScreens]; this class only draws it.
 */
class WidgetTvPickerActivity : Activity() {
    private val background = Executors.newSingleThreadExecutor()
    private lateinit var picker: TvPicker
    private lateinit var heading: TextView
    private lateinit var message: TextView
    private lateinit var progress: View
    private lateinit var note: View
    private lateinit var noteText: TextView
    private lateinit var scroll: ScrollView
    private lateinit var list: LinearLayout
    private lateinit var retry: Button

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        setContentView(R.layout.activity_tv_picker)
        heading = findViewById(R.id.picker_heading)
        message = findViewById(R.id.picker_message)
        progress = findViewById(R.id.picker_progress)
        note = findViewById(R.id.picker_note)
        noteText = findViewById(R.id.picker_note_text)
        scroll = findViewById(R.id.picker_scroll)
        list = findViewById(R.id.picker_list)
        list.clipToOutline = true
        retry = findViewById(R.id.picker_retry)
        val root = findViewById<FrameLayout>(R.id.picker_root)
        val panel = findViewById<LinearLayout>(R.id.picker_panel)
        fitPanel(root, panel)
        root.setOnClickListener { cancel() }
        findViewById<Button>(R.id.picker_cancel).setOnClickListener { cancel() }
        retry.setOnClickListener { discover() }
        picker = TvPicker(PreferencesWidgetStore(this), EmbeddedBridge.relay(this)) { Log.i(TAG, it) }
        Log.i(TAG, "widget picker opened")
        discover()
    }

    override fun onDestroy() {
        background.shutdown()
        super.onDestroy()
    }

    private fun cancel() {
        Log.i(TAG, "widget picker cancelled")
        finish()
    }

    /** Clear of the status bar, the navigation bar and a cutout; never wider than 480dp. */
    private fun fitPanel(root: FrameLayout, panel: LinearLayout) {
        val panelBottom = panel.paddingBottom
        ViewCompat.setOnApplyWindowInsetsListener(root) { view, insets ->
            val safe = safeContentPadding(
                insets.getInsets(WindowInsetsCompat.Type.systemBars()),
                insets.getInsets(WindowInsetsCompat.Type.displayCutout()),
            )
            view.setPadding(safe.left, safe.top, safe.right, 0)
            panel.setPadding(panel.paddingLeft, panel.paddingTop, panel.paddingRight, panelBottom + safe.bottom)
            WindowInsetsCompat.CONSUMED
        }
        val maxWidth = dp(480)
        if (resources.displayMetrics.widthPixels > maxWidth) {
            panel.layoutParams = (panel.layoutParams as FrameLayout.LayoutParams).apply { width = maxWidth }
        }
    }

    private fun discover() {
        show(PickerScreens.searching())
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
            runOnUiThread { if (!isFinishing && !isDestroyed) show(PickerScreens.of(found)) }
        }
    }

    private fun show(screen: PickerScreen) {
        heading.text = screen.heading
        message.text = screen.message.orEmpty()
        message.visibility = if (screen.message == null) View.GONE else View.VISIBLE
        progress.visibility = if (screen.searching) View.VISIBLE else View.GONE
        noteText.text = screen.note.orEmpty()
        note.visibility = if (screen.note == null) View.GONE else View.VISIBLE
        retry.visibility = if (screen.canSearchAgain) View.VISIBLE else View.INVISIBLE
        list.removeAllViews()
        screen.rows.forEachIndexed { index, row ->
            if (index > 0) list.addView(separator())
            list.addView(rowView(row))
        }
        scroll.visibility = if (screen.rows.isEmpty()) View.GONE else View.VISIBLE
        // About five rows, then the list scrolls: the panel stays compact.
        scroll.layoutParams = scroll.layoutParams.apply {
            height = if (screen.rows.size > MAX_VISIBLE_ROWS) dp(MAX_VISIBLE_ROWS * 57) else ViewGroup.LayoutParams.WRAP_CONTENT
        }
    }

    private fun rowView(row: PickerRow): View {
        val view = LayoutInflater.from(this).inflate(R.layout.item_tv_picker_row, list, false)
        view.findViewById<TextView>(R.id.picker_row_name).apply {
            text = row.name
            typeface = if (row.current) Typeface.create("sans-serif-medium", Typeface.NORMAL) else Typeface.DEFAULT
        }
        view.findViewById<TextView>(R.id.picker_row_subtitle).apply {
            text = row.subtitle.orEmpty()
            visibility = if (row.subtitle == null) View.GONE else View.VISIBLE
        }
        view.findViewById<ImageView>(R.id.picker_row_check).visibility = if (row.current) View.VISIBLE else View.GONE
        view.isActivated = row.current
        view.contentDescription = row.spoken
        view.setOnClickListener {
            if (picker.choose(row.tv)) ControlTvWidget.renderAll(applicationContext)
            finish()
        }
        return view
    }

    private fun separator(): View = View(this).apply {
        layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(1))
        setBackgroundColor(getColor(R.color.widget_line))
    }

    private fun dp(value: Int): Int =
        TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, value.toFloat(), resources.displayMetrics).toInt()

    private companion object {
        const val TAG = "control-tv"
        const val MAX_VISIBLE_ROWS = 5
    }
}
