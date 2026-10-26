package io.github.guillaumeboileaupro.controltv.receiver

import org.json.JSONObject

/**
 * A request to open one app on the TV, read from the link control-TV opened through the TV's
 * paired remote (`controltv-receiver://launch?package=…&reply=…&token=…`). The TV's remote
 * cannot open an app by its package on every Google TV (`market://launch` is refused by some),
 * but it opens this receiver's links; the receiver then starts the app's own launch activity.
 */
data class AppLaunchRequest(val pkg: String, val reply: String, val token: String)

/** What happened, as the receiver saw it; only `STARTED` means the TV started the app. */
enum class LaunchResult(val wire: String) {
    STARTED("started"),
    NOT_INSTALLED("not_installed"),
    REFUSED("refused"),
}

object AppLaunch {
    private val PACKAGE = Regex("[A-Za-z][A-Za-z0-9_]*(\\.[A-Za-z0-9_]+)+")

    /** The request in [link], or `null` for anything else: a package name and a local reply. */
    fun parse(link: String?): AppLaunchRequest? {
        val params = AppList.linkParams(link, "launch") ?: return null
        val pkg = params["package"]?.takeIf { it.length <= 255 && PACKAGE.matches(it) } ?: return null
        val token = params["token"]?.takeIf { AppList.TOKEN.matches(it) } ?: return null
        val reply = params["reply"]?.takeIf { AppList.isLocalReply(it, "/launch") } ?: return null
        return AppLaunchRequest(pkg, reply, token)
    }

    fun answer(request: AppLaunchRequest, result: LaunchResult): String =
        JSONObject().put("token", request.token).put("package", request.pkg).put("result", result.wire).toString()
}
