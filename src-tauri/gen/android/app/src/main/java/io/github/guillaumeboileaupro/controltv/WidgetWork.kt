package io.github.guillaumeboileaupro.controltv

import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequest
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.OutOfQuotaPolicy
import androidx.work.workDataOf
import java.security.MessageDigest

/**
 * How one accepted widget tap becomes WorkManager work.
 *
 * - Expedited: a tap is short, user-initiated work that must start now. Ordinary work can be
 *   deferred indefinitely for an app in the background (aggressively so on some phones), which
 *   left a tapped widget saying "Working…" with no job ever started. Out of expedited quota it
 *   still runs, as ordinary work.
 * - One unique name per tap, derived from the tap's token: the same tap delivered again maps
 *   to the same work and is kept once (KEEP), while a job of an earlier tap that has not run
 *   can no longer block every later tap, as one shared name did. Jobs never queue on the
 *   bridge: an overlapping one is turned down by the non-queuing transaction, and a late one
 *   expires (60 s).
 * - The name carries a hash of the token, never the token itself.
 */
object WidgetWork {
    val POLICY = ExistingWorkPolicy.KEEP

    fun uniqueName(tapId: String): String {
        val digest = MessageDigest.getInstance("SHA-256").digest(tapId.toByteArray())
        return "control-tv-widget-tap-" + digest.take(12).joinToString("") { "%02x".format(it) }
    }

    fun request(tap: TapSpec): OneTimeWorkRequest =
        OneTimeWorkRequestBuilder<WidgetActionWorker>()
            .setExpedited(OutOfQuotaPolicy.RUN_AS_NON_EXPEDITED_WORK_REQUEST)
            .setInputData(
                workDataOf(
                    WidgetActionWorker.KEY_ACTION to tap.action.name,
                    WidgetActionWorker.KEY_TAP to tap.tapId,
                    WidgetActionWorker.KEY_TAPPED_AT to tap.tappedAtMillis,
                ),
            )
            .build()
}
