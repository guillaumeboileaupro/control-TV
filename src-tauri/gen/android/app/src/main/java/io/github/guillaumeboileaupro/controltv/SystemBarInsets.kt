package io.github.guillaumeboileaupro.controltv

import android.view.View
import androidx.core.graphics.Insets
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat

/**
 * The padding that keeps the app's content clear of the status bar, the navigation bar and a
 * display cutout: on each side, the larger of the system bars and the cutout, as the system
 * reports them for this window (never a fixed size).
 */
fun safeContentPadding(systemBars: Insets, displayCutout: Insets): Insets =
    Insets.max(systemBars, displayCutout)

/**
 * Lays the WebView out inside the window's safe area. The app draws edge to edge (enforced
 * from Android 15 for this target SDK), so without this the page starts under the status
 * bar. [content] is the activity's content container, which holds the WebView; the insets
 * are applied there and consumed, so the page itself sees no inset and no CSS
 * `env(safe-area-inset-*)` padding can be added twice. They are reapplied whenever they
 * change (rotation, a cutout, a bar shown or hidden).
 */
fun keepContentClearOfSystemBars(content: View) {
    ViewCompat.setOnApplyWindowInsetsListener(content) { view, insets ->
        val padding = safeContentPadding(
            insets.getInsets(WindowInsetsCompat.Type.systemBars()),
            insets.getInsets(WindowInsetsCompat.Type.displayCutout()),
        )
        view.setPadding(padding.left, padding.top, padding.right, padding.bottom)
        WindowInsetsCompat.CONSUMED
    }
}
