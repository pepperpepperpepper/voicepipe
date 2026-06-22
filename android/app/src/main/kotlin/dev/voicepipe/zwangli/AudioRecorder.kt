package dev.voicepipe.zwangli

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * Records microphone audio as 16 kHz mono 16-bit PCM and returns it WAV-wrapped
 * for upload to the server's `/transcribe-dispatch` endpoint.
 *
 * This replaces on-device [android.speech.SpeechRecognizer]: speech-to-text now
 * happens server-side (Groq Whisper), so the client just captures a clip and
 * ships the bytes. Recording reads on a background thread; [start] is a no-op if
 * already recording, and [stop] returns the captured WAV (or null if nothing was
 * captured). The caller must hold RECORD_AUDIO permission before calling [start].
 */
class AudioRecorder {
    @Volatile private var recording = false
    private var thread: Thread? = null
    private var record: AudioRecord? = null
    private val pcm = ByteArrayOutputStream()

    val isRecording: Boolean get() = recording

    /**
     * Start recording. If [onAutoStop] is supplied, the recorder watches for
     * end-of-speech (the user spoke, then went quiet for [SILENCE_END_BYTES]
     * worth of audio) — or a hard [MAX_BYTES] cap — and invokes it once, on the
     * recorder thread. Callers should marshal back to the UI thread and call
     * [stop]. This gives hands-free "press → talk → it stops itself" behavior.
     */
    @SuppressLint("MissingPermission") // caller verifies RECORD_AUDIO first
    fun start(onAutoStop: (() -> Unit)? = null): Boolean {
        if (recording) return true
        val minBuf = AudioRecord.getMinBufferSize(SAMPLE_RATE, CHANNEL, ENCODING)
        if (minBuf <= 0) return false
        val bufSize = maxOf(minBuf, SAMPLE_RATE) // ~1s of headroom
        val ar = try {
            AudioRecord(MediaRecorder.AudioSource.MIC, SAMPLE_RATE, CHANNEL, ENCODING, bufSize)
        } catch (e: Exception) {
            return false
        }
        if (ar.state != AudioRecord.STATE_INITIALIZED) {
            ar.release()
            return false
        }
        synchronized(pcm) { pcm.reset() }
        record = ar
        recording = true
        ar.startRecording()
        thread = Thread {
            val buf = ByteArray(bufSize)
            var heardVoice = false
            var silentBytes = 0L
            var totalBytes = 0L
            var fired = false
            while (recording) {
                val n = ar.read(buf, 0, buf.size)
                if (n > 0) {
                    synchronized(pcm) { pcm.write(buf, 0, n) }
                    if (onAutoStop != null && !fired) {
                        totalBytes += n
                        if (rms16(buf, n) >= VOICE_RMS) {
                            heardVoice = true
                            silentBytes = 0
                        } else if (heardVoice) {
                            silentBytes += n
                        }
                        if ((heardVoice && silentBytes >= SILENCE_END_BYTES) ||
                            totalBytes >= MAX_BYTES
                        ) {
                            fired = true
                            onAutoStop()
                        }
                    }
                }
            }
        }.also { it.start() }
        return true
    }

    /** Stop recording and return WAV bytes, or null if nothing was captured. */
    fun stop(): ByteArray? {
        if (!recording) return null
        recording = false
        joinThread()
        releaseRecord(stopFirst = true)
        val data = synchronized(pcm) { pcm.toByteArray() }
        return if (data.isEmpty()) null else wavWrap(data)
    }

    /** Abort recording and discard any captured audio. */
    fun cancel() {
        recording = false
        joinThread()
        releaseRecord(stopFirst = true)
        synchronized(pcm) { pcm.reset() }
    }

    private fun joinThread() {
        try {
            thread?.join(2000)
        } catch (_: InterruptedException) {
            Thread.currentThread().interrupt()
        }
        thread = null
    }

    private fun releaseRecord(stopFirst: Boolean) {
        record?.let {
            if (stopFirst) {
                try {
                    it.stop()
                } catch (_: IllegalStateException) {
                }
            }
            it.release()
        }
        record = null
    }

    private fun wavWrap(pcmData: ByteArray): ByteArray {
        val channels = 1
        val bitsPerSample = 16
        val byteRate = SAMPLE_RATE * channels * bitsPerSample / 8
        val blockAlign = channels * bitsPerSample / 8
        val dataLen = pcmData.size
        val header = ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN)
        header.put("RIFF".toByteArray(Charsets.US_ASCII))
        header.putInt(36 + dataLen)
        header.put("WAVE".toByteArray(Charsets.US_ASCII))
        header.put("fmt ".toByteArray(Charsets.US_ASCII))
        header.putInt(16) // PCM fmt chunk size
        header.putShort(1) // audio format = PCM
        header.putShort(channels.toShort())
        header.putInt(SAMPLE_RATE)
        header.putInt(byteRate)
        header.putShort(blockAlign.toShort())
        header.putShort(bitsPerSample.toShort())
        header.put("data".toByteArray(Charsets.US_ASCII))
        header.putInt(dataLen)
        return header.array() + pcmData
    }

    companion object {
        private const val SAMPLE_RATE = 16000
        private const val CHANNEL = AudioFormat.CHANNEL_IN_MONO
        private const val ENCODING = AudioFormat.ENCODING_PCM_16BIT

        // Endpointing (auto-stop) tuning, in bytes of 16-bit mono @16kHz
        // (32000 bytes = 1s). End ~1.5s after the user stops talking; never
        // record past ~20s.
        private const val VOICE_RMS = 600.0
        private const val SILENCE_END_BYTES = (SAMPLE_RATE * 2 * 1.5).toLong()
        private const val MAX_BYTES = (SAMPLE_RATE * 2 * 20).toLong()

        /** Root-mean-square amplitude of the first [len] bytes as LE 16-bit PCM. */
        fun rms16(buf: ByteArray, len: Int): Double {
            val samples = len / 2
            if (samples <= 0) return 0.0
            var sumSq = 0.0
            var i = 0
            while (i + 1 < len) {
                val s = (buf[i].toInt() and 0xFF) or (buf[i + 1].toInt() shl 8)
                sumSq += (s.toDouble() * s.toDouble())
                i += 2
            }
            return kotlin.math.sqrt(sumSq / samples)
        }
    }
}
