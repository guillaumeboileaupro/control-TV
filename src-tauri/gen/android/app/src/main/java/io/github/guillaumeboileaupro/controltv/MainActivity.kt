package io.github.guillaumeboileaupro.controltv

import android.graphics.Color
import android.os.Bundle
import androidx.activity.SystemBarStyle
import androidx.activity.enableEdgeToEdge

class MainActivity : TauriActivity() {
  override fun onCreate(savedInstanceState: Bundle?) {
    // The UI is light only (white background): transparent system bars with dark icons, over
    // the window background, which matches the page (themes.xml).
    enableEdgeToEdge(
      statusBarStyle = SystemBarStyle.light(Color.TRANSPARENT, Color.TRANSPARENT),
      navigationBarStyle = SystemBarStyle.light(Color.TRANSPARENT, Color.TRANSPARENT),
    )
    super.onCreate(savedInstanceState)
    keepContentClearOfSystemBars(findViewById(android.R.id.content))
    // Should a launcher withhold the update broadcast, opening the app still redraws a widget
    // left over from the previous version (see ControlTvWidget.redrawsAfter).
    ControlTvWidget.renderAll(applicationContext)
  }
}
