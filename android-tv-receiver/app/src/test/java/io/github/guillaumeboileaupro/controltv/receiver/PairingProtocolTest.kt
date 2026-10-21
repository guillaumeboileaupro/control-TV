package io.github.guillaumeboileaupro.controltv.receiver

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class PairingProtocolTest {
    private val token = "A".repeat(48)

    @Test fun validatesPairingTargets() {
        assertEquals(9999, PairingTarget("192.0.2.1", 9999, token).port)
        assertThrows(IllegalArgumentException::class.java) { PairingTarget("", 9999, token) }
        assertThrows(IllegalArgumentException::class.java) { PairingTarget("host", 0, token) }
        assertThrows(IllegalArgumentException::class.java) { PairingTarget("host", 9999, "short") }
    }

    @Test fun encodesAuthenticationWithoutLoggingOrEchoingOtherFields() {
        val hello = PairingProtocol.hello(PairingTarget("host", 9999, token))
        assertTrue(hello.contains(token))
        assertFalse(hello.contains("host"))
    }

    @Test fun requiresAnSdpOfferAndMakesRefusalExplicit() {
        assertEquals("v=0\r\ns=test", PairingProtocol.offer("{\"offer\":\"v=0\\r\\ns=test\"}"))
        assertThrows(IllegalArgumentException::class.java) { PairingProtocol.offer("{\"offer\":\"bad\"}") }
        assertTrue(PairingProtocol.answer("ignored", false).contains("\"accepted\":false"))
        assertFalse(PairingProtocol.answer("secret", false).contains("secret"))
    }
}

