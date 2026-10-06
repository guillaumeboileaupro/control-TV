package io.github.guillaumeboileaupro.controltv

import androidx.core.graphics.Insets
import org.junit.Assert.assertEquals
import org.junit.Test

class SystemBarInsetsTest {
    @Test
    fun theContentIsPaddedByTheReportedStatusAndNavigationBars() {
        val bars = Insets.of(0, 63, 0, 42)

        assertEquals(bars, safeContentPadding(bars, Insets.NONE))
    }

    @Test
    fun aCutoutLargerThanTheStatusBarWinsOnItsOwnSideOnly() {
        val bars = Insets.of(0, 63, 0, 42)
        val cutout = Insets.of(0, 98, 0, 0)

        assertEquals(Insets.of(0, 98, 0, 42), safeContentPadding(bars, cutout))
    }

    @Test
    fun aSideCutoutInLandscapePadsThatSide() {
        val bars = Insets.of(0, 24, 48, 0)
        val cutout = Insets.of(80, 0, 0, 0)

        assertEquals(Insets.of(80, 24, 48, 0), safeContentPadding(bars, cutout))
    }

    @Test
    fun noReportedInsetMeansNoPadding() {
        assertEquals(Insets.NONE, safeContentPadding(Insets.NONE, Insets.NONE))
    }
}
