package io.github.guillaumeboileaupro.controltv

import org.json.JSONObject

/** Rejection code when the embedded Python could not be reached: the request was not sent. */
const val CODE_BACKEND_UNAVAILABLE = "backend_unavailable"

/** Rejection code when a request may have reached the control layer: delivery is ambiguous. */
const val CODE_BRIDGE_TRANSPORT = "bridge_transport"

/**
 * Thrown when the embedded bridge could not be reached: Python did not start or
 * `control_tv.embedded` could not be imported. No request reached the bridge.
 */
class PythonUnavailable(cause: Throwable) : Exception("embedded Python bridge unavailable: $cause", cause)

/**
 * One request line through the embedded bridge, with the failure split the Rust shell relies on.
 *
 * [load] starts Python and imports `control_tv.embedded` (cached by Python after the first
 * call) and returns its `handle`. A failure there happens before a request reaches the
 * control layer, so it is [PythonUnavailable] (`backend_unavailable`, certainly not sent).
 * A failure while `handle` runs, or after it, is passed on unchanged: the request may have
 * been sent, so it stays the ambiguous `bridge_transport`. `handle` is called at most once.
 */
class EmbeddedHandler(
    private val load: () -> (String) -> String,
    private val log: (String) -> Unit = {},
    private val clock: () -> Long = System::nanoTime,
) : (String) -> String {
    /**
     * [log] gets, for each request, its method, how long the control layer took (Python start
     * included) and how it ended: `ok`, the error code, or `failed`. Never a parameter, device
     * id, name or address.
     */
    override fun invoke(line: String): String {
        val started = clock()
        val method = methodOf(line)
        val handle = try {
            load()
        } catch (error: Throwable) {
            log("bridge $method: ${elapsed(started)} ms, $CODE_BACKEND_UNAVAILABLE")
            throw PythonUnavailable(error)
        }
        val answer = try {
            handle(line)
        } catch (error: Throwable) {
            log("bridge $method: ${elapsed(started)} ms, failed")
            throw error
        }
        log("bridge $method: ${elapsed(started)} ms, ${endOf(answer)}")
        return answer
    }

    private fun elapsed(started: Long): Long = (clock() - started) / 1_000_000

    private fun methodOf(line: String): String =
        runCatching { JSONObject(line).optString("method") }.getOrNull()
            ?.takeIf { METHOD.matches(it) } ?: "?"

    private fun endOf(answer: String): String = runCatching {
        val response = JSONObject(answer)
        if (response.optBoolean("ok", false)) "ok"
        else response.optJSONObject("error")?.optString("code")?.takeIf { METHOD.matches(it) } ?: "error"
    }.getOrDefault("unreadable")

    private companion object {
        /** A method or error code is a short snake_case word; anything else is not logged. */
        val METHOD = Regex("[a-z][a-z0-9_]{0,63}")
    }
}

/** The rejection code reported to the Rust shell for a failed request. */
fun rejectionCode(error: Throwable): String =
    if (error is PythonUnavailable) CODE_BACKEND_UNAVAILABLE else CODE_BRIDGE_TRANSPORT
