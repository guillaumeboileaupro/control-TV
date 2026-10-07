package io.github.guillaumeboileaupro.controltv

import java.util.concurrent.CompletableFuture
import java.util.concurrent.ExecutionException
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.TimeoutException

/** No answer within the caller's bound: the request may still run and be delivered. */
class BridgeCallTimeout(timeoutMillis: Long) :
    Exception("no bridge answer within $timeoutMillis ms; the request may still be running")

/**
 * Runs bridge requests off the Android main thread, one at a time, in arrival order.
 *
 * Android delivers plugin commands on the main thread, and a discovery or a command can
 * take seconds; running it there would freeze the window. Each request is handed to
 * [handler] (the embedded Python `control_tv.embedded.handle`) on a single worker thread,
 * which also keeps the shared control layer single-flight, as the desktop bridge is.
 *
 * Nothing is retried: a request runs exactly once and its outcome, success or failure,
 * is reported exactly once.
 */
class BridgeRelay(
    private val handler: (String) -> String,
    private val worker: ExecutorService = Executors.newSingleThreadExecutor { runnable ->
        Thread(runnable, "control-tv-bridge").apply { isDaemon = true }
    },
) {
    fun submit(line: String, onResponse: (String) -> Unit, onFailure: (Throwable) -> Unit) {
        worker.execute {
            val outcome = runCatching { handler(line) }
            outcome.fold(onResponse, onFailure)
        }
    }

    /**
     * Runs [task] once on the same worker, after the requests already submitted and before
     * later ones. A failing task is reported by [onFailure] and does not stop the worker.
     */
    fun execute(task: () -> Unit, onFailure: (Throwable) -> Unit) {
        worker.execute {
            runCatching(task).onFailure(onFailure)
        }
    }

    /**
     * Runs [line] once on the worker and waits for its answer, for a caller already off the
     * main thread (the home-screen widget's background job). The handler's own failure is
     * rethrown unchanged ([PythonUnavailable] stays "not sent"); no answer within
     * [timeoutMillis] is [BridgeCallTimeout], and the request is neither cancelled nor resent.
     */
    fun call(line: String, timeoutMillis: Long): String {
        val answer = CompletableFuture<String>()
        submit(line, onResponse = { answer.complete(it) }, onFailure = { answer.completeExceptionally(it) })
        return try {
            answer.get(timeoutMillis, TimeUnit.MILLISECONDS)
        } catch (error: ExecutionException) {
            throw error.cause ?: error
        } catch (error: TimeoutException) {
            throw BridgeCallTimeout(timeoutMillis)
        }
    }

    fun shutdown() {
        worker.shutdown()
    }
}
