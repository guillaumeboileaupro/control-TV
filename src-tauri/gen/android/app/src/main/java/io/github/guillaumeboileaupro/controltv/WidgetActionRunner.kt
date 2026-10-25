package io.github.guillaumeboileaupro.controltv

import org.json.JSONObject

/**
 * What a widget button asks for. A button with a [key] presses that key of the TV's own remote
 * (`press_remote_key`): it needs no Cast session and no volume reported by the TV. The others
 * pick a Cast command from the state the TV reported. New entries go last: a button's pending
 * intent is numbered by its position.
 */
enum class WidgetAction(val key: String? = null) {
    PLAY_PAUSE,
    STOP,
    MUTE("mute"),
    VOLUME_DOWN("volume_down"),
    VOLUME_UP("volume_up"),
    REFRESH,
    POWER("power"),
    UP("up"),
    DOWN("down"),
    LEFT("left"),
    RIGHT("right"),
    OK("ok"),
    BACK("back"),
    HOME("home"),
    ASSISTANT("assistant"),
}

/** The part of a status report the widget reads; `null` means not reported. */
data class TvState(
    val connected: Boolean,
    val playback: String?,
    val title: String?,
    val supportsPause: Boolean?,
    val volumeLevel: Double?,
    val volumeFixed: Boolean,
    val muted: Boolean?,
)

/** How a widget tap ended. Only [Sent] and [MaybeSent] involve a command reaching the bridge. */
sealed interface WidgetOutcome {
    object NoSelection : WidgetOutcome
    object Expired : WidgetOutcome
    object SelectionChanged : WidgetOutcome
    object Busy : WidgetOutcome
    object NotFound : WidgetOutcome
    object Unavailable : WidgetOutcome
    object StatusUnreadable : WidgetOutcome
    data class Status(val state: TvState) : WidgetOutcome
    data class NoCommand(val reason: String, val state: TvState) : WidgetOutcome
    data class Sent(val command: String, val confirmation: String, val observed: TvState?) : WidgetOutcome
    data class KeySent(val action: WidgetAction, val confirmation: String) : WidgetOutcome
    data class NotSent(val code: String) : WidgetOutcome
    data class MaybeSent(val command: String) : WidgetOutcome
}

/**
 * Durable "this tap's command was already handed to the bridge". The widget's background job
 * can be run again by Android after a process death, possibly after newer taps ran: every
 * claimed tap is kept ([ClaimedTaps]) for ten times its lifetime, so a tap claimed once is
 * never claimed again while it could still run. The check and the insertion are one atomic,
 * durable store update.
 */
class TapGuard(private val store: WidgetStore, private val now: () -> Long = System::currentTimeMillis) {
    fun claim(tapId: String): Boolean = store.updateClaimedTaps { ClaimedTaps.claim(it, tapId, now()) }
}

/**
 * One widget tap against the shared control layer, through the same bridge request lines as
 * the window (`control_tv.embedded.handle`): `get_status`, `discover_devices` and the
 * command methods. No Cast, validation or confirmation logic is here; it only picks, like
 * the window does, which single command the observed state calls for.
 *
 * Reads come first and may be repeated (a read sends nothing). Then at most one command is
 * sent, once: whatever happens to it, nothing is retried, and an answer that does not prove
 * the outcome stays ambiguous ([WidgetOutcome.MaybeSent]).
 *
 * [log] gets, for a command, what the TV reported before it, the exact value requested and
 * how the shared layer answered (confirmed, unconfirmed, not checked, refused or undetermined):
 * enough to tell a command the TV ignored from one it was never asked for. Never a device id,
 * name, address or media title.
 */
class WidgetActionRunner(
    private val bridge: (String) -> String,
    private val guard: TapGuard,
    private val log: (String) -> Unit = {},
) {
    private var nextId = 1

    /**
     * [tapAgeMillis]: how long ago the tap happened. A job that starts too late (Android can
     * hold a queued job back, and reschedules it after a reboot) does nothing at all, so a
     * command never arrives long after the tap that asked for it. [mayCommand] is asked again
     * immediately before the command is claimed (the tap may have aged during the reads, or
     * its caller stopped waiting): false means no command at all.
     */
    fun run(
        action: WidgetAction,
        selection: Selection?,
        tapId: String,
        tapAgeMillis: Long = 0,
        mayCommand: () -> Boolean = { true },
    ): WidgetOutcome {
        if (selection == null) return WidgetOutcome.NoSelection
        if (tapAgeMillis !in 0..MAX_TAP_AGE_MILLIS) return WidgetOutcome.Expired
        action.key?.let { return pressKey(action, it, selection, tapId, mayCommand) }
        val state = when (val read = readStatus(selection.deviceId)) {
            is StatusRead.Read -> read.state
            is StatusRead.Failed -> return read.outcome
        }
        if (action == WidgetAction.REFRESH) return WidgetOutcome.Status(state)
        val command = when (val choice = choose(action, state)) {
            is Choice.Send -> choice
            is Choice.Skip -> return WidgetOutcome.NoCommand(choice.reason, state)
        }
        if (!mayCommand()) return WidgetOutcome.Expired
        // Claimed durably before the send: a re-run of this tap never sends a second time.
        if (!guard.claim(tapId)) return WidgetOutcome.MaybeSent(command.method)
        log("widget command: ${command.method} (TV reported ${reported(state)})")
        val params = JSONObject().put("deviceId", selection.deviceId)
        val result = when (val delivery = deliver(command.method, params, tapId)) {
            is Delivery.Answered -> delivery.result
            is Delivery.Failed -> return delivery.outcome
        }
        val observed = result.optJSONObject("observed")?.let(::parseState)
        val confirmation = result.getString("confirmation")
        val after = observed?.let { " (TV then reported ${reported(it)})" } ?: ""
        log("widget command result: ${command.method} -> sent, $confirmation$after")
        return WidgetOutcome.Sent(command.method, confirmation, observed)
    }

    /**
     * One key of the TV's remote, claimed and sent at most once. A TV not known to this process
     * yet (it may have just started) is refused before anything is sent: one read-only
     * discovery, then the selected id only, and the key is sent then if the tap may still run.
     */
    private fun pressKey(
        action: WidgetAction,
        key: String,
        selection: Selection,
        tapId: String,
        mayCommand: () -> Boolean,
    ): WidgetOutcome {
        if (!mayCommand()) return WidgetOutcome.Expired
        if (!guard.claim(tapId)) return WidgetOutcome.MaybeSent(PRESS_KEY)
        log("widget command: $PRESS_KEY key=$key")
        val params = JSONObject().put("deviceId", selection.deviceId).put("key", key)
        var delivery = deliver(PRESS_KEY, params, tapId)
        if ((delivery as? Delivery.Failed)?.code == "device_not_found") {
            val found = try {
                discovered(selection.deviceId)
            } catch (error: Exception) {
                return WidgetOutcome.StatusUnreadable
            }
            if (found == null) return WidgetOutcome.StatusUnreadable
            if (!found) return WidgetOutcome.NotFound
            if (!mayCommand()) return WidgetOutcome.Expired
            delivery = deliver(PRESS_KEY, params, tapId)
        }
        return when (delivery) {
            is Delivery.Answered -> {
                val confirmation = delivery.result.getString("confirmation")
                log("widget command result: $PRESS_KEY -> sent, $confirmation")
                WidgetOutcome.KeySent(action, confirmation)
            }
            is Delivery.Failed -> delivery.outcome
        }
    }

    private sealed interface Delivery {
        data class Answered(val result: JSONObject) : Delivery
        data class Failed(val outcome: WidgetOutcome, val code: String?) : Delivery
    }

    /** One command line to the bridge, never retried here; what its answer proves. */
    private fun deliver(method: String, params: JSONObject, tapId: String): Delivery {
        val response = try {
            // The tap is this order's application request id: the bridge's acceptance trace
            // correlates every send of it (C7). Status reads are separate messages.
            JSONObject(bridge(request(method, params, requestId = tapId)))
        } catch (error: PythonUnavailable) {
            log("widget command result: $method -> not sent (control-TV unavailable)")
            return Delivery.Failed(WidgetOutcome.NotSent("backend_unavailable"), null)
        } catch (error: Exception) {
            log("widget command result: $method -> no answer, may have been sent")
            return Delivery.Failed(WidgetOutcome.MaybeSent(method), null)
        }
        if (!response.optBoolean("ok", false)) {
            val code = response.optJSONObject("error")?.optString("code").orEmpty()
            val sent = code !in NOT_SENT_CODES
            log("widget command result: $method -> ${if (sent) "may have been sent" else "not sent"} ($code)")
            val outcome = if (sent) WidgetOutcome.MaybeSent(method) else WidgetOutcome.NotSent(code)
            return Delivery.Failed(outcome, code)
        }
        return Delivery.Answered(response.getJSONObject("result").getJSONObject("result"))
    }

    /** The reported state a command was chosen from or checked against; no media title. */
    private fun reported(state: TvState): String {
        if (!state.connected) return "not connected"
        val volume = if (state.volumeFixed) "fixed" else state.volumeLevel?.let { "$it" } ?: "undetermined"
        return "playback=${state.playback ?: "none"} muted=${state.muted ?: "undetermined"} volume=$volume"
    }

    private sealed interface StatusRead {
        data class Read(val state: TvState) : StatusRead
        data class Failed(val outcome: WidgetOutcome) : StatusRead
    }

    private fun readStatus(deviceId: String): StatusRead {
        try {
            var response = JSONObject(bridge(request("get_status", JSONObject().put("deviceId", deviceId))))
            if (errorCode(response) == "device_not_found") {
                // Not known to this process yet (it may have just started): one read-only
                // discovery, then the selected id only. Another TV is never used instead.
                when (discovered(deviceId)) {
                    null -> return StatusRead.Failed(WidgetOutcome.StatusUnreadable)
                    false -> return StatusRead.Failed(WidgetOutcome.NotFound)
                    true -> {}
                }
                response = JSONObject(bridge(request("get_status", JSONObject().put("deviceId", deviceId))))
            }
            if (!response.optBoolean("ok", false)) {
                val outcome = if (errorCode(response) == "device_not_found") WidgetOutcome.NotFound else WidgetOutcome.StatusUnreadable
                return StatusRead.Failed(outcome)
            }
            return StatusRead.Read(parseState(response.getJSONObject("result").getJSONObject("status")))
        } catch (error: PythonUnavailable) {
            return StatusRead.Failed(WidgetOutcome.Unavailable)
        } catch (error: Exception) {
            return StatusRead.Failed(WidgetOutcome.StatusUnreadable)
        }
    }

    /** Whether one read-only discovery lists [deviceId]; null when the discovery failed. */
    private fun discovered(deviceId: String): Boolean? {
        val answer = JSONObject(bridge(request("discover_devices", JSONObject())))
        if (!answer.optBoolean("ok", false)) return null
        val devices = answer.getJSONObject("result").getJSONArray("devices")
        return deviceId in (0 until devices.length()).map { devices.getJSONObject(it).optString("id") }
    }

    private sealed interface Choice {
        data class Send(val method: String) : Choice
        data class Skip(val reason: String) : Choice
    }

    private fun choose(action: WidgetAction, state: TvState): Choice {
        if (!state.connected) return Choice.Skip(REASON_NOT_CONNECTED)
        return when (action) {
            WidgetAction.PLAY_PAUSE -> when (state.playback) {
                "playing", "buffering" ->
                    if (state.supportsPause == false) Choice.Skip(REASON_NO_PAUSE) else Choice.Send("pause")
                "paused" -> Choice.Send("play")
                null, "idle" -> Choice.Skip(REASON_NOTHING_PLAYING)
                else -> Choice.Skip(REASON_PLAYBACK_UNDETERMINED)
            }
            WidgetAction.STOP -> when (state.playback) {
                null, "idle" -> Choice.Skip(REASON_NOTHING_PLAYING)
                else -> Choice.Send("stop")
            }
            // REFRESH sends nothing; a remote key never gets here (see pressKey).
            else -> Choice.Skip("")
        }
    }

    private fun request(method: String, params: JSONObject, requestId: String? = null): String {
        val line = JSONObject().put("id", nextId++).put("method", method).put("params", params)
        requestId?.let { line.put("requestId", it) }
        return line.toString()
    }

    private fun errorCode(response: JSONObject): String? =
        if (response.optBoolean("ok", false)) null else response.optJSONObject("error")?.optString("code")

    companion object {
        /** A tap older than this when its job starts sends and reads nothing. */
        const val MAX_TAP_AGE_MILLIS = 60_000L

        /** The bridge method of every remote key. */
        const val PRESS_KEY = "press_remote_key"

        // Refused by the shared layer before anything was sent; every other failure of a
        // command may or may not have reached the TV.
        val NOT_SENT_CODES = setOf(
            "invalid_argument",
            "device_not_found",
            "ambiguous_target",
            "unsupported_operation",
            "unsupported_media",
            "discovery_failed",
            "pairing_required",
        )

        const val REASON_NOT_CONNECTED = "The TV isn't connected."
        const val REASON_NOTHING_PLAYING = "Nothing is playing."
        const val REASON_NO_PAUSE = "This media can't be paused."
        const val REASON_PLAYBACK_UNDETERMINED = "The TV didn't say what is playing."

        fun parseState(status: JSONObject): TvState {
            val receiver = status.optJSONObject("receiver")
            val media = status.optJSONObject("media")
            return TvState(
                connected = status.optString("connection") == "connected",
                playback = media?.optNullableString("playbackState"),
                title = media?.optNullableString("title"),
                supportsPause = media?.optNullableBoolean("supportsPause"),
                volumeLevel = receiver?.takeIf { !it.isNull("volumeLevel") }?.optDouble("volumeLevel")?.takeIf { !it.isNaN() },
                volumeFixed = receiver?.optNullableString("volumeControlType") == "fixed",
                muted = receiver?.optNullableBoolean("muted"),
            )
        }

        private fun JSONObject.optNullableString(key: String): String? =
            if (isNull(key)) null else optString(key).takeIf { it.isNotEmpty() }

        private fun JSONObject.optNullableBoolean(key: String): Boolean? =
            if (isNull(key)) null else (opt(key) as? Boolean)
    }
}
