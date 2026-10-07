package io.github.guillaumeboileaupro.controltv

import java.util.concurrent.CompletableFuture
import java.util.concurrent.ExecutionException
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.TimeUnit
import java.util.concurrent.TimeoutException
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

/** One request line in, one response line out, run directly inside an admitted transaction. */
interface BridgeTransaction {
    fun call(line: String): String

    /** False once the caller stopped waiting: no command may be claimed after that. */
    val stillWanted: Boolean
}

/** How [BridgeRelay.tryTransaction] ended. */
sealed interface TransactionResult<out T> {
    /** The worker was running or had work queued: the transaction never entered Python. */
    object Busy : TransactionResult<Nothing>

    /** No start within the bound: it was withdrawn and can never run later. */
    object NotStarted : TransactionResult<Nothing>

    /** It started but did not finish within the bound; it may still be running. */
    object TimedOutAfterStart : TransactionResult<Nothing>

    data class Done<T>(val value: T) : TransactionResult<T>
}

/** What the home-screen widget needs from the bridge: an atomic, non-queuing transaction. */
interface TransactionRunner {
    fun <T> tryTransaction(timeoutMillis: Long, body: (BridgeTransaction) -> T): TransactionResult<T>
}

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
 *
 * The window queues its requests ([submit]). The home-screen widget never queues: its
 * [tryTransaction] is admitted only when nothing runs or waits, then runs its whole
 * sequence as one task on the same worker, so nothing interleaves with it.
 */
class BridgeRelay(
    private val handler: (String) -> String,
    private val worker: ExecutorService = Executors.newSingleThreadExecutor { runnable ->
        Thread(runnable, "control-tv-bridge").apply { isDaemon = true }
    },
) : TransactionRunner {
    // Tasks submitted and not finished yet (running or queued).
    private val pending = AtomicInteger(0)

    fun submit(line: String, onResponse: (String) -> Unit, onFailure: (Throwable) -> Unit) {
        enqueue {
            val outcome = runCatching { handler(line) }
            outcome.fold(onResponse, onFailure)
        }
    }

    /**
     * Runs [task] once on the same worker, after the requests already submitted and before
     * later ones. A failing task is reported by [onFailure] and does not stop the worker.
     */
    fun execute(task: () -> Unit, onFailure: (Throwable) -> Unit) {
        enqueue { runCatching(task).onFailure(onFailure) }
    }

    /**
     * Runs [body] as one transaction on the worker, only if the worker is idle with nothing
     * queued; otherwise [TransactionResult.Busy] at once, and nothing enters Python. Inside,
     * [BridgeTransaction.call] runs the handler directly on the worker: requests submitted
     * meanwhile wait until the transaction ends. If it has not started within
     * [timeoutMillis] it is withdrawn and never runs; if it started and has not finished,
     * [BridgeTransaction.stillWanted] turns false so it can claim no command afterwards.
     */
    override fun <T> tryTransaction(timeoutMillis: Long, body: (BridgeTransaction) -> T): TransactionResult<T> {
        if (!pending.compareAndSet(0, 1)) return TransactionResult.Busy
        val state = AtomicInteger(PENDING)
        val abandoned = AtomicBoolean(false)
        val answer = CompletableFuture<T>()
        val transaction = object : BridgeTransaction {
            override fun call(line: String): String = handler(line)
            override val stillWanted: Boolean get() = !abandoned.get()
        }
        try {
            worker.execute {
                try {
                    if (state.compareAndSet(PENDING, STARTED)) {
                        runCatching { body(transaction) }.fold(
                            { answer.complete(it) },
                            { answer.completeExceptionally(it) },
                        )
                    }
                } finally {
                    pending.decrementAndGet()
                }
            }
        } catch (error: RejectedExecutionException) {
            pending.decrementAndGet()
            return TransactionResult.Busy
        }
        return try {
            TransactionResult.Done(answer.get(timeoutMillis, TimeUnit.MILLISECONDS))
        } catch (error: ExecutionException) {
            throw error.cause ?: error
        } catch (error: TimeoutException) {
            abandoned.set(true)
            if (state.compareAndSet(PENDING, ABANDONED)) TransactionResult.NotStarted
            else TransactionResult.TimedOutAfterStart
        }
    }

    fun shutdown() {
        worker.shutdown()
    }

    private fun enqueue(task: () -> Unit) {
        pending.incrementAndGet()
        try {
            worker.execute {
                try {
                    task()
                } finally {
                    pending.decrementAndGet()
                }
            }
        } catch (error: RejectedExecutionException) {
            pending.decrementAndGet()
            throw error
        }
    }

    private companion object {
        const val PENDING = 0
        const val STARTED = 1
        const val ABANDONED = 2
    }
}
