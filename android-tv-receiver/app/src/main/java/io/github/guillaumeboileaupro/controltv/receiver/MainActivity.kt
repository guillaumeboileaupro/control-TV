package io.github.guillaumeboileaupro.controltv.receiver

import android.os.Bundle
import android.text.Editable
import android.text.InputType
import android.text.TextWatcher
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import org.webrtc.SurfaceViewRenderer

class MainActivity : AppCompatActivity() {
    private lateinit var renderer: SurfaceViewRenderer
    private lateinit var form: LinearLayout
    private lateinit var headline: TextView
    private lateinit var detail: TextView
    private var session: ReceiverSession? = null
    private val generations = SessionGeneration()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // ControlTV on Ubuntu types the one-time session code into this field through the TV's
        // remote channel; nobody has to type it. It stays focused so that input reaches it.
        val handoff = field("Code de session (rempli automatiquement)").apply {
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
        }
        val title = TextView(this).apply {
            text = "ControlTV — écran de l'ordinateur"; textSize = 22f; gravity = Gravity.CENTER
        }
        headline = TextView(this).apply { textSize = 26f; gravity = Gravity.CENTER; setPadding(0, 24, 0, 8) }
        detail = TextView(this).apply { textSize = 16f; gravity = Gravity.CENTER; setPadding(0, 0, 0, 24) }
        val refuse = Button(this).apply { text = "Refuser / arrêter"; setOnClickListener { finish() } }
        form = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL; gravity = Gravity.CENTER
            setPadding(64, 32, 64, 32)
            addView(title); addView(headline); addView(detail); addView(handoff); addView(refuse)
        }
        renderer = SurfaceViewRenderer(this).apply { visibility = View.VISIBLE }
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            addView(form, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.MATCH_PARENT, 1f))
            addView(renderer, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.MATCH_PARENT, 2f))
        }
        setContentView(root)
        lateinit var watcher: TextWatcher
        fun awaitFreshHandoff() {
            session = null
            show(ReceiverScreen.waiting())
            handoff.removeTextChangedListener(watcher)
            handoff.text.clear()
            handoff.visibility = View.VISIBLE
            handoff.addTextChangedListener(watcher)
            handoff.requestFocus()
        }
        watcher = object : TextWatcher {
            override fun beforeTextChanged(value: CharSequence?, start: Int, count: Int, after: Int) = Unit
            override fun onTextChanged(value: CharSequence?, start: Int, before: Int, count: Int) = Unit
            override fun afterTextChanged(value: Editable?) {
                val encoded = value?.toString() ?: return
                if (!encoded.startsWith("ctv1|")) return
                handoff.removeTextChangedListener(this)
                handoff.text.clear()
                session?.close()
                session = null
                generations.retire()
                runCatching { PairingProtocol.handoff(encoded) }
                    .onSuccess { target ->
                        // Callbacks of this session apply only while it is the current one: a late
                        // callback of a replaced or closed session never touches the screen or
                        // forgets the session that replaced it.
                        val generation = generations.next()
                        show(ReceiverScreen.fromSession("Connexion…"))
                        session = ReceiverSession(
                            this@MainActivity,
                            renderer,
                            { message ->
                                runOnUiThread {
                                    if (generations.isCurrent(generation)) show(ReceiverScreen.fromSession(message))
                                }
                            },
                            { runOnUiThread { if (generations.isCurrent(generation)) awaitFreshHandoff() } },
                        )
                        session?.connect(target)
                        handoff.addTextChangedListener(this)
                        handoff.requestFocus()
                    }
                    .onFailure {
                        show(ReceiverScreen.fromSession("Erreur : paramètres de session refusés"))
                        handoff.addTextChangedListener(this)
                    }
            }
        }
        handoff.addTextChangedListener(watcher)
        handoff.requestFocus()
        show(ReceiverScreen.waiting())
    }

    private fun field(hintText: String) = EditText(this).apply { hint = hintText; isSingleLine = true }

    private fun show(view: ReceiverView) {
        headline.text = view.title
        detail.text = view.detail
        if (view.keepScreenOn) {
            window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        } else {
            window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        }
    }

    /** Back on screen after a stop: the stream was closed then, so wait for a new one. */
    override fun onStart() {
        super.onStart()
        if (session == null) show(ReceiverScreen.waiting())
    }

    override fun onStop() { endSession(); super.onStop() }
    override fun onDestroy() { endSession(); super.onDestroy() }

    private fun endSession() {
        generations.retire()
        session?.close()
        session = null
        window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
    }
}

