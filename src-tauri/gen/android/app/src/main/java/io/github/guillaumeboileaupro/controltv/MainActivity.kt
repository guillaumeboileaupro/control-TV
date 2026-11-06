package io.github.guillaumeboileaupro.controltv

import android.graphics.Color
import android.Manifest
import android.content.pm.PackageManager
import android.os.Bundle
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.SystemBarStyle
import androidx.activity.enableEdgeToEdge
import androidx.core.content.ContextCompat

class MainActivity : TauriActivity() {
  private var microphoneAnswer: ((Boolean) -> Unit)? = null
  private val microphonePermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) {
    granted -> microphoneAnswer?.also { microphoneAnswer = null }?.invoke(granted)
  }

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

  /** Ask only after an explicit Assistant action; no capture starts before this answers true. */
  fun requestMicrophonePermission(answer: (Boolean) -> Unit) {
    if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) ==
      PackageManager.PERMISSION_GRANTED) {
      answer(true)
      return
    }
    check(microphoneAnswer == null) { "a microphone permission request is already active" }
    microphoneAnswer = answer
    microphonePermission.launch(Manifest.permission.RECORD_AUDIO)
  }

  override fun onDestroy() {
    microphoneAnswer?.also { microphoneAnswer = null }?.invoke(false)
    super.onDestroy()
  }
}
