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
}
