package io.github.guillaumeboileaupro.controltv

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/**
 * At most one command per tap, even when WorkManager runs an older tap's job again after a
 * process death and newer taps ran in between (Codex P2 on 72b1d26: the guard kept only the
 * last claimed tap, so A -> B -> A again could send A's command twice).
 */
class TapReplayTest {
    private var clock = 1_000_000_000L

    private val tv = FakeBridge { method, _ ->
        when (method) {
            "get_status" -> statusAnswer(status())
            else -> commandAnswer(method, "confirmed")
        }
    }

    private fun run(store: WidgetStore, tapId: String): WidgetOutcome =
        WidgetActionRunner(tv, TapGuard(store) { clock }).run(WidgetAction.STOP, SELECTED, tapId)

    /** A new process: a new store and guard over what the first one wrote to disk. */
    private fun restarted(store: MemoryWidgetStore) = MemoryWidgetStore().also { it.claimed = store.claimed }

    @Test
    fun anOlderTapRerunAfterANewerOneAndARestartSendsNothing() {
        val first = MemoryWidgetStore()
        assertTrue(run(first, "tap-a") is WidgetOutcome.Sent)
        clock += 5_000
        assertTrue(run(first, "tap-b") is WidgetOutcome.Sent)
        assertEquals(2, tv.commands.size)

        clock += 5_000
        val outcome = run(restarted(first), "tap-a")

        assertEquals(WidgetOutcome.MaybeSent("stop"), outcome)
        assertEquals("A's re-run sends no command", 2, tv.commands.size)
    }

    @Test
    fun anOlderTapDeliveredAgainAfterANewerOneInTheSameProcessSendsNothing() {
        val store = MemoryWidgetStore()
        run(store, "tap-a")
        run(store, "tap-b")

        assertEquals(WidgetOutcome.MaybeSent("stop"), run(store, "tap-a"))
        assertEquals(WidgetOutcome.MaybeSent("stop"), run(store, "tap-b"))
        assertEquals(2, tv.commands.size)
    }

    @Test
    fun manyRecentTapsAreAllStillProtected() {
        val store = MemoryWidgetStore()
        val taps = (1..20).map { "tap-$it" }
        for (tap in taps) {
            assertTrue(TapGuard(store) { clock }.claim(tap))
            clock += 1_000
        }

        val guard = TapGuard(restarted(store)) { clock }
        for (tap in taps) assertFalse(tap, guard.claim(tap))
    }

    @Test
    fun aClaimIsKeptLongAfterItsTapCanNoLongerRun() {
        assertTrue(ClaimedTaps.RETENTION_MILLIS >= 10 * WidgetActionRunner.MAX_TAP_AGE_MILLIS)
        val stored = ClaimedTaps.claim(null, "tap-a", clock)

        // Another tap at the last moment a re-run of A could still be eligible, and later.
        for (later in listOf(WidgetActionRunner.MAX_TAP_AGE_MILLIS, ClaimedTaps.RETENTION_MILLIS)) {
            val after = ClaimedTaps.claim(stored, "tap-new", clock + later)!!
            assertTrue("kept after $later ms", "tap-a" in ClaimedTaps.parse(after)!!)
            assertNull(ClaimedTaps.claim(after, "tap-a", clock + later))
        }
    }

    @Test
    fun onlyClaimsOlderThanTheRetentionAreForgottenAndAFutureDatedOneIsKept() {
        var stored: String? = null
        stored = ClaimedTaps.claim(stored, "old", clock)
        stored = ClaimedTaps.claim(stored, "future", clock + 3_600_000L)
        stored = ClaimedTaps.claim(stored, "recent", clock + ClaimedTaps.RETENTION_MILLIS)

        val later = clock + ClaimedTaps.RETENTION_MILLIS + 1
        val kept = ClaimedTaps.parse(ClaimedTaps.claim(stored, "now", later))!!.keys

        assertEquals(setOf("future", "recent", "now"), kept)
    }

    @Test
    fun theRecordStaysBoundedWithoutDroppingATapThatCouldStillRun() {
        // Full of claims old enough to be evictable (but within the retention).
        var stored: String? = null
        val oldEnough = clock - ClaimedTaps.EVICTABLE_AFTER_MILLIS - 10_000
        repeat(ClaimedTaps.MAX_ENTRIES + 10) { stored = ClaimedTaps.claim(stored, "old-$it", oldEnough + it) }
        stored = ClaimedTaps.claim(stored, "new", clock)
        val bounded = ClaimedTaps.parse(stored)!!
        assertEquals(ClaimedTaps.MAX_ENTRIES, bounded.size)
        assertTrue("new" in bounded)
        assertFalse("the oldest go first", "old-0" in bounded)

        // Full of young claims: none is dropped, protection before the bound.
        var young: String? = null
        repeat(ClaimedTaps.MAX_ENTRIES + 5) { young = ClaimedTaps.claim(young, "young-$it", clock + it) }
        assertEquals(ClaimedTaps.MAX_ENTRIES + 5, ClaimedTaps.parse(young)!!.size)
    }

    @Test
    fun anUnreadableRecordRefusesTheClaimAndKeepsTheRecord() {
        val store = MemoryWidgetStore().also { it.claimed = "not a record" }

        assertEquals(WidgetOutcome.MaybeSent("stop"), run(store, "tap-a"))
        assertEquals(0, tv.commands.size)
        assertEquals("not a record", store.claimed)
    }

    @Test
    fun theRecordHoldsOnlyTapIdsAndClaimTimes() {
        val stored = ClaimedTaps.claim(null, "tap-a", clock)!!

        assertEquals(clock, JSONObject(stored).getLong("tap-a"))
        assertEquals(1, JSONObject(stored).length())
    }

    @Test
    fun concurrentClaimsOfTheSameTapAdmitExactlyOne() {
        val store = MemoryWidgetStore()
        val pool = Executors.newFixedThreadPool(8)
        val start = CountDownLatch(1)
        val admitted = AtomicInteger()
        repeat(64) {
            pool.execute {
                start.await()
                if (TapGuard(store) { clock }.claim("tap-a")) admitted.incrementAndGet()
            }
        }
        start.countDown()
        pool.shutdown()
        assertTrue(pool.awaitTermination(10, TimeUnit.SECONDS))

        assertEquals(1, admitted.get())
    }
}
