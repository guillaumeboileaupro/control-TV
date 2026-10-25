package io.github.guillaumeboileaupro.controltv

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import java.io.ByteArrayOutputStream
import java.util.concurrent.Executor
import java.util.concurrent.atomic.AtomicBoolean

/**
 * The phone's voice for the TV's Google Assistant, in the format the TV's remote protocol takes
 * (`androidtvremote2.start_voice`: 16-bit PCM, mono, 8000 Hz, chunks of at least 8 KB).
 *
 * Capture only: handing the chunks to the TV needs a voice session in the control layer (Python
 * bridge and TV remote adapter), which is not wired yet. Nothing here records unless asked to,
 * and every capture stops by itself after [VoiceFormat.MAX_SECONDS].
 */
object VoiceFormat {
    const val SAMPLE_RATE = 8000
    const val BYTES_PER_SAMPLE = 2
    const val CHUNK_BYTES = 8192
    const val MAX_SECONDS = 15
    const val MAX_BYTES = SAMPLE_RATE * BYTES_PER_SAMPLE * MAX_SECONDS
}

/** Cuts a PCM byte stream into chunks of [VoiceFormat.CHUNK_BYTES]; the last one may be shorter. */
class VoiceChunker(private val chunkBytes: Int = VoiceFormat.CHUNK_BYTES) {
    private val pending = ByteArrayOutputStream()

    init {
        require(chunkBytes > 0 && chunkBytes % VoiceFormat.BYTES_PER_SAMPLE == 0)
    }

    /** The whole chunks completed by [count] bytes of [data]. */
    fun add(data: ByteArray, count: Int = data.size): List<ByteArray> {
        require(count in 0..data.size)
        pending.write(data, 0, count)
        val buffered = pending.toByteArray()
        val whole = buffered.size / chunkBytes * chunkBytes
        pending.reset()
        pending.write(buffered, whole, buffered.size - whole)
        return (0 until whole step chunkBytes).map { buffered.copyOfRange(it, it + chunkBytes) }
    }

    /** What is left, as a last chunk (none when empty); the chunker is then empty. */
    fun finish(): ByteArray? = pending.toByteArray().also { pending.reset() }.takeIf { it.isNotEmpty() }
}

/** A microphone: started, read until stopped. */
interface Microphone {
    fun start()

    /** Fills [buffer]; the number of bytes read, or a negative value on an error. */
    fun read(buffer: ByteArray): Int

    fun stop()
}

/** Why a capture ended. */
enum class VoiceEnd { STOPPED, TIME_LIMIT, MICROPHONE_ERROR }

/**
 * One voice capture at a time, only while asked for: [start] refuses without the microphone
 * permission and while a capture runs; the capture ends on [stop], at the time limit, or on a
 * microphone error, and the microphone is always stopped.
 */
class VoiceCapture(
    private val microphone: () -> Microphone,
    private val permitted: () -> Boolean,
    private val executor: Executor,
    private val onChunk: (ByteArray) -> Unit,
    private val onEnd: (VoiceEnd) -> Unit,
) {
    private val running = AtomicBoolean(false)
    private val stopRequested = AtomicBoolean(false)

    val active: Boolean get() = running.get()

    /** Whether a capture started; false without the permission or while one runs. */
    fun start(): Boolean {
        if (!permitted()) return false
        if (!running.compareAndSet(false, true)) return false
        stopRequested.set(false)
        executor.execute { capture() }
        return true
    }

    /** Ends the running capture (no effect when none runs). */
    fun stop() {
        stopRequested.set(true)
    }

    private fun capture() {
        val chunker = VoiceChunker()
        var end = VoiceEnd.STOPPED
        var source: Microphone? = null
        try {
            source = microphone()
            source.start()
            val buffer = ByteArray(VoiceFormat.CHUNK_BYTES / 4)
            var total = 0
            while (!stopRequested.get()) {
                if (total >= VoiceFormat.MAX_BYTES) {
                    end = VoiceEnd.TIME_LIMIT
                    break
                }
                val read = source.read(buffer)
                if (read < 0) {
                    end = VoiceEnd.MICROPHONE_ERROR
                    break
                }
                val count = minOf(read, VoiceFormat.MAX_BYTES - total)
                total += count
                chunker.add(buffer, count).forEach(onChunk)
            }
            chunker.finish()?.let(onChunk)
        } catch (error: Exception) {
            end = VoiceEnd.MICROPHONE_ERROR
        } finally {
            try {
                source?.stop()
            } catch (ignored: Exception) {
                end = VoiceEnd.MICROPHONE_ERROR
            }
            running.set(false)
            onEnd(end)
        }
    }
}

/** The phone's microphone through [AudioRecord], in [VoiceFormat]; callers check the permission. */
class AudioRecordMicrophone : Microphone {
    private var record: AudioRecord? = null

    @SuppressLint("MissingPermission") // VoiceCapture.start checks RECORD_AUDIO first
    override fun start() {
        val channel = AudioFormat.CHANNEL_IN_MONO
        val encoding = AudioFormat.ENCODING_PCM_16BIT
        val minimum = AudioRecord.getMinBufferSize(VoiceFormat.SAMPLE_RATE, channel, encoding)
        val created = AudioRecord(
            MediaRecorder.AudioSource.VOICE_RECOGNITION,
            VoiceFormat.SAMPLE_RATE,
            channel,
            encoding,
            maxOf(minimum, VoiceFormat.CHUNK_BYTES),
        )
        check(created.state == AudioRecord.STATE_INITIALIZED) { "the microphone cannot be opened" }
        record = created
        created.startRecording()
    }

    override fun read(buffer: ByteArray): Int = record?.read(buffer, 0, buffer.size) ?: -1

    override fun stop() {
        record?.let {
            try {
                it.stop()
            } finally {
                it.release()
            }
        }
        record = null
    }
}
