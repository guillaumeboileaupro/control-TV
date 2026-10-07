package io.github.guillaumeboileaupro.controltv

import android.content.Intent
import io.github.guillaumeboileaupro.controltv.ControlTvWidget.WidgetClick
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.w3c.dom.Element
import java.io.File
import javax.xml.parsers.DocumentBuilderFactory

/**
 * Where each tap on the widget goes, checked against the app's real resource ids and the
 * real widget layout file: the TV name opens the widget's picker, ↻ is a Refresh tap, only the
 * logo opens the app, and no other view of the layout can take those taps.
 */
class WidgetRoutingTest {
    private val android = "http://schemas.android.com/apk/res/android"

    private fun click(id: Int): WidgetClick = ControlTvWidget.CLICKS.single { it.first == id }.second

    /** The resource name behind an R.id value, read from the generated R class. */
    private fun idName(value: Int): String =
        R.id::class.java.fields.single { it.type == Int::class.javaPrimitiveType && it.getInt(null) == value }.name

    private fun layoutName(value: Int): String =
        R.layout::class.java.fields.single { it.getInt(null) == value }.name

    private fun layout(): List<Element> {
        val file = File("src/main/res/layout/${layoutName(ControlTvWidget.LAYOUT)}.xml")
        val document = DocumentBuilderFactory.newInstance().apply { isNamespaceAware = true }
            .newDocumentBuilder().parse(file)
        val all = document.getElementsByTagName("*")
        return (0 until all.length).map { all.item(it) as Element }
    }

    private fun Element.id(): String? = getAttributeNS(android, "id").removePrefix("@+id/").ifEmpty { null }

    private val clicked: Set<String> get() = ControlTvWidget.CLICKS.map { idName(it.first) }.toSet()

    @Test
    fun theTvNameOpensTheWidgetsOwnPickerAndNotTheApp() {
        val open = click(R.id.widget_header) as WidgetClick.Open

        assertEquals(WidgetTvPickerActivity::class.java, open.activity)
        assertTrue(open.flags and Intent.FLAG_ACTIVITY_NEW_TASK != 0)
    }

    @Test
    fun refreshIsARefreshTapForTheTapReceiver() {
        assertEquals(WidgetClick.Tap(WidgetAction.REFRESH), click(R.id.widget_refresh))
    }

    @Test
    fun onlyTheLogoOpensTheMainApp() {
        val opensApp = ControlTvWidget.CLICKS.filter { (it.second as? WidgetClick.Open)?.activity == MainActivity::class.java }

        assertEquals(listOf(R.id.widget_logo), opensApp.map { it.first })
    }

    @Test
    fun everyActionHasExactlyOneButtonAndEveryClickedViewOneTarget() {
        val taps = ControlTvWidget.CLICKS.mapNotNull { (it.second as? WidgetClick.Tap)?.action }

        assertEquals(WidgetAction.entries.sorted(), taps.sorted())
        assertEquals(ControlTvWidget.CLICKS.size, ControlTvWidget.CLICKS.map { it.first }.toSet().size)
        // Distinct pending intents for the two activities (request code is part of their identity).
        val codes = ControlTvWidget.CLICKS.mapNotNull { (it.second as? WidgetClick.Open)?.requestCode }
        assertEquals(codes.size, codes.toSet().size)
    }

    @Test
    fun theWidgetIsDrawnWithItsOwnLayoutWhichHasEveryClickedView() {
        assertEquals("widget_control_tv", layoutName(ControlTvWidget.LAYOUT))
        val declared = layout().mapNotNull { it.id() }.toSet()

        assertTrue("$clicked not all in $declared", declared.containsAll(clicked))
    }

    @Test
    fun noParentChildOrOverlayCanTakeAClickedViewsTap() {
        val views = layout()
        for (view in views.filter { it.id() in clicked }) {
            // No clicked view sits inside another one (a parent would take the child's taps).
            var parent = view.parentNode
            while (parent is Element) {
                assertFalse("${view.id()} inside ${parent.id()}", parent.id() in clicked)
                parent = parent.parentNode
            }
        }
        for (view in views.filter { it.id() !in clicked }) {
            // Nothing else is clickable: no child of the TV name and no other view takes a tap.
            for (attribute in listOf("clickable", "longClickable", "onClick")) {
                assertEquals("${view.tagName} ${view.id()} $attribute", "", view.getAttributeNS(android, attribute))
            }
        }
        // Only linear layouts: no view is stacked over another, so none can cover a button.
        assertEquals(setOf("LinearLayout", "ImageView", "ImageButton", "TextView"), views.map { it.tagName }.toSet())
    }

    @Test
    fun anAppUpdateRedrawsTheWidgetAndNothingElseDoesSoHere() {
        assertTrue(ControlTvWidget.redrawsAfter(Intent.ACTION_MY_PACKAGE_REPLACED))
        assertFalse(ControlTvWidget.redrawsAfter("android.appwidget.action.APPWIDGET_UPDATE"))
        assertFalse(ControlTvWidget.redrawsAfter(WidgetTapReceiver.ACTION_TAP))
        assertFalse(ControlTvWidget.redrawsAfter(null))
    }
}
