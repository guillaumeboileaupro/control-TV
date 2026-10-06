package io.github.guillaumeboileaupro.controltv

import org.junit.Assert.assertEquals
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference

class EmbeddedHandlerTest {
    @Test
    fun aPythonThatCannotStartIsUnavailable() {
        val handler = EmbeddedHandler(load = { throw IllegalStateException("Python did not start") })

        val error = runCatching { handler("""{"id":1,"method":"pause","params":{}}""") }.exceptionOrNull()

        assertTrue(error is PythonUnavailable)
        assertEquals(CODE_BACKEND_UNAVAILABLE, rejectionCode(error!!))
    }

    @Test
    fun aFailedImportOfTheEmbeddedModuleIsUnavailableNotATransportFailure() {
        val importError = RuntimeException("ModuleNotFoundError: No module named 'control_tv'")
        // Python started; getModule("control_tv.embedded") is what fails.
        val handler = EmbeddedHandler(load = { throw importError })

        val error = runCatching { handler("line") }.exceptionOrNull()

        assertTrue(error is PythonUnavailable)
        assertSame(importError, error!!.cause)
        assertEquals(CODE_BACKEND_UNAVAILABLE, rejectionCode(error))
    }

    @Test
    fun aFailureInsideHandleStaysAmbiguousAndHandleRunsOnce() {
        val calls = AtomicInteger(0)
        val failure = RuntimeException("raised after the request reached the control layer")
        val handler = EmbeddedHandler(load = { { _: String -> calls.incrementAndGet(); throw failure } })

        val error = runCatching { handler("line") }.exceptionOrNull()

        assertSame(failure, error)
        assertEquals(CODE_BRIDGE_TRANSPORT, rejectionCode(error!!))
        assertEquals(1, calls.get())
    }

    @Test
    fun aLoadedBridgeHandlesTheLineOnceAndReturnsItsResponse() {
        val seen = mutableListOf<String>()
        val handler = EmbeddedHandler(load = { { line: String -> seen.add(line); "response" } })

        assertEquals("response", handler("request"))
        assertEquals(listOf("request"), seen)
    }

    @Test
    fun throughTheRelayAnUnavailableBridgeIsReportedOnceAsNotSent() {
        val failures = AtomicInteger(0)
        val code = AtomicReference<String>()
        val done = CountDownLatch(1)
        val relay = BridgeRelay(handler = EmbeddedHandler(load = { throw IllegalStateException("no module") }))

        relay.submit("line", onResponse = { done.countDown() }, onFailure = { error ->
            failures.incrementAndGet()
            code.set(rejectionCode(error))
            done.countDown()
        })

        assertTrue(done.await(5, TimeUnit.SECONDS))
        Thread.sleep(100)
        assertEquals(1, failures.get())
        assertEquals(CODE_BACKEND_UNAVAILABLE, code.get())
        relay.shutdown()
    }
}
