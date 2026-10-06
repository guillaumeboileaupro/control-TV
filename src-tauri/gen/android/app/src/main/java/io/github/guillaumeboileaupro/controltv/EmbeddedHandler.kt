package io.github.guillaumeboileaupro.controltv

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
 * call) and returns its `handle`. A failure there happens before any request reaches the
 * control layer, so it is [PythonUnavailable] (`backend_unavailable`, certainly not sent).
 * A failure while `handle` runs, or after it, is passed on unchanged: the request may have
 * been sent, so it stays the ambiguous `bridge_transport`. `handle` is called at most once.
 */
class EmbeddedHandler(private val load: () -> (String) -> String) : (String) -> String {
    override fun invoke(line: String): String {
        val handle = try {
            load()
        } catch (error: Throwable) {
            throw PythonUnavailable(error)
        }
        return handle(line)
    }
}

/** The rejection code reported to the Rust shell for a failed request. */
fun rejectionCode(error: Throwable): String =
    if (error is PythonUnavailable) CODE_BACKEND_UNAVAILABLE else CODE_BRIDGE_TRANSPORT
