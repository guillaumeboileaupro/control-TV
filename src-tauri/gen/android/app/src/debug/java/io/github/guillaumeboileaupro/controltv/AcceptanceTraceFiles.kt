package io.github.guillaumeboileaupro.controltv

import org.json.JSONException
import org.json.JSONObject
import java.io.File

/**
 * Where the debug acceptance mode keeps its evidence (the app's private files, never shared
 * storage) and how the acceptance screen summarizes it. No Android dependency: unit-tested.
 */
internal object AcceptanceTraceFiles {
    const val DIRECTORY = "acceptance"
    const val TRACE = "command-trace.jsonl"

    /** The events that end a command's trace (same set as `control_tv.command_trace`). */
    val TERMINAL_EVENTS = setOf(
        "command_confirmed",
        "command_unconfirmed",
        "confirmation_skipped",
        "ambiguous_result",
        "not_sent",
        "command_rejected",
    )

    data class RequestSummary(
        val requestId: String,
        val client: String,
        val command: String,
        val events: Int,
        val outcome: String,
    )

    fun directory(filesDir: File): File = File(filesDir, DIRECTORY)

    fun traceFile(filesDir: File): File = File(directory(filesDir), TRACE)

    /** One summary per request, in first-seen order; malformed lines are skipped. */
    fun summarize(lines: List<String>): List<RequestSummary> {
        val order = mutableListOf<String>()
        val events = mutableMapOf<String, MutableList<JSONObject>>()
        for (line in lines) {
            val event = try {
                JSONObject(line)
            } catch (error: JSONException) {
                continue
            }
            val requestId = event.optString("request_id", "")
            if (requestId.isEmpty()) continue
            if (requestId !in events) order += requestId
            events.getOrPut(requestId) { mutableListOf() } += event
        }
        return order.map { requestId ->
            val stream = events.getValue(requestId)
            RequestSummary(
                requestId = requestId,
                client = stream.first().optString("client", "-"),
                command = stream.firstNotNullOfOrNull { event ->
                    event.optString("command", "").ifEmpty { null }
                } ?: "-",
                events = stream.size,
                outcome = stream.lastOrNull { it.optString("event") in TERMINAL_EVENTS }
                    ?.optString("event") ?: "-",
            )
        }
    }

    /** Removes every acceptance file (trace, markers, holds); true when nothing is left. */
    fun delete(filesDir: File): Boolean {
        val directory = directory(filesDir)
        return !directory.exists() || directory.deleteRecursively()
    }
}
