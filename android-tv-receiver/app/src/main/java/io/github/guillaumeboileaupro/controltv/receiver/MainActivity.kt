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
        val handoff = field("Connexion sécurisée en attente").apply {
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
        }
        status = TextView(this).apply { text = "En attente de ControlTV"; textSize = 18f; gravity = Gravity.CENTER }
        val refuse = Button(this).apply { text = "Refuser / arrêter"; setOnClickListener { finish() } }
        form = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL; gravity = Gravity.CENTER
            setPadding(64, 32, 64, 32)
            addView(handoff); addView(refuse); addView(status)
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
                if (!encoded.startsWith("ctv1|") || session != null) return
                handoff.removeTextChangedListener(this)
                handoff.text.clear()
                runCatching { PairingProtocol.handoff(encoded) }
                    .onSuccess { target ->
                        handoff.visibility = View.GONE
                        session = ReceiverSession(
                            this@MainActivity,
                            renderer,
                            { message -> runOnUiThread { status.text = message } },
                            { runOnUiThread { awaitFreshHandoff() } },
                        )
                        session?.connect(target)
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

