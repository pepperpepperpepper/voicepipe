package dev.voicepipe.zwangli

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ContactResolverTest {

    @Test
    fun `addresses are detected and skip contact lookup`() {
        assertTrue(ContactResolver.looksLikeEmail("ran.blake@example.com"))
        assertTrue(ContactResolver.looksLikeEmail("  bob@host.co  "))
    }

    @Test
    fun `spoken names are not addresses and trigger lookup`() {
        assertFalse(ContactResolver.looksLikeEmail("Ran Blake"))
        assertFalse(ContactResolver.looksLikeEmail("bob"))
        // A stray space around an @ is not a usable address.
        assertFalse(ContactResolver.looksLikeEmail("ran blake @ example"))
    }
}
