package io.github.guillaumeboileaupro.controltv.receiver

import android.os.Bundle
import android.text.Editable
import android.text.InputType
import android.text.TextWatcher
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import org.webrtc.SurfaceViewRenderer

class MainActivity : AppCompatActivity() {
    private lateinit var renderer: SurfaceViewRenderer
    private lateinit var form: LinearLayout
    private lateinit var status: TextView
    private var session: ReceiverSession? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // ControlTV on Ubuntu types the one-time session code into this field through the TV's
        // remote channel; nobody has to type it. It stays focused so that input reaches it.
        val handoff = field("Code de session (rempli automatiquement)").apply {
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
        }
        val title = TextView(this).apply {
            text = "ControlTV — écran de l'ordinateur"; textSize = 26f; gravity = Gravity.CENTER
        }
        val help = TextView(this).apply {
            text = "Sur l'ordinateur, dans ControlTV, choisissez « Écran vers la TV » puis WebRTC.\n" +
                "Cette TV se connecte toute seule : il n'y a rien à saisir ici."
            textSize = 16f; gravity = Gravity.CENTER; setPadding(0, 16, 0, 24)
        }
        status = TextView(this).apply { text = "En attente de ControlTV"; textSize = 18f; gravity = Gravity.CENTER }
        val refuse = Button(this).apply { text = "Refuser / arrêter"; setOnClickListener { finish() } }
        form = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL; gravity = Gravity.CENTER
            setPadding(64, 32, 64, 32)
            addView(title); addView(help); addView(handoff); addView(refuse); addView(status)
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
                runCatching { PairingProtocol.handoff(encoded) }
                    .onSuccess { target ->
                        session = ReceiverSession(
                            this@MainActivity,
                            renderer,
                            { message -> runOnUiThread { status.text = message } },
                            { runOnUiThread { awaitFreshHandoff() } },
                        )
                        session?.connect(target)
                        handoff.addTextChangedListener(this)
                        handoff.requestFocus()
                    }
                    .onFailure {
                        status.text = "Paramètres de session refusés"
                        handoff.addTextChangedListener(this)
                    }
            }
        }
        handoff.addTextChangedListener(watcher)
        handoff.requestFocus()
    }

    private fun field(hintText: String) = EditText(this).apply { hint = hintText; isSingleLine = true }

    override fun onStop() { super.onStop(); session?.close(); session = null }
    override fun onDestroy() { session?.close(); session = null; super.onDestroy() }
}

