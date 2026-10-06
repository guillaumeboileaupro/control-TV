package io.github.guillaumeboileaupro.controltv

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Collections
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

class BridgeRelayTest {
    @Test
    fun relaysTheRequestLineAndTheResponseLineUnchanged() {
        val request = """{"id":1,"method":"ping","params":{}}"""
        val response = """{"id": 1, "ok": true, "result": {"status": "ready"}}"""
        val seen = mutableListOf<String>()
        val done = CountDownLatch(1)
        var answer: String? = null
        val relay = BridgeRelay(handler = { line -> seen.add(line); response })

        relay.submit(request, onResponse = { answer = it; done.countDown() }, onFailure = { done.countDown() })

        assertTrue(done.await(5, TimeUnit.SECONDS))
        assertEquals(listOf(request), seen)
        assertEquals(response, answer)
        relay.shutdown()
    }

    @Test
    fun requestsRunOneAtATimeInArrivalOrder() {
        val running = AtomicInteger(0)
        val maxRunning = AtomicInteger(0)
        val order = Collections.synchronizedList(mutableListOf<String>())
        val done = CountDownLatch(3)
        val relay = BridgeRelay(handler = { line ->
            maxRunning.accumulateAndGet(running.incrementAndGet(), ::maxOf)
            Thread.sleep(50)
            order.add(line)
            running.decrementAndGet()
            line
        })

        for (line in listOf("a", "b", "c")) {
            relay.submit(line, onResponse = { done.countDown() }, onFailure = { done.countDown() })
        }

        assertTrue(done.await(5, TimeUnit.SECONDS))
        assertEquals(listOf("a", "b", "c"), order)
        assertEquals(1, maxRunning.get())
        relay.shutdown()
    }

    @Test
    fun aFailureIsReportedOnceAndTheRequestIsNeverRetried() {
        val calls = AtomicInteger(0)
        val failures = AtomicInteger(0)
        val responses = AtomicInteger(0)
        val done = CountDownLatch(1)
        val relay = BridgeRelay(handler = { calls.incrementAndGet(); throw IllegalStateException("boom") })

        relay.submit("x", onResponse = { responses.incrementAndGet(); done.countDown() }, onFailure = {
            failures.incrementAndGet()
            done.countDown()
        })

        assertTrue(done.await(5, TimeUnit.SECONDS))
        Thread.sleep(100)
        assertEquals(1, calls.get())
        assertEquals(1, failures.get())
        assertEquals(0, responses.get())
        relay.shutdown()
    }

    @Test
    fun aStartupTaskRunsOnceOnTheWorkerBeforeLaterRequests() {
        val order = Collections.synchronizedList(mutableListOf<String>())
        val done = CountDownLatch(1)
        val relay = BridgeRelay(handler = { line -> order.add(line); line })

        relay.execute(task = { order.add("diagnostic") }, onFailure = {})
        relay.submit("ping", onResponse = { done.countDown() }, onFailure = { done.countDown() })

        assertTrue(done.await(5, TimeUnit.SECONDS))
        assertEquals(listOf("diagnostic", "ping"), order)
        relay.shutdown()
    }

    @Test
    fun aFailingStartupTaskIsReportedOnceAndRequestsStillRun() {
        val failures = AtomicInteger(0)
        val handled = AtomicInteger(0)
        val done = CountDownLatch(1)
        val relay = BridgeRelay(handler = { line -> handled.incrementAndGet(); line })

        relay.execute(task = { throw IllegalStateException("no Python") }, onFailure = { failures.incrementAndGet() })
        relay.submit("ping", onResponse = { done.countDown() }, onFailure = { done.countDown() })

        assertTrue(done.await(5, TimeUnit.SECONDS))
        assertEquals(1, failures.get())
        assertEquals(1, handled.get())
        relay.shutdown()
    }
}
