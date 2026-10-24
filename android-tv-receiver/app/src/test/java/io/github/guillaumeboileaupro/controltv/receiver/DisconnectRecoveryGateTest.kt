package io.github.guillaumeboileaupro.controltv.receiver

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class DisconnectRecoveryGateTest {
    @Test
    fun a_connection_that_returns_within_the_grace_period_cancels_retry() {
        val gate = DisconnectRecoveryGate()
        val ticket = gate.disconnected()

        gate.recovered()

        assertFalse(gate.isStillDisconnected(ticket))
    }

    @Test
    fun only_the_latest_unrecovered_disconnect_can_request_retry() {
        val gate = DisconnectRecoveryGate()
        val first = gate.disconnected()
        val second = gate.disconnected()

        assertFalse(gate.isStillDisconnected(first))
        assertTrue(gate.isStillDisconnected(second))
    }
}
