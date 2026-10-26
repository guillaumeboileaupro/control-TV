package io.github.guillaumeboileaupro.controltv.receiver

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ReceiverScreenTest {
    @Test
    fun waitingExplainsWhereTheStreamStartsAndKeepsNoScreenAwake() {
        val view = ReceiverScreen.waiting()
        assertEquals(ReceiverPhase.WAITING, view.phase)
        assertTrue(view.detail.contains("Écran vers la TV"))
        assertFalse(view.keepScreenOn)
    }

    @Test
    fun eachSessionStateTextHasItsScreen() {
        val phases = mapOf(
            "Connexion…" to ReceiverPhase.CONNECTING,
            "Négociation…" to ReceiverPhase.CONNECTING,
            "checking" to ReceiverPhase.CONNECTING,
            "connecting" to ReceiverPhase.CONNECTING,
            "Diffusion" to ReceiverPhase.STREAMING,
            "connected" to ReceiverPhase.STREAMING,
            "completed" to ReceiverPhase.STREAMING,
            "disconnected" to ReceiverPhase.INTERRUPTED,
            "failed" to ReceiverPhase.ERROR,
            "closed" to ReceiverPhase.ERROR,
            "Erreur : délai de négociation dépassé" to ReceiverPhase.ERROR,
            "Erreur SDP : offre invalide" to ReceiverPhase.ERROR,
        )
        for ((state, phase) in phases) {
            assertEquals(state, phase, ReceiverScreen.fromSession(state).phase)
        }
    }

    @Test
    fun theScreenStaysAwakeOnlyWhileAStreamIsShownOrComing() {
        assertTrue(ReceiverScreen.fromSession("Diffusion").keepScreenOn)
        assertTrue(ReceiverScreen.fromSession("disconnected").keepScreenOn)
        assertTrue(ReceiverScreen.fromSession("Connexion…").keepScreenOn)
        assertFalse(ReceiverScreen.fromSession("failed").keepScreenOn)
        assertFalse(ReceiverScreen.fromSession("Erreur : envoi de la réponse impossible").keepScreenOn)
    }

    @Test
    fun anErrorKeepsItsReasonAndSaysHowToRestart() {
        val view = ReceiverScreen.fromSession("Erreur : délai de négociation dépassé")
        assertEquals("La diffusion s'est arrêtée", view.title)
        assertTrue(view.detail.startsWith("délai de négociation dépassé."))
        assertTrue(view.detail.contains("Relancez"))
        assertTrue(ReceiverScreen.fromSession("Erreur").detail.startsWith("erreur inconnue."))
    }

    @Test
    fun anUnknownStateIsShownAsConnectingWithItsText() {
        val view = ReceiverScreen.fromSession("  gathering ")
        assertEquals(ReceiverPhase.CONNECTING, view.phase)
        assertEquals("gathering", view.detail)
    }

    @Test
    fun aRetiredOrReplacedSessionNoLongerOwnsTheScreen() {
        val generations = SessionGeneration()
        val first = generations.next()
        assertTrue(generations.isCurrent(first))

        val second = generations.next()
        assertFalse("a replaced session's late callback is ignored", generations.isCurrent(first))
        assertTrue(generations.isCurrent(second))

        generations.retire()
        assertFalse("a closed session's late callback is ignored", generations.isCurrent(second))
    }
}
