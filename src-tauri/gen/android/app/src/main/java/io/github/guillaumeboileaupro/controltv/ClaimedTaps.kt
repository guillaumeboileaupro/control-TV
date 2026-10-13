package io.github.guillaumeboileaupro.controltv

import org.json.JSONException
import org.json.JSONObject

/**
 * The durable record of every recently claimed tap: tap id -> when its command was claimed
 * (wall clock, ms), stored as one JSON object in the app's private preferences. Kept free of
 * Android types so it can be tested.
 *
 * A tap is command-eligible only while its age is within 0..[WidgetActionRunner.MAX_TAP_AGE_MILLIS]
 * (60 s), and its claim happens inside that window, so claimedAt >= tappedAt. An entry is
 * forgotten only once it is older than [RETENTION_MILLIS] (10 min): its tap is then more than
 * 10 min old and can never be eligible again, so forgetting it cannot reopen a re-run. Each
 * claim adds its own entry: a newer tap never removes an older one's protection.
 */
object ClaimedTaps {
    /** How long a claim is kept: ten times the tap's 60 s lifetime. */
    const val RETENTION_MILLIS = 10 * 60_000L

    /** Above this many entries, entries older than [EVICTABLE_AFTER_MILLIS] are dropped first. */
    const val MAX_ENTRIES = 256

    /** Twice the tap's lifetime: an entry this old protects a tap that can no longer run. */
    const val EVICTABLE_AFTER_MILLIS = 2 * WidgetActionRunner.MAX_TAP_AGE_MILLIS

    /**
     * The record with [tapId] claimed at [now], or null when [tapId] is already claimed (or the
     * record cannot be read: then nothing proves the tap was not claimed, so it is refused).
     */
    fun claim(stored: String?, tapId: String, now: Long): String? {
        val claims = parse(stored) ?: return null
        if (claims.containsKey(tapId)) return null
        // Forget only what can never be eligible again; an entry dated in the future (the clock
        // went back) is kept.
        claims.entries.removeAll { now - it.value > RETENTION_MILLIS }
        if (claims.size >= MAX_ENTRIES) {
            val evictable = claims.entries.filter { now - it.value > EVICTABLE_AFTER_MILLIS }.sortedBy { it.value }
            evictable.take(claims.size - MAX_ENTRIES + 1).forEach { claims.remove(it.key) }
            // Entries younger than that are never dropped, even above the bound: protection
            // first. Taps run one at a time on the bridge, so this stays small in practice.
        }
        claims[tapId] = now
        return JSONObject(claims as Map<*, *>).toString()
    }

    /** The claimed tap ids in [stored]; null if it is not a record this app wrote. */
    fun parse(stored: String?): MutableMap<String, Long>? {
        if (stored == null) return mutableMapOf()
        return try {
            val json = JSONObject(stored)
            json.keys().asSequence().associateWithTo(mutableMapOf()) { json.getLong(it) }
        } catch (error: JSONException) {
            null
        }
    }
}
