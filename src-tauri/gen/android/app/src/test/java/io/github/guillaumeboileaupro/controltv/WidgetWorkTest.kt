package io.github.guillaumeboileaupro.controltv

import androidx.work.ExistingWorkPolicy
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * How a tap becomes WorkManager work. On the phone (dcb6588) taps reached the widget but no
 * job ever ran: ordinary work could be deferred indefinitely, and one name shared by every
 * tap (with KEEP) let a never-started job drop all later taps.
 */
class WidgetWorkTest {
    private val tap = TapSpec(WidgetAction.REFRESH, "4f9e1c2a-token", 1_000L)

    @Test
    fun aTapIsExpeditedWorkSoItStartsNow() {
        assertTrue(WidgetWork.request(tap).workSpec.expedited)
    }

    @Test
    fun eachTapHasItsOwnWorkNameSoAnUnstartedJobCannotBlockTheNextTap() {
        val first = WidgetWork.uniqueName("token-1")
        val second = WidgetWork.uniqueName("token-2")

        assertNotEquals(first, second)
        assertEquals(ExistingWorkPolicy.KEEP, WidgetWork.POLICY)
    }

    @Test
    fun theSameTapAlwaysMapsToTheSameWorkSoARedeliveryIsKeptOnce() {
        assertEquals(WidgetWork.uniqueName("token-1"), WidgetWork.uniqueName("token-1"))
    }

    @Test
    fun theWorkNameNeverCarriesTheToken() {
        val name = WidgetWork.uniqueName(tap.tapId)

        assertFalse(name.contains(tap.tapId))
        assertTrue(name.startsWith("control-tv-widget-tap-"))
    }

    @Test
    fun theJobGetsOnlyTheActionTheTapIdentityAndItsTime() {
        val data = WidgetWork.request(tap).workSpec.input

        assertEquals("REFRESH", data.getString(WidgetActionWorker.KEY_ACTION))
        assertEquals(tap.tapId, data.getString(WidgetActionWorker.KEY_TAP))
        assertEquals(1_000L, data.getLong(WidgetActionWorker.KEY_TAPPED_AT, 0L))
        assertEquals(3, data.keyValueMap.size)
    }
}
