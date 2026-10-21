package io.github.guillaumeboileaupro.controltv.receiver

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class PairingProtocolTest {
    private val token = "A".repeat(48)
    private val fingerprint = List(32) { "AB" }.joinToString(":")

    @Test fun validatesPairingTargets() {
        assertEquals(9999, PairingTarget("192.0.2.1", 9999, token, fingerprint).port)
        assertThrows(IllegalArgumentException::class.java) { PairingTarget("", 9999, token, fingerprint) }
        assertThrows(IllegalArgumentException::class.java) { PairingTarget("host", 0, token, fingerprint) }
        assertThrows(IllegalArgumentException::class.java) { PairingTarget("host", 9999, "short", fingerprint) }
        assertThrows(IllegalArgumentException::class.java) { PairingTarget("host", 9999, token, "invalid") }
    }

    @Test fun encodesAuthenticationWithoutLoggingOrEchoingOtherFields() {
        val target = PairingTarget("host", 9999, token, fingerprint)
        val hello = PairingProtocol.hello(target)
        assertTrue(hello.contains(token))
        assertFalse(hello.contains("host"))
        assertFalse(target.toString().contains(token))
        assertFalse(target.toString().contains(fingerprint))
    }

    @Test fun requiresAnSdpOfferAndMakesRefusalExplicit() {
        assertEquals("v=0\r\ns=test", PairingProtocol.offer("{\"offer\":\"v=0\\r\\ns=test\"}"))
        assertThrows(IllegalArgumentException::class.java) { PairingProtocol.offer("{\"offer\":\"bad\"}") }
        assertTrue(PairingProtocol.answer("ignored", false).contains("\"accepted\":false"))
        assertFalse(PairingProtocol.answer("secret", false).contains("secret"))
    }

    @Test fun sendsTheFinalAnswerOnlyAfterLocalSdpAndIceCompletion() {
        val localFirst = IceCompleteAnswerGate()
        localFirst.markLocalDescriptionSet()
        assertEquals(null, localFirst.take("v=0\r\na=early"))
        localFirst.markIceComplete()
        assertEquals("v=0\r\na=final", localFirst.take("v=0\r\na=final"))
        assertEquals(null, localFirst.take("v=0\r\na=replay"))

        val iceFirst = IceCompleteAnswerGate()
        iceFirst.markIceComplete()
        assertEquals(null, iceFirst.take("v=0\r\na=early"))
        iceFirst.markLocalDescriptionSet()
        assertEquals("v=0\r\na=final", iceFirst.take("v=0\r\na=final"))
    }
}

