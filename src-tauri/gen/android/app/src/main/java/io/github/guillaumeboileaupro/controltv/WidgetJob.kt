package io.github.guillaumeboileaupro.controltv

/** One widget tap handed to the background job: its stable identity and when it happened. */
data class TapSpec(val action: WidgetAction, val tapId: String, val tappedAtMillis: Long)

/**
 * The background job's work for one tap, kept free of Android types so it can be tested.
 *
 * The whole tap (reads, choice, at most one command) runs as one non-queuing transaction on
 * the app's single bridge worker ([TransactionRunner]): if the window is using the bridge,
 * the tap is turned down at once and nothing enters Python. The command can only be claimed
 * while the tap is still young and its job still waits for it.
 */
class WidgetJob(
    private val store: WidgetStore,
    private val bridge: TransactionRunner,
    private val now: () -> Long,
    private val log: (String) -> Unit = {},
) {
    fun run(tap: TapSpec): WidgetOutcome {
        val selection = store.selection()
        if (selection == null) {
            log("widget transaction: no TV chosen")
            return WidgetOutcome.NoSelection
        }
        val age = { now() - tap.tappedAtMillis }
        if (age() !in 0..WidgetActionRunner.MAX_TAP_AGE_MILLIS) {
            log("widget transaction: tap expired before it started (${tap.action.name})")
            return WidgetOutcome.Expired
        }
        val result = bridge.tryTransaction(TRANSACTION_TIMEOUT_MILLIS) { transaction ->
            log("widget transaction admitted: ${tap.action.name}")
            WidgetActionRunner(transaction::call, TapGuard(store, now), log).run(
                tap.action,
                selection,
                tap.tapId,
                tapAgeMillis = age(),
                mayCommand = { transaction.stillWanted && age() in 0..WidgetActionRunner.MAX_TAP_AGE_MILLIS },
            )
        }
        val outcome = when (result) {
            is TransactionResult.Done -> result.value
            TransactionResult.Busy -> WidgetOutcome.Busy
            TransactionResult.NotStarted -> WidgetOutcome.Expired
            // Started, still running: no command can be claimed any more, but one may already
            // have been handed over.
            TransactionResult.TimedOutAfterStart -> WidgetOutcome.MaybeSent("command")
        }
        log("widget transaction ${describe(result)}: ${tap.action.name} -> ${outcome::class.simpleName}")
        return outcome
    }

    private fun describe(result: TransactionResult<*>): String = when (result) {
        is TransactionResult.Done -> "completed"
        TransactionResult.Busy -> "busy, not admitted"
        TransactionResult.NotStarted -> "not started in time, withdrawn"
        TransactionResult.TimedOutAfterStart -> "timed out after it started"
    }

    companion object {
        // Longer than any single shared-layer call (discovery 5 s, a command's confirmation
        // window) and shorter than the tap's lifetime.
        const val TRANSACTION_TIMEOUT_MILLIS = 45_000L
    }
}
