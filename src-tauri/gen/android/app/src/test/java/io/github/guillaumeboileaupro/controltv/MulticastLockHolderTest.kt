package io.github.guillaumeboileaupro.controltv

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

private class FakeLock : MulticastLockApi {
    var acquired = 0
    var released = 0
    override var isHeld = false
        private set

    override fun acquire() {
        acquired++
        isHeld = true
    }

    override fun release() {
        check(isHeld) { "released a lock that is not held" }
        released++
        isHeld = false
    }
}

class MulticastLockHolderTest {
    @Test
    fun theLockIsHeldInTheForegroundAndReleasedInTheBackground() {
        val lock = FakeLock()
        val holder = MulticastLockHolder(lock)

        holder.acquire()
        assertTrue(holder.isHeld)
        holder.release()
        assertFalse(holder.isHeld)
    }

    @Test
    fun repeatedTransitionsNeverStackOrOverReleaseTheLock() {
        val lock = FakeLock()
        val holder = MulticastLockHolder(lock)

        holder.acquire()
        holder.acquire()
        holder.release()
        holder.release()
        holder.acquire()

        assertEquals(2, lock.acquired)
        assertEquals(1, lock.released)
        assertTrue(holder.isHeld)
    }

    @Test
    fun releasingALockThatWasNeverHeldDoesNothing() {
        val lock = FakeLock()

        MulticastLockHolder(lock).release()

        assertEquals(0, lock.released)
        assertFalse(lock.isHeld)
    }
}
