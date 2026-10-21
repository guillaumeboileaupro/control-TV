package io.github.guillaumeboileaupro.controltv.receiver

import android.os.Bundle
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
        val host = field("Adresse Ubuntu")
        val port = field("Port")
        val token = field("Code de session")
        status = TextView(this).apply { text = "Prêt"; textSize = 18f; gravity = Gravity.CENTER }
        val accept = Button(this).apply {
            text = "Accepter cette session"
            setOnClickListener {
                runCatching { PairingTarget(host.text.toString().trim(), port.text.toString().toInt(), token.text.toString().trim()) }
                    .onSuccess { target ->
                        isEnabled = false
                        session = ReceiverSession(this@MainActivity, renderer) { message -> runOnUiThread { status.text = message } }
                        session?.connect(target)
                    }
                    .onFailure { status.text = "Paramètres invalides" }
            }
        }
        val refuse = Button(this).apply { text = "Refuser / arrêter"; setOnClickListener { finish() } }
        form = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL; gravity = Gravity.CENTER
            setPadding(64, 32, 64, 32)
            addView(host); addView(port); addView(token); addView(accept); addView(refuse); addView(status)
        }
        renderer = SurfaceViewRenderer(this).apply { visibility = View.VISIBLE }
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            addView(form, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.MATCH_PARENT, 1f))
            addView(renderer, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.MATCH_PARENT, 2f))
        }
        setContentView(root)
        accept.requestFocus()
    }

    private fun field(hintText: String) = EditText(this).apply { hint = hintText; isSingleLine = true }

    override fun onStop() { super.onStop(); session?.close(); session = null }
    override fun onDestroy() { session?.close(); session = null; super.onDestroy() }
}

