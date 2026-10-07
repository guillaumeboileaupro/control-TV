package io.github.guillaumeboileaupro.controltv

import org.json.JSONObject
import kotlin.math.roundToInt

/** What a widget button asks for. */
enum class WidgetAction { PLAY_PAUSE, STOP, MUTE, VOLUME_DOWN, VOLUME_UP, REFRESH }

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
    object NotFound : WidgetOutcome
    object Unavailable : WidgetOutcome
    object StatusUnreadable : WidgetOutcome
    data class Status(val state: TvState) : WidgetOutcome
    data class NoCommand(val reason: String, val state: TvState) : WidgetOutcome
    data class Sent(val command: String, val confirmation: String, val observed: TvState?) : WidgetOutcome
    data class NotSent(val code: String) : WidgetOutcome
    data class MaybeSent(val command: String) : WidgetOutcome
}

/**
 * Durable "this tap's command was already handed to the bridge". The widget's background job
 * can be run again by Android after a process death; a tap claimed once never sends again.
 */
class TapGuard(private val store: WidgetStore) {
    fun claim(tapId: String): Boolean {
        if (store.claimedTap() == tapId) return false
        store.saveClaimedTap(tapId)
        return true
    }
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
 */
class WidgetActionRunner(
    private val bridge: (String) -> String,
    private val guard: TapGuard,
) {
    private var nextId = 1

    /**
     * [tapAgeMillis]: how long ago the tap happened. A job that starts too late (Android can
     * hold a queued job back, and reschedules it after a reboot) does nothing at all, so a
     * command never arrives long after the tap that asked for it.
     */
    fun run(action: WidgetAction, selection: Selection?, tapId: String, tapAgeMillis: Long = 0): WidgetOutcome {
        if (selection == null) return WidgetOutcome.NoSelection
        if (tapAgeMillis !in 0..MAX_TAP_AGE_MILLIS) return WidgetOutcome.Expired
        val state = when (val read = readStatus(selection.deviceId)) {
            is StatusRead.Read -> read.state
            is StatusRead.Failed -> return read.outcome
        }
        if (action == WidgetAction.REFRESH) return WidgetOutcome.Status(state)
        val command = when (val choice = choose(action, state)) {
            is Choice.Send -> choice
            is Choice.Skip -> return WidgetOutcome.NoCommand(choice.reason, state)
        }
        // Claimed durably before the send: a re-run of this tap never sends a second time.
        if (!guard.claim(tapId)) return WidgetOutcome.MaybeSent(command.method)
        val params = JSONObject(command.params).put("deviceId", selection.deviceId)
        val response = try {
            JSONObject(bridge(request(command.method, params)))
        } catch (error: PythonUnavailable) {
            return WidgetOutcome.NotSent("backend_unavailable")
        } catch (error: Exception) {
            return WidgetOutcome.MaybeSent(command.method)
        }
        if (!response.optBoolean("ok", false)) {
            val code = response.optJSONObject("error")?.optString("code").orEmpty()
            return if (code in NOT_SENT_CODES) WidgetOutcome.NotSent(code) else WidgetOutcome.MaybeSent(command.method)
        }
        val result = response.getJSONObject("result").getJSONObject("result")
        val observed = result.optJSONObject("observed")?.let(::parseState)
        return WidgetOutcome.Sent(command.method, result.getString("confirmation"), observed)
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
                val discovered = JSONObject(bridge(request("discover_devices", JSONObject())))
                if (!discovered.optBoolean("ok", false)) return StatusRead.Failed(WidgetOutcome.StatusUnreadable)
                val devices = discovered.getJSONObject("result").getJSONArray("devices")
                val found = (0 until devices.length()).any { devices.getJSONObject(it).optString("id") == deviceId }
                if (!found) return StatusRead.Failed(WidgetOutcome.NotFound)
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

    private sealed interface Choice {
        data class Send(val method: String, val params: Map<String, Any> = emptyMap()) : Choice
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
                else -> Choice.Skip(REASON_PLAYBACK_UNKNOWN)
            }
            WidgetAction.STOP -> when (state.playback) {
                null, "idle" -> Choice.Skip(REASON_NOTHING_PLAYING)
                else -> Choice.Send("stop")
            }
            // set_muted is an absolute state: the opposite of the one the TV reported.
            WidgetAction.MUTE -> state.muted?.let { Choice.Send("set_muted", mapOf("muted" to !it)) }
                ?: Choice.Skip(REASON_MUTE_UNKNOWN)
            WidgetAction.VOLUME_DOWN -> volume(state, -VOLUME_STEP)
            WidgetAction.VOLUME_UP -> volume(state, +VOLUME_STEP)
            WidgetAction.REFRESH -> Choice.Skip("")
        }
    }

    private fun volume(state: TvState, step: Double): Choice {
        if (state.volumeFixed) return Choice.Skip(REASON_VOLUME_FIXED)
        val level = state.volumeLevel ?: return Choice.Skip(REASON_VOLUME_UNKNOWN)
        // From the level the TV reported, never from a guess; one step is the window's own
        // limit for one raise (10 points), applied the same way down.
        val target = ((level + step).coerceIn(0.0, 1.0) * 100).roundToInt() / 100.0
        if (target == level) return Choice.Skip(if (step > 0) REASON_VOLUME_MAX else REASON_VOLUME_MIN)
        return Choice.Send("set_volume", mapOf("level" to target))
    }

    private fun request(method: String, params: JSONObject): String =
        JSONObject().put("id", nextId++).put("method", method).put("params", params).toString()

    private fun errorCode(response: JSONObject): String? =
        if (response.optBoolean("ok", false)) null else response.optJSONObject("error")?.optString("code")

    companion object {
        /** A tap older than this when its job starts sends and reads nothing. */
        const val MAX_TAP_AGE_MILLIS = 60_000L

        /** One volume tap: the window's raise limit for one gesture, 10 points. */
        const val VOLUME_STEP = 0.10

        // Refused by the shared layer before anything was sent; every other failure of a
        // command may or may not have reached the TV.
        val NOT_SENT_CODES = setOf(
            "invalid_argument",
            "device_not_found",
            "ambiguous_target",
            "unsupported_operation",
            "unsupported_media",
            "discovery_failed",
        )

        const val REASON_NOT_CONNECTED = "The TV isn't connected."
        const val REASON_NOTHING_PLAYING = "Nothing is playing."
        const val REASON_NO_PAUSE = "This media can't be paused."
        const val REASON_PLAYBACK_UNKNOWN = "The TV didn't say what is playing."
        const val REASON_MUTE_UNKNOWN = "The TV didn't report its mute state."
        const val REASON_VOLUME_UNKNOWN = "The TV didn't report its volume."
        const val REASON_VOLUME_FIXED = "This TV's volume can't be changed here."
        const val REASON_VOLUME_MAX = "Already at full volume."
        const val REASON_VOLUME_MIN = "Already at the lowest volume."

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
