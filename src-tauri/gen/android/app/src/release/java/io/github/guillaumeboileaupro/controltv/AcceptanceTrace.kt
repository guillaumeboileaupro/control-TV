package io.github.guillaumeboileaupro.controltv

import android.content.Context
import com.chaquo.python.PyObject

/**
 * Release builds have no acceptance mode: nothing is traced, and nothing in the app can switch
 * a trace on (the debug build's version lives in src/debug).
 */
internal object AcceptanceTrace {
    @Suppress("UNUSED_PARAMETER")
    fun configure(context: Context, embedded: PyObject) = Unit
}
