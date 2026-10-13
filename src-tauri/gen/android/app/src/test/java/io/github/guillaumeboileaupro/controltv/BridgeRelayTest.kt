package io.github.guillaumeboileaupro.controltv

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Collections
import java.util.concurrent.CompletableFuture
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
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

    // --- The widget's non-queuing transaction --------------------------------------------

    @Test
    fun aTransactionRunsItsCallsOnTheWorkerAndReturnsItsValue() {
        val relay = BridgeRelay(handler = { line -> "answer to $line" })

        val result = relay.tryTransaction(5_000) { tx -> tx.call("status") + " / " + tx.call("pause") }

        assertEquals(TransactionResult.Done("answer to status / answer to pause"), result)
        relay.shutdown()
    }

    @Test
    fun aTransactionWhileTheWindowsRequestRunsIsTurnedDownAndNeverEntersPython() {
        val release = CountDownLatch(1)
        val started = CountDownLatch(1)
        val seen = Collections.synchronizedList(mutableListOf<String>())
        val relay = BridgeRelay(handler = { line ->
            seen.add(line)
            if (line == "window") { started.countDown(); release.await(5, TimeUnit.SECONDS) }
            line
        })
        relay.submit("window", onResponse = {}, onFailure = {})
        assertTrue(started.await(5, TimeUnit.SECONDS))

        val result = relay.tryTransaction(5_000) { tx -> tx.call("widget pause") }
        release.countDown()
        Thread.sleep(200)

        assertEquals(TransactionResult.Busy, result)
        assertEquals(listOf("window"), seen.toList())
        relay.shutdown()
    }

    @Test
    fun aTransactionWhileTheWindowHasARequestQueuedIsTurnedDownToo() {
        val release = CountDownLatch(1)
        val seen = Collections.synchronizedList(mutableListOf<String>())
        val relay = BridgeRelay(handler = { line -> seen.add(line); release.await(5, TimeUnit.SECONDS); line })
        relay.submit("window 1", onResponse = {}, onFailure = {})
        relay.submit("window 2", onResponse = {}, onFailure = {})

        val result = relay.tryTransaction(5_000) { tx -> tx.call("widget stop") }
        release.countDown()
        Thread.sleep(300)

        assertEquals(TransactionResult.Busy, result)
        assertEquals(listOf("window 1", "window 2"), seen.toList())
        relay.shutdown()
    }

    @Test
    fun aTransactionNotStartedInTimeIsWithdrawnAndNeverRunsLater() {
        // A worker thread that is late to pick the transaction up (held outside the relay).
        val worker = Executors.newSingleThreadExecutor()
        val hold = CountDownLatch(1)
        worker.execute { hold.await(5, TimeUnit.SECONDS) }
        val calls = AtomicInteger(0)
        val relay = BridgeRelay(handler = { line -> calls.incrementAndGet(); line }, worker = worker)

        val result = relay.tryTransaction(100) { tx -> tx.call("widget mute") }
        hold.countDown()
        Thread.sleep(300)

        assertEquals(TransactionResult.NotStarted, result)
        assertEquals(0, calls.get())
        // The worker is free again afterwards: a new transaction is admitted.
        assertEquals(TransactionResult.Done("x"), relay.tryTransaction(5_000) { tx -> tx.call("x") })
        relay.shutdown()
    }

    @Test
    fun theWindowsRequestsWaitUntilAnAdmittedTransactionEndsAndNeverInterleave() {
        val order = Collections.synchronizedList(mutableListOf<String>())
        val windowDone = CountDownLatch(1)
        val relay = BridgeRelay(handler = { line -> order.add(line); line })

        val result = relay.tryTransaction(5_000) { tx ->
            tx.call("widget status")
            relay.submit("window", onResponse = { windowDone.countDown() }, onFailure = { windowDone.countDown() })
            Thread.sleep(200)
            tx.call("widget pause")
        }

        assertTrue(windowDone.await(5, TimeUnit.SECONDS))
        assertEquals(TransactionResult.Done("widget pause"), result)
        assertEquals(listOf("widget status", "widget pause", "window"), order.toList())
        relay.shutdown()
    }

    @Test
    fun aTransactionTimedOutAfterItStartedCanNoLongerClaimACommand() {
        val wantedAtTheEnd = CompletableFuture<Boolean>()
        val relay = BridgeRelay(handler = { line -> line })

        val result = relay.tryTransaction(100) { tx ->
            Thread.sleep(400)
            wantedAtTheEnd.complete(tx.stillWanted)
        }

        assertEquals(TransactionResult.TimedOutAfterStart, result)
        assertEquals(false, wantedAtTheEnd.get(5, TimeUnit.SECONDS))
        relay.shutdown()
    }

    @Test
    fun aPythonThatCannotStartInsideATransactionStaysNotSent() {
        val relay = BridgeRelay(handler = EmbeddedHandler(load = { throw IllegalStateException("no Python") }))

        val error = runCatching { relay.tryTransaction(5_000) { tx -> tx.call("pause") } }.exceptionOrNull()

        assertTrue(error is PythonUnavailable)
        relay.shutdown()
    }

    @Test
    fun aTransactionThatHasAnsweredLeavesTheWorkerFreeForTheNextOne() {
        val relay = BridgeRelay(handler = { line -> line })

        repeat(200) { index ->
            val result = relay.tryTransaction(5_000) { tx -> tx.call("tap $index") }
            assertEquals("transaction $index", TransactionResult.Done("tap $index"), result)
        }
        relay.shutdown()
    }
}
