package io.github.guillaumeboileaupro.controltv.receiver

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import java.net.HttpURLConnection
import java.net.URL

/**
 * Opened by control-TV through the TV's remote, with no visible screen: starts the requested
 * app once through its own launch activity (the one the TV's home screen uses), answers with
 * what actually happened (started, not installed, refused), and closes. A link that is not a
 * valid launch request does nothing.
 */
class AppLaunchActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val request = AppLaunch.parse(intent?.dataString)
        if (request == null) {
            finish()
            return
        }
        val result = start(request.pkg)
        val body = AppLaunch.answer(request, result)
        // Closed now: the started app is in front; the answer is sent in the background.
        finish()
        Thread {
            try {
                send(request.reply, body)
            } catch (ignored: Exception) {
                // control-TV stops waiting by itself and says it does not know.
            }
        }.start()
    }

    private fun start(pkg: String): LaunchResult {
        val launch = packageManager.getLeanbackLaunchIntentForPackage(pkg)
            ?: packageManager.getLaunchIntentForPackage(pkg)
            ?: return LaunchResult.NOT_INSTALLED
        return try {
            startActivity(launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            LaunchResult.STARTED
        } catch (error: Exception) {
            LaunchResult.REFUSED
        }
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
