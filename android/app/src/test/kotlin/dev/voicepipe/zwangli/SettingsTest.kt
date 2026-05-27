package dev.voicepipe.zwangli

import dev.voicepipe.zwangli.Settings.Companion.isValidUrl
import dev.voicepipe.zwangli.Settings.Companion.normalizeUrl
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SettingsTest {

    @Test
    fun `normalizeUrl keeps a full http URL untouched`() {
        assertEquals("http://localhost:8765", normalizeUrl("http://localhost:8765"))
    }

    @Test
    fun `normalizeUrl keeps a full https URL untouched`() {
        assertEquals("https://dispatch.example.com", normalizeUrl("https://dispatch.example.com"))
    }

    @Test
    fun `normalizeUrl strips a trailing slash`() {
        assertEquals("http://localhost:8765", normalizeUrl("http://localhost:8765/"))
    }

    @Test
    fun `normalizeUrl trims whitespace`() {
        assertEquals("http://localhost:8765", normalizeUrl("  http://localhost:8765  "))
    }

    @Test
    fun `normalizeUrl adds http scheme when missing`() {
        assertEquals("http://localhost:8765", normalizeUrl("localhost:8765"))
    }

    @Test
    fun `normalizeUrl returns the default for empty input`() {
        assertEquals(Settings.DEFAULT_SERVER_URL, normalizeUrl(""))
        assertEquals(Settings.DEFAULT_SERVER_URL, normalizeUrl("   "))
    }

    @Test
    fun `isValidUrl accepts well-formed inputs`() {
        assertTrue(isValidUrl("http://localhost:8765"))
        assertTrue(isValidUrl("https://dispatch.example.com"))
        assertTrue(isValidUrl("localhost:8765"))
    }

    @Test
    fun `isValidUrl rejects garbage`() {
        assertFalse(isValidUrl("http://"))
    }

    @Test
    fun `isValidSearchUrlTemplate accepts empty as use-system-default`() {
        assertTrue(Settings.isValidSearchUrlTemplate(""))
        assertTrue(Settings.isValidSearchUrlTemplate("   "))
    }

    @Test
    fun `isValidSearchUrlTemplate accepts common engine templates`() {
        assertTrue(Settings.isValidSearchUrlTemplate("https://duckduckgo.com/?q={query}"))
        assertTrue(Settings.isValidSearchUrlTemplate("https://www.google.com/search?q={query}"))
        assertTrue(Settings.isValidSearchUrlTemplate("https://kagi.com/search?q={query}"))
        assertTrue(Settings.isValidSearchUrlTemplate("http://search.local/?q={query}&safe=on"))
    }

    @Test
    fun `isValidSearchUrlTemplate rejects template missing query placeholder`() {
        assertFalse(Settings.isValidSearchUrlTemplate("https://duckduckgo.com/?q="))
        assertFalse(Settings.isValidSearchUrlTemplate("https://example.com/search"))
    }

    @Test
    fun `isValidSearchUrlTemplate rejects template without http or https scheme`() {
        assertFalse(Settings.isValidSearchUrlTemplate("duckduckgo.com/?q={query}"))
        assertFalse(Settings.isValidSearchUrlTemplate("ftp://search/?q={query}"))
        assertFalse(Settings.isValidSearchUrlTemplate("javascript:alert({query})"))
    }

    @Test
    fun `isValidSearchUrlTemplate rejects template that wouldn't URL-parse`() {
        assertFalse(Settings.isValidSearchUrlTemplate("https:///?q={query}"))
    }

    // -----------------------------------------------------------------------
    // Transcript history (SharedPreferences-backed; uses a fake)
    // -----------------------------------------------------------------------

    private fun freshSettings(): Settings = Settings(FakeSharedPreferences())

    @Test
    fun `transcriptHistory is empty before any record`() {
        assertEquals(emptyList<String>(), freshSettings().transcriptHistory)
    }

    @Test
    fun `recordTranscript adds the entry to the front`() {
        val s = freshSettings()
        s.recordTranscript("zwingli alarm 7am")
        s.recordTranscript("zwingli timer 5 minutes")
        assertEquals(
            listOf("zwingli timer 5 minutes", "zwingli alarm 7am"),
            s.transcriptHistory,
        )
    }

    @Test
    fun `recordTranscript dedupes by move-to-front`() {
        val s = freshSettings()
        s.recordTranscript("a")
        s.recordTranscript("b")
        s.recordTranscript("c")
        s.recordTranscript("a")  // re-record; should move to front, not duplicate
        assertEquals(listOf("a", "c", "b"), s.transcriptHistory)
    }

    @Test
    fun `recordTranscript trims whitespace before storing`() {
        val s = freshSettings()
        s.recordTranscript("  zwingli home  ")
        assertEquals(listOf("zwingli home"), s.transcriptHistory)
    }

    @Test
    fun `recordTranscript ignores blank entries`() {
        val s = freshSettings()
        s.recordTranscript("zwingli home")
        s.recordTranscript("")
        s.recordTranscript("   ")
        assertEquals(listOf("zwingli home"), s.transcriptHistory)
    }

    @Test
    fun `recordTranscript caps at TRANSCRIPT_HISTORY_MAX`() {
        val s = freshSettings()
        repeat(Settings.TRANSCRIPT_HISTORY_MAX + 5) { s.recordTranscript("entry-$it") }
        val history = s.transcriptHistory
        assertEquals(Settings.TRANSCRIPT_HISTORY_MAX, history.size)
        // Most recent entry is at the front; oldest 5 should have been dropped.
        assertEquals("entry-${Settings.TRANSCRIPT_HISTORY_MAX + 4}", history.first())
        assertEquals("entry-5", history.last())
    }

    @Test
    fun `removeTranscriptHistoryEntry drops the matching entry`() {
        val s = freshSettings()
        s.recordTranscript("a")
        s.recordTranscript("b")
        s.recordTranscript("c")
        s.removeTranscriptHistoryEntry("b")
        assertEquals(listOf("c", "a"), s.transcriptHistory)
    }

    @Test
    fun `removeTranscriptHistoryEntry is no-op for missing entry`() {
        val s = freshSettings()
        s.recordTranscript("a")
        s.removeTranscriptHistoryEntry("missing")
        assertEquals(listOf("a"), s.transcriptHistory)
    }

    @Test
    fun `clearTranscriptHistory empties the list`() {
        val s = freshSettings()
        s.recordTranscript("a")
        s.recordTranscript("b")
        s.clearTranscriptHistory()
        assertEquals(emptyList<String>(), s.transcriptHistory)
    }

    @Test
    fun `transcriptHistory survives a fresh Settings instance over the same prefs`() {
        val prefs = FakeSharedPreferences()
        val s1 = Settings(prefs)
        s1.recordTranscript("a")
        s1.recordTranscript("b")
        // Rebinding Settings to the same prefs should reload the history —
        // this is the contract the activity relies on across onResume.
        val s2 = Settings(prefs)
        assertEquals(listOf("b", "a"), s2.transcriptHistory)
    }

    @Test
    fun `recordTranscript with malformed stored JSON resets cleanly`() {
        val prefs = FakeSharedPreferences()
        prefs.edit().putString(Settings.KEY_TRANSCRIPT_HISTORY, "not-json").apply()
        val s = Settings(prefs)
        // Old garbage reads as empty; a new record writes valid JSON.
        assertEquals(emptyList<String>(), s.transcriptHistory)
        s.recordTranscript("zwingli home")
        assertEquals(listOf("zwingli home"), s.transcriptHistory)
    }
}
