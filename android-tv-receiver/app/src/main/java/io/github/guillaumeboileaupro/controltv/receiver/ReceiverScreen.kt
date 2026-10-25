package io.github.guillaumeboileaupro.controltv.receiver

/** What the TV shows, from the session's raw state text (the session itself is unchanged). */
internal enum class ReceiverPhase { WAITING, CONNECTING, STREAMING, INTERRUPTED, ERROR }

internal data class ReceiverView(
    val phase: ReceiverPhase,
    val title: String,
    val detail: String,
    /** Keep the TV awake: only while a stream is shown or about to be. */
    val keepScreenOn: Boolean,
)

internal object ReceiverScreen {
    private const val RESTART = "Relancez « Écran vers la TV » depuis ControlTV sur l'ordinateur."

    fun waiting(): ReceiverView = ReceiverView(
        ReceiverPhase.WAITING,
        "En attente de ControlTV",
        "Sur l'ordinateur, dans ControlTV, choisissez « Écran vers la TV » puis WebRTC. " +
            "Cette TV se connecte toute seule : il n'y a rien à saisir ici.",
        keepScreenOn = false,
    )

    /** The view for one state text of the session (`ReceiverSession`'s `onState`). */
    fun fromSession(state: String): ReceiverView {
        val text = state.trim()
        if (text.startsWith("Erreur")) {
            val reason = text.removePrefix("Erreur").trimStart(' ', ':').ifBlank { "erreur inconnue" }
            return ReceiverView(ReceiverPhase.ERROR, "La diffusion s'est arrêtée", "$reason. $RESTART", false)
        }
        return when (text.lowercase()) {
            "diffusion", "connected", "completed" ->
                ReceiverView(ReceiverPhase.STREAMING, "Diffusion en cours", "Écran de l'ordinateur", true)
            "disconnected" -> ReceiverView(
                ReceiverPhase.INTERRUPTED,
                "Connexion interrompue",
                "Reprise en cours… Si l'image ne revient pas, $RESTART",
                true,
            )
            "failed", "closed" ->
                ReceiverView(ReceiverPhase.ERROR, "La diffusion s'est arrêtée", RESTART, false)
            "connexion…", "négociation…", "new", "checking", "connecting" ->
                ReceiverView(ReceiverPhase.CONNECTING, "Connexion à l'ordinateur…", "", true)
            else -> ReceiverView(ReceiverPhase.CONNECTING, "Connexion à l'ordinateur…", text, true)
        }
    }
}

/**
 * Which session the screen belongs to. Each new session takes a new generation; a callback of an
 * older session (late after an interruption or a replacement) is ignored, so it can never reset or
 * close the screen of the session that replaced it.
 */
internal class SessionGeneration {
    private var current = 0L

    @Synchronized
    fun next(): Long = ++current

    /** Ends the current session: none of its callbacks applies any more. */
    @Synchronized
    fun retire() {
        current++
    }

    @Synchronized
    fun isCurrent(generation: Long): Boolean = generation == current
}
