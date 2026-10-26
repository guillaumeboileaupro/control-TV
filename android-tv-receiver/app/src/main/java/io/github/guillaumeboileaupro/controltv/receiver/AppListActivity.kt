package io.github.guillaumeboileaupro.controltv.receiver

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import java.net.HttpURLConnection
import java.net.URL

/**
 * Opened by control-TV through the TV's remote, with no visible screen: reads the TV's
 * launcher apps (package and the TV's own label), closes at once, then sends them to the
 * address in the link, leaving the TV as it was. A link that is not a valid app-list request does
 * nothing.
 */
class AppListActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val request = AppList.parse(intent?.dataString)
        if (request == null) {
            finish()
            return
        }
        val body = AppList.answer(request.token, launcherApps(), packageName)
        // Closed at once, before the network: the app in front (a video) is covered only for
        // the time it takes to read the list, never while the answer travels.
        finish()
        Thread {
            try {
                send(request.reply, body)
            } catch (ignored: Exception) {
                // control-TV stops waiting by itself; nothing to show on the TV.
            }
        }.start()
    }

    private fun launcherApps(): List<Pair<String, String>> {
        val categories = listOf(Intent.CATEGORY_LEANBACK_LAUNCHER, Intent.CATEGORY_LAUNCHER)
        return categories.flatMap { category ->
            packageManager.queryIntentActivities(Intent(Intent.ACTION_MAIN).addCategory(category), 0)
        }.map { it.activityInfo.packageName to it.loadLabel(packageManager).toString() }
    }

    private fun send(reply: String, body: String) {
        val connection = URL(reply).openConnection() as HttpURLConnection
        try {
            connection.requestMethod = "POST"
            connection.connectTimeout = 3_000
            connection.readTimeout = 3_000
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json")
            connection.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            connection.responseCode
        } finally {
            connection.disconnect()
        }
    }
}
