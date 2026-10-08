package io.github.guillaumeboileaupro.controltv

import org.json.JSONArray
import org.json.JSONObject

/** In-memory [WidgetStore]: what the app's preferences would hold. */
class MemoryWidgetStore : WidgetStore {
    var selected: Selection? = null
    var knownNames: Map<String, String> = emptyMap()
    var lastView: WidgetView? = null
    var claimed: String? = null
    var armed: String? = null

    override fun selection() = selected
    override fun saveSelection(selection: Selection) { selected = selection }
    override fun names() = knownNames
    override fun saveNames(names: Map<String, String>) { knownNames = names }
    override fun view() = lastView
    override fun saveView(view: WidgetView) { lastView = view }
    /** The stored record of claimed taps ([ClaimedTaps]); null while no tap was claimed. */
    @Synchronized
    override fun updateClaimedTaps(update: (String?) -> String?): Boolean {
        claimed = update(claimed) ?: return false
        return true
    }
    override fun armedToken(newToken: () -> String): String = armed ?: newToken().also { armed = it }
    @Synchronized
    override fun consumeToken(token: String, next: String): Boolean {
        if (armed != token) return false
        armed = next
        return true
    }
}

/** A scripted shared layer: answers bridge request lines by method and records each one. */
class FakeBridge(private val answer: (method: String, params: JSONObject) -> String) : (String) -> String {
    val requests = mutableListOf<Pair<String, JSONObject>>()

    val methods: List<String> get() = requests.map { it.first }

    val commands: List<Pair<String, JSONObject>>
        get() = requests.filter { it.first !in setOf("get_status", "discover_devices") }

    override fun invoke(line: String): String {
        val request = JSONObject(line)
        val method = request.getString("method")
        val params = request.getJSONObject("params")
        requests += method to params
        return answer(method, params)
    }
}

const val TV = "tv-1"
const val OTHER_TV = "tv-2"
val SELECTED = Selection(TV, "Living room")

fun status(
    playback: String? = "playing",
    title: String? = "A film",
    supportsPause: Boolean? = null,
    level: Double? = 0.47,
    muted: Boolean? = false,
    controlType: String? = "attenuation",
    connection: String = "connected",
    deviceId: String = TV,
): JSONObject {
    val media = if (playback == null) JSONObject.NULL else JSONObject()
        .put("playbackState", playback)
        .put("title", title ?: JSONObject.NULL)
        .put("supportsPause", supportsPause ?: JSONObject.NULL)
    val receiver = JSONObject()
        .put("volumeLevel", level ?: JSONObject.NULL)
        .put("muted", muted ?: JSONObject.NULL)
        .put("volumeControlType", controlType ?: JSONObject.NULL)
    return JSONObject().put("deviceId", deviceId).put("connection", connection)
        .put("receiver", receiver).put("media", media)
}

fun statusAnswer(state: JSONObject = status()): String =
    JSONObject().put("id", 1).put("ok", true).put("result", JSONObject().put("status", state)).toString()

fun devicesAnswer(vararg ids: String): String =
    JSONObject().put("id", 1).put("ok", true).put(
        "result",
        JSONObject().put(
            "devices",
            JSONArray(ids.map { JSONObject().put("id", it).put("friendlyName", "TV $it") }),
        ),
    ).toString()

fun commandAnswer(command: String, confirmation: String, observed: JSONObject? = null): String =
    JSONObject().put("id", 1).put("ok", true).put(
        "result",
        JSONObject().put(
            "result",
            JSONObject().put("command", command).put("deviceId", TV).put("confirmation", confirmation)
                .put("detail", JSONObject.NULL).put("observed", observed ?: JSONObject.NULL),
        ),
    ).toString()

fun errorAnswer(code: String): String =
    JSONObject().put("id", 1).put("ok", false)
        .put("error", JSONObject().put("code", code).put("message", "detail")).toString()
