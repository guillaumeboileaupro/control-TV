package io.github.guillaumeboileaupro.controltv.receiver

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class OneShotGateTest {
    @Test
    fun claimSucceedsOnlyOnce() {
        val gate = OneShotGate()
        assertTrue(gate.claim())
        assertFalse(gate.claim())
    }

    @Test
    fun closePreventsTheWatchdogFromExpiring() {
        val gate = OneShotGate()
        gate.close()
        assertFalse(gate.claim())
    }
}
