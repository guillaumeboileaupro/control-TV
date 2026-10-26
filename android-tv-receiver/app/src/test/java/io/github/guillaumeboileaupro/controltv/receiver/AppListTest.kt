package io.github.guillaumeboileaupro.controltv.receiver

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class AppListTest {
    private val token = "a".repeat(32)
    private val link = "controltv-receiver://apps?reply=http://192.168.1.20:40123/apps&token=$token"

    @Test
    fun theLinkControlTvOpensIsARequestToAnswerOnTheLocalNetwork() {
        assertEquals(AppListRequest("http://192.168.1.20:40123/apps", token), AppList.parse(link))
        assertEquals(
            "http://10.0.0.5:8080/apps",
            AppList.parse(link.replace("192.168.1.20:40123", "10.0.0.5:8080"))?.reply,
        )
    }

    @Test
    fun anyOtherLinkIsNotARequest() {
        for (other in listOf(
            null,
            "",
            "https://example.com/apps",
            link.replace("controltv-receiver", "other"),
            link.replace("://apps", "://else"),
            link.replace(token, "short"),
            link.replace("192.168.1.20", "8.8.8.8"),
            link.replace("http://", "https://"),
            link.replace("/apps&", "/elsewhere&"),
            "controltv-receiver://apps?token=$token",
        )) {
            assertNull(other, AppList.parse(other))
        }
    }

    @Test
    fun onlyPrivateAddressesAreLocal() {
        assertTrue(AppList.isLocalReply("http://172.16.0.1:1/apps"))
        assertFalse(AppList.isLocalReply("http://172.32.0.1:1/apps"))
        assertFalse(AppList.isLocalReply("http://192.168.1.300:1/apps"))
        assertFalse(AppList.isLocalReply("http://192.168.1.2/apps"))
        assertFalse(AppList.isLocalReply("http://192.168.1.2:1/apps?x=1"))
    }

    @Test
    fun theAnswerListsEachAppOnceWithItsLabelAndNeverTheReceiver() {
        val apps = listOf(
            "com.netflix.ninja" to "Netflix",
            "fr.free.oqee" to "OQEE by Free",
            "com.netflix.ninja" to "Netflix",
            "io.github.guillaumeboileaupro.controltv.receiver" to "ControlTV Récepteur",
        )

        val answer = JSONObject(AppList.answer(token, apps, "io.github.guillaumeboileaupro.controltv.receiver"))

        assertEquals(token, answer.getString("token"))
        val listed = answer.getJSONArray("apps")
        assertEquals(2, listed.length())
        assertEquals("com.netflix.ninja", listed.getJSONObject(0).getString("package"))
        assertEquals("OQEE by Free", listed.getJSONObject(1).getString("label"))
    }
}
