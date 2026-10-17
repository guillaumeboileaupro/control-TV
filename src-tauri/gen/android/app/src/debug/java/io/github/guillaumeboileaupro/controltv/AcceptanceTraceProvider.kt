package io.github.guillaumeboileaupro.controltv

import androidx.core.content.FileProvider

/** Debug builds only: shares the acceptance trace, and nothing else, when the owner exports it. */
class AcceptanceTraceProvider : FileProvider()
