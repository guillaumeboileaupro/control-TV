package io.github.guillaumeboileaupro.controltv

import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.os.Bundle
import android.os.Process
import android.view.ViewGroup.LayoutParams
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Switch
import android.widget.TextView
import androidx.core.content.FileProvider
import org.json.JSONObject

/**
 * Debug builds only (src/debug): the owner's acceptance-mode screen. It switches the trace on
 * or off (from the next start), shows whether this process traces and what was recorded, and
 * exports or deletes the evidence. Nothing is exported or deleted without a tap.
 */
class AcceptanceTraceActivity : Activity() {
    private lateinit var status: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val padding = (16 * resources.displayMetrics.density).toInt()
        val column = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(padding, padding, padding, padding)
        }
        column.addView(Switch(this).apply {
            text = "Mode recette : enregistrer les traces C6/C7 (au prochain démarrage)"
            isChecked = AcceptanceTrace.enabled(this@AcceptanceTraceActivity)
            setOnCheckedChangeListener { _, checked ->
                AcceptanceTrace.setEnabled(this@AcceptanceTraceActivity, checked)
                refresh()
            }
        })
        status = TextView(this)
        column.addView(status)
        column.addView(button("Actualiser") { refresh() })
        column.addView(button("Exporter la trace…") { export() })
        column.addView(button("Supprimer les preuves…") { confirmDelete() })
        column.addView(button("Fermer l'application (pour appliquer)") {
            finishAffinity()
            Process.killProcess(Process.myPid())
        })
        setContentView(ScrollView(this).apply { addView(column) })
        refresh()
    }

    private fun button(label: String, action: () -> Unit) = Button(this).apply {
        text = label
        layoutParams = LinearLayout.LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT)
        setOnClickListener { action() }
    }

    private fun refresh() {
        val context = applicationContext
        Thread {
            val text = describe(context)
            runOnUiThread { status.text = text }
        }.start()
    }

    private fun describe(context: android.content.Context): String {
        val switchedOn = AcceptanceTrace.enabled(context)
        val active = try {
            JSONObject(AcceptanceTrace.status(context)).optBoolean("active", false)
        } catch (error: Exception) {
            false
        }
        val trace = AcceptanceTraceFiles.traceFile(context.filesDir)
        val lines = if (trace.isFile) trace.readLines() else emptyList()
        val requests = AcceptanceTraceFiles.summarize(lines).takeLast(10).joinToString("\n") {
            "${it.requestId} ${it.client} ${it.command} — ${it.outcome} (${it.events})"
        }
        return buildString {
            appendLine("Interrupteur : ${if (switchedOn) "activé" else "désactivé"}")
            appendLine("Trace active dans ce processus : ${if (active) "oui" else "non"}")
            appendLine("Fichier privé : ${if (trace.isFile) "${trace.length()} octets" else "aucun"}")
            if (switchedOn != active) appendLine("Fermez puis rouvrez l'application pour appliquer.")
            appendLine()
            appendLine("Dernières requêtes (identifiant, client, commande, issue) :")
            append(requests.ifEmpty { "aucune" })
        }
    }

    private fun export() {
        val trace = AcceptanceTraceFiles.traceFile(filesDir)
        if (!trace.isFile) {
            status.text = "Aucune trace à exporter."
            return
        }
        val uri = FileProvider.getUriForFile(this, "$packageName.acceptance", trace)
        val share = Intent(Intent.ACTION_SEND).apply {
            type = "application/json"
            putExtra(Intent.EXTRA_STREAM, uri)
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
        startActivity(Intent.createChooser(share, "Exporter la trace de recette"))
    }

    private fun confirmDelete() {
        AlertDialog.Builder(this)
            .setMessage("Supprimer toutes les preuves de recette de cet appareil ?")
            .setPositiveButton("Supprimer") { _, _ ->
                AcceptanceTraceFiles.delete(filesDir)
                refresh()
            }
            .setNegativeButton("Annuler", null)
            .show()
    }
}
