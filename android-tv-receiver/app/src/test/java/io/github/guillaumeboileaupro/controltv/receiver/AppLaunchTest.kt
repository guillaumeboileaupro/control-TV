package io.github.guillaumeboileaupro.controltv.receiver

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class AppLaunchTest {
    private val token = "b".repeat(32)
    private val link =
        "controltv-receiver://launch?package=tv.arte.plus7&reply=http://192.168.1.20:40123/launch&token=$token"

    @Test
    fun theLinkControlTvOpensIsOneAppToStartWithALocalReply() {
        assertEquals(
            AppLaunchRequest("tv.arte.plus7", "http://192.168.1.20:40123/launch", token),
            AppLaunch.parse(link),
        )
    }

    @Test
    fun anyOtherLinkStartsNothing() {
        for (other in listOf(
            null,
            "market://launch?id=tv.arte.plus7",
            link.replace("://launch", "://apps"),
            link.replace("tv.arte.plus7", "not a package"),
            link.replace("tv.arte.plus7", "single"),
            link.replace("/launch&", "/apps&"),
            link.replace("192.168.1.20", "8.8.8.8"),
            link.replace(token, "short"),
            "controltv-receiver://launch?reply=http://192.168.1.20:40123/launch&token=$token",
        )) {
            assertNull(other, AppLaunch.parse(other))
        }
    }

    @Test
    fun theAnswerSaysWhatHappenedForThatRequest() {
        val request = AppLaunch.parse(link)!!
        for (result in LaunchResult.entries) {
            val answer = JSONObject(AppLaunch.answer(request, result))
            assertEquals(token, answer.getString("token"))
            assertEquals("tv.arte.plus7", answer.getString("package"))
            assertEquals(result.wire, answer.getString("result"))
        }
        assertEquals(listOf("started", "not_installed", "refused"), LaunchResult.entries.map { it.wire })
    }

    @Test
    fun theAppListLinkStillNeedsItsOwnPath() {
        assertNull(AppList.parse(link.replace("://launch", "://apps")))
    }
}
