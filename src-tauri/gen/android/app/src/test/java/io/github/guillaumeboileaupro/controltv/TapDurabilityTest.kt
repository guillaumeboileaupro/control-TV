package io.github.guillaumeboileaupro.controltv

import android.content.Context
import android.content.ContextWrapper
import android.content.SharedPreferences
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/**
 * The durable claim through the real preference-backed store (Codex P3 on 0dbe21b): an update
 * from the single-marker version, a write that fails, and the exact 120 s eviction boundary.
 */
class TapDurabilityTest {
    private val clock = 1_000_000_000L

    private val tv = FakeBridge { method, _ ->
        when (method) {
            "get_status" -> statusAnswer(status())
            else -> commandAnswer(method, "confirmed")
        }
    }

    private fun run(store: WidgetStore, tapId: String): WidgetOutcome =
        WidgetActionRunner(tv, TapGuard(store) { clock }).run(WidgetAction.STOP, SELECTED, tapId)

    @Test
    fun aTapClaimedByTheSingleMarkerVersionStaysClaimedAfterTheUpdate() {
        val prefs = MemoryPreferences(mutableMapOf("claimed_tap" to "tap-a"))
        val store = PreferencesWidgetStore(contextWith(prefs))

        assertEquals(WidgetOutcome.MaybeSent("stop"), run(store, "tap-a"))
        assertEquals("the legacy tap hands nothing over", 0, tv.commands.size)

        // A newer tap migrates the record; the legacy tap stays claimed in it, after a restart too.
        assertTrue(run(store, "tap-b") is WidgetOutcome.Sent)
        assertNull(prefs.values["claimed_tap"])
        assertEquals(setOf("tap-a", "tap-b"), ClaimedTaps.parse(prefs.values["claimed_taps"] as String)!!.keys)
        assertEquals(WidgetOutcome.MaybeSent("stop"), run(PreferencesWidgetStore(contextWith(prefs)), "tap-a"))
        assertEquals(listOf("stop"), tv.commands.map { it.first })
    }

    @Test
    fun aClaimThatCannotBeWrittenFailsClosedAndHandsNothingOver() {
        val prefs = MemoryPreferences(commits = false)
        val store = PreferencesWidgetStore(contextWith(prefs))

        try {
            run(store, "tap-a")
            fail("a claim that was not written must not let the command through")
        } catch (expected: IllegalStateException) {
            // WidgetActionWorker reports this as "may have been sent" and never retries it.
        }
        assertEquals(listOf("get_status"), tv.methods)
        assertEquals(0, tv.commands.size)
        assertFalse("nothing recorded", "claimed_taps" in prefs.values)
    }

    @Test
    fun underCapacityPressureAClaimExactly120SecondsOldIsKeptAndOnlyOlderOnesGo() {
        assertTrue(ClaimedTaps.EVICTABLE_AFTER_MILLIS >= 2 * WidgetActionRunner.MAX_TAP_AGE_MILLIS)
        val edge = ClaimedTaps.EVICTABLE_AFTER_MILLIS
        // More evictions are wanted than there are claims strictly older than 120 s, so the
        // claim at exactly 120 s is the next candidate: it must stay.
        val claims = mutableMapOf<String, Long>()
        repeat(5) { claims["older-$it"] = clock - edge - 1 - it }
        claims["edge"] = clock - edge
        repeat(ClaimedTaps.MAX_ENTRIES) { claims["young-$it"] = clock - 1_000 - it }
        val stored = JSONObject(claims as Map<*, *>).toString()

        val after = ClaimedTaps.parse(ClaimedTaps.claim(stored, "new", clock))!!

        assertTrue("edge" in after)
        assertTrue((0 until 5).none { "older-$it" in after })
        assertTrue("new" in after)
        val store = MemoryWidgetStore().also { it.claimed = JSONObject(after as Map<*, *>).toString() }
        assertFalse("the boundary tap is not reopened", TapGuard(store) { clock }.claim("edge"))

        // One millisecond later it is strictly older than 120 s and may go.
        val later = ClaimedTaps.parse(ClaimedTaps.claim(store.claimed, "newer", clock + 1))!!
        assertFalse("edge" in later)
    }

    private fun contextWith(prefs: SharedPreferences): Context = object : ContextWrapper(null) {
        override fun getApplicationContext(): Context = this
        override fun getSharedPreferences(name: String?, mode: Int): SharedPreferences = prefs
    }
}

/** In-memory [SharedPreferences]; with [commits] false every commit fails and writes nothing. */
class MemoryPreferences(
    val values: MutableMap<String, Any?> = mutableMapOf(),
    private val commits: Boolean = true,
) : SharedPreferences {
    override fun getAll(): MutableMap<String, *> = values.toMutableMap()
    override fun getString(key: String, defValue: String?): String? = values[key] as String? ?: defValue
    override fun getStringSet(key: String, defValues: MutableSet<String>?): MutableSet<String>? = defValues
    override fun getInt(key: String, defValue: Int): Int = values[key] as Int? ?: defValue
    override fun getLong(key: String, defValue: Long): Long = values[key] as Long? ?: defValue
    override fun getFloat(key: String, defValue: Float): Float = values[key] as Float? ?: defValue
    override fun getBoolean(key: String, defValue: Boolean): Boolean = values[key] as Boolean? ?: defValue
    override fun contains(key: String): Boolean = key in values
    override fun registerOnSharedPreferenceChangeListener(l: SharedPreferences.OnSharedPreferenceChangeListener) {}
    override fun unregisterOnSharedPreferenceChangeListener(l: SharedPreferences.OnSharedPreferenceChangeListener) {}

    override fun edit(): SharedPreferences.Editor = object : SharedPreferences.Editor {
        private val changes = mutableMapOf<String, Any?>()
        private val removed = mutableSetOf<String>()
        override fun putString(key: String, value: String?) = apply { changes[key] = value }
        override fun putStringSet(key: String, values: MutableSet<String>?) = apply { changes[key] = values }
        override fun putInt(key: String, value: Int) = apply { changes[key] = value }
        override fun putLong(key: String, value: Long) = apply { changes[key] = value }
        override fun putFloat(key: String, value: Float) = apply { changes[key] = value }
        override fun putBoolean(key: String, value: Boolean) = apply { changes[key] = value }
        override fun remove(key: String) = apply { removed += key }
        override fun clear() = apply { removed += values.keys }
        override fun commit(): Boolean {
            if (!commits) return false
            removed.forEach { values.remove(it) }
            values.putAll(changes)
            return true
        }
        override fun apply() {
            commit()
        }
    }
}
