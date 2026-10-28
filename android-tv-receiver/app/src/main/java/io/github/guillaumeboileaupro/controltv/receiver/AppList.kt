package io.github.guillaumeboileaupro.controltv.receiver

import org.json.JSONArray
import org.json.JSONObject
import java.net.URI
import java.net.URLDecoder

/**
 * A request for the TV's installed apps, read from the link control-TV opened through the TV's
 * paired remote (`controltv-receiver://apps?reply=…&token=…`): where to send them and the token
 * that proves the answer is for that request. No TV setting is needed for this.
 */
data class AppListRequest(val reply: String, val token: String)

object AppList {
    val TOKEN = Regex("[A-Za-z0-9_-]{16,64}")
    private const val MAX_APPS = 500

    /** The request in [link], or `null` for anything else: only a reply on the local network. */
    fun parse(link: String?): AppListRequest? {
        val params = linkParams(link, "apps") ?: return null
        val token = params["token"]?.takeIf { TOKEN.matches(it) } ?: return null
        val reply = params["reply"]?.takeIf { isLocalReply(it) } ?: return null
        return AppListRequest(reply, token)
    }

    /** The query of a `controltv-receiver://<host>?…` link, or `null` for any other link. */
    fun linkParams(link: String?, host: String): Map<String, String>? {
        val uri = runCatching { URI(link ?: return null) }.getOrNull() ?: return null
        if (uri.scheme != "controltv-receiver" || uri.host != host) return null
        val query = uri.rawQuery ?: return null
        return query.split("&").mapNotNull { part ->
            val pair = part.split("=", limit = 2)
            if (pair.size == 2) pair[0] to URLDecoder.decode(pair[1], "UTF-8") else null
        }.toMap()
    }

    /** `http://<private IPv4>:<port>/<path>` only: an answer never leaves the home network. */
    fun isLocalReply(reply: String, path: String = "/apps"): Boolean {
        val uri = runCatching { URI(reply) }.getOrNull() ?: return false
        if (uri.scheme != "http" || uri.path != path || uri.query != null || uri.port !in 1..65535) return false
        val parts = uri.host?.split(".")?.map { it.toIntOrNull() ?: return false } ?: return false
        if (parts.size != 4 || parts.any { it !in 0..255 }) return false
        val (a, b) = parts
        return a == 10 || (a == 172 && b in 16..31) || (a == 192 && b == 168)
    }

    /** The answer: each app once, by package, with the TV's label; never this receiver itself. */
    fun answer(token: String, apps: List<Pair<String, String>>, own: String): String {
        val list = JSONArray()
        apps.filter { it.first != own }.distinctBy { it.first }.sortedBy { it.second.lowercase() }
            .take(MAX_APPS)
            .forEach { (pkg, label) -> list.put(JSONObject().put("package", pkg).put("label", label)) }
        return JSONObject().put("token", token).put("apps", list).toString()
    }
}
