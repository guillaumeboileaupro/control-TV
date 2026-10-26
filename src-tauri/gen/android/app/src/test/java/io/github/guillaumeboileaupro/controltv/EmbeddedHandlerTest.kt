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

/** What each request leaves in the log (issue #112: measure the phone's slowness). */
class EmbeddedHandlerLogTest {
    private val logs = mutableListOf<String>()
    private var now = 0L

    private fun handler(answer: (String) -> String) = EmbeddedHandler(
        load = { { line -> now += 1_250_000_000L; answer(line) } },
        log = { logs += it },
        clock = { now },
    )

    @Test
    fun eachRequestLogsItsMethodItsDurationAndHowItEnded() {
        handler { """{"id":1,"ok":true,"result":{}}""" }(
            """{"id":1,"method":"press_remote_key","params":{"deviceId":"tv-1","key":"up"}}""",
        )
        handler { """{"id":2,"ok":false,"error":{"code":"pairing_required","message":"10.0.0.5"}}""" }(
            """{"id":2,"method":"list_tv_apps","params":{"deviceId":"tv-1"}}""",
        )

        assertEquals(
            listOf("bridge press_remote_key: 1250 ms, ok", "bridge list_tv_apps: 1250 ms, pairing_required"),
            logs,
        )
    }

    @Test
    fun aFailureIsLoggedAndPassedOnAndNothingPrivateIsLogged() {
        val failing = handler { throw IllegalStateException("tv-1 at 10.0.0.5") }

        val error = runCatching { failing("""{"id":3,"method":"get_status","params":{"deviceId":"tv-1"}}""") }
            .exceptionOrNull()
        handler { "not json" }("""{"method":"Living room 10.0.0.5"}""")

        assertTrue(error is IllegalStateException)
        assertEquals(listOf("bridge get_status: 1250 ms, failed", "bridge ?: 1250 ms, unreadable"), logs)
        assertTrue(logs.none { "tv-1" in it || "10.0.0.5" in it || "Living" in it })
    }

    @Test
    fun aPythonThatCannotStartIsLoggedAsUnavailable() {
        val unavailable = EmbeddedHandler(load = { error("no Python") }, log = { logs += it }, clock = { now })

        runCatching { unavailable("""{"id":4,"method":"discover_devices","params":{}}""") }

        assertEquals(listOf("bridge discover_devices: 0 ms, $CODE_BACKEND_UNAVAILABLE"), logs)
    }
}
