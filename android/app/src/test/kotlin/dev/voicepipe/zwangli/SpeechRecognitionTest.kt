package dev.voicepipe.zwangli

import android.speech.SpeechRecognizer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SpeechRecognitionTest {

    @Test
    fun `topResult returns null for null list`() {
        assertNull(SpeechRecognition.topResult(null))
    }

    @Test
    fun `topResult returns null for empty list`() {
        assertNull(SpeechRecognition.topResult(emptyList()))
    }

    @Test
    fun `topResult returns null when every entry is blank`() {
        assertNull(SpeechRecognition.topResult(listOf("", "   ", "\t")))
    }

    @Test
    fun `topResult returns the first non-blank entry`() {
        assertEquals(
            "zwingli strip hello",
            SpeechRecognition.topResult(listOf("zwingli strip hello", "another guess")),
        )
    }

    @Test
    fun `topResult skips leading blanks`() {
        assertEquals(
            "hello world",
            SpeechRecognition.topResult(listOf("", "  ", "hello world", "also")),
        )
    }

    @Test
    fun `topResult trims surrounding whitespace`() {
        assertEquals("hello", SpeechRecognition.topResult(listOf("   hello   ")))
    }

    @Test
    fun `describeError produces distinct messages for well-known codes`() {
        val codes = listOf(
            SpeechRecognizer.ERROR_AUDIO,
            SpeechRecognizer.ERROR_CLIENT,
            SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS,
            SpeechRecognizer.ERROR_NETWORK,
            SpeechRecognizer.ERROR_NETWORK_TIMEOUT,
            SpeechRecognizer.ERROR_NO_MATCH,
            SpeechRecognizer.ERROR_RECOGNIZER_BUSY,
            SpeechRecognizer.ERROR_SERVER,
            SpeechRecognizer.ERROR_SPEECH_TIMEOUT,
        )
        val messages = codes.map(SpeechRecognition::describeError)
        for (m in messages) assertFalse("message must be non-empty", m.isBlank())
        for (i in messages.indices) for (j in i + 1 until messages.size) {
            assertNotEquals("$i vs $j collide", messages[i], messages[j])
        }
    }

    @Test
    fun `describeError falls back for unknown codes`() {
        val msg = SpeechRecognition.describeError(99)
        assertTrue("unknown-code message includes the code", msg.contains("99"))
    }

    @Test
    fun `isRecoverable marks transient codes recoverable`() {
        assertTrue(SpeechRecognition.isRecoverable(SpeechRecognizer.ERROR_NO_MATCH))
        assertTrue(SpeechRecognition.isRecoverable(SpeechRecognizer.ERROR_SPEECH_TIMEOUT))
        assertTrue(SpeechRecognition.isRecoverable(SpeechRecognizer.ERROR_RECOGNIZER_BUSY))
    }

    @Test
    fun `isRecoverable marks hard failures non-recoverable`() {
        assertFalse(SpeechRecognition.isRecoverable(SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS))
        assertFalse(SpeechRecognition.isRecoverable(SpeechRecognizer.ERROR_AUDIO))
        assertFalse(SpeechRecognition.isRecoverable(SpeechRecognizer.ERROR_CLIENT))
    }
}
