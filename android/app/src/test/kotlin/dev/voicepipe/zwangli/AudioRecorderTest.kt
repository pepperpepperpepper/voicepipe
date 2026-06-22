package dev.voicepipe.zwangli

import kotlin.math.abs
import org.junit.Assert.assertTrue
import org.junit.Test

class AudioRecorderTest {

    private fun pcm(amplitude: Int, samples: Int): ByteArray {
        val b = ByteArray(samples * 2)
        var i = 0
        while (i < samples) {
            // alternate +/- amplitude so RMS ≈ amplitude
            val v = if (i % 2 == 0) amplitude else -amplitude
            b[i * 2] = (v and 0xFF).toByte()
            b[i * 2 + 1] = (v shr 8).toByte()
            i++
        }
        return b
    }

    @Test
    fun `rms of silence is near zero`() {
        val buf = ByteArray(640) // all zeros
        assertTrue(AudioRecorder.rms16(buf, buf.size) < 1.0)
    }

    @Test
    fun `rms of loud signal is large`() {
        val buf = pcm(5000, 320)
        assertTrue(AudioRecorder.rms16(buf, buf.size) > 4000.0)
    }

    @Test
    fun `rms tracks amplitude`() {
        val quiet = AudioRecorder.rms16(pcm(200, 320), 640)
        val loud = AudioRecorder.rms16(pcm(3000, 320), 640)
        assertTrue(loud > quiet)
        assertTrue(abs(quiet - 200.0) < 5.0)
    }
}
