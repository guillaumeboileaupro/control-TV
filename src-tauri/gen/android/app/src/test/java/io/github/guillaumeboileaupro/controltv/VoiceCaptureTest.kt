package io.github.guillaumeboileaupro.controltv

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.Executor

class VoiceCaptureTest {
    private class FakeMicrophone(
        private val reads: MutableList<Int>,
        private val failOnStart: Boolean = false,
        private val onRead: () -> Unit = {},
    ) : Microphone {
        var started = 0
        var stopped = 0

        override fun start() {
            started++
            if (failOnStart) throw IllegalStateException("busy")
        }

        override fun read(buffer: ByteArray): Int {
            onRead()
            if (reads.isEmpty()) return buffer.size
            val next = reads.removeAt(0)
            if (next > 0) buffer.fill(1, 0, next)
            return next
        }

        override fun stop() {
            stopped++
        }
    }

    private val inline = Executor { it.run() }

    private fun capture(
        microphone: FakeMicrophone,
        permitted: Boolean = true,
        chunks: MutableList<ByteArray> = mutableListOf(),
        ends: MutableList<VoiceEnd> = mutableListOf(),
        executor: Executor = inline,
    ) = VoiceCapture({ microphone }, { permitted }, executor, { chunks.add(it) }, { ends.add(it) })

    @Test
    fun theFormatIsWhatTheTvVoiceSessionTakes() {
        assertEquals(8000, VoiceFormat.SAMPLE_RATE)
        assertEquals(2, VoiceFormat.BYTES_PER_SAMPLE)
        assertEquals(8192, VoiceFormat.CHUNK_BYTES)
        assertEquals(8000 * 2 * 15, VoiceFormat.MAX_BYTES)
    }

    @Test
    fun theChunkerCutsWholeChunksAndKeepsTheRestForTheEnd() {
        val chunker = VoiceChunker(chunkBytes = 4)
        assertTrue(chunker.add(byteArrayOf(1, 2, 3)).isEmpty())
        val whole = chunker.add(byteArrayOf(4, 5, 6, 7, 8, 9, 10))
        assertEquals(2, whole.size)
        assertArrayEquals(byteArrayOf(1, 2, 3, 4), whole[0])
        assertArrayEquals(byteArrayOf(5, 6, 7, 8), whole[1])
        assertArrayEquals(byteArrayOf(9, 10), chunker.finish())
        assertNull(chunker.finish())
    }

    @Test
    fun theChunkerReadsOnlyTheCountedBytes() {
        val chunker = VoiceChunker(chunkBytes = 2)
        assertEquals(1, chunker.add(byteArrayOf(7, 8, 9, 9), count = 2).size)
        assertNull(chunker.finish())
    }

    @Test
    fun withoutThePermissionNothingRecords() {
        val microphone = FakeMicrophone(mutableListOf())
        val voice = capture(microphone, permitted = false)

        assertFalse(voice.start())
        assertEquals(0, microphone.started)
        assertFalse(voice.active)
    }

    @Test
    fun aCaptureSendsWholeChunksThenTheRestAndStopsTheMicrophone() {
        val chunks = mutableListOf<ByteArray>()
        val ends = mutableListOf<VoiceEnd>()
        lateinit var voice: VoiceCapture
        var reads = 0
        val microphone = FakeMicrophone(mutableListOf()) { if (++reads == 5) voice.stop() }
        voice = capture(microphone, chunks = chunks, ends = ends)

        assertTrue(voice.start())

        // 5 reads of 2048 bytes: one 8192-byte chunk, then 2048 bytes as the last chunk.
        assertEquals(listOf(8192, 2048), chunks.map { it.size })
        assertEquals(listOf(VoiceEnd.STOPPED), ends)
        assertEquals(1, microphone.started)
        assertEquals(1, microphone.stopped)
        assertFalse(voice.active)
    }

    @Test
    fun aCaptureEndsByItselfAtTheTimeLimit() {
        val chunks = mutableListOf<ByteArray>()
        val ends = mutableListOf<VoiceEnd>()
        val microphone = FakeMicrophone(mutableListOf())
        val voice = capture(microphone, chunks = chunks, ends = ends)

        assertTrue(voice.start())

        assertEquals(VoiceFormat.MAX_BYTES, chunks.sumOf { it.size })
        assertEquals(listOf(VoiceEnd.TIME_LIMIT), ends)
        assertEquals(1, microphone.stopped)
    }

    @Test
    fun aMicrophoneErrorEndsTheCaptureAndStillStopsTheMicrophone() {
        val ends = mutableListOf<VoiceEnd>()
        val failing = FakeMicrophone(mutableListOf(2048, -3))
        assertTrue(capture(failing, ends = ends).start())
        assertEquals(listOf(VoiceEnd.MICROPHONE_ERROR), ends)
        assertEquals(1, failing.stopped)

        val unopened = FakeMicrophone(mutableListOf(), failOnStart = true)
        val ended = mutableListOf<VoiceEnd>()
        assertTrue(capture(unopened, ends = ended).start())
        assertEquals(listOf(VoiceEnd.MICROPHONE_ERROR), ended)
        assertEquals(1, unopened.stopped)
    }

    @Test
    fun onlyOneCaptureRunsAtATime() {
        val queued = mutableListOf<Runnable>()
        val microphone = FakeMicrophone(mutableListOf())
        val voice = capture(microphone, executor = Executor { queued.add(it) })

        assertTrue(voice.start())
        assertFalse("a second capture waits for the first to end", voice.start())
        assertTrue(voice.active)
        voice.stop()
        queued.single().run()

        assertFalse(voice.active)
        assertEquals(1, microphone.started)
        assertEquals(1, microphone.stopped)
    }
}
