package dev.voicepipe.zwangli

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ClientActionsTest {

    @Test
    fun `capabilities advertise clipboard and audio_feedback`() {
        assertEquals(listOf("clipboard", "audio_feedback"), ClientActions.CAPABILITIES)
    }

    @Test
    fun `parses clipboard action`() {
        val a = ClientActions.parse(parse("""{"type":"clipboard","text":"hello"}"""))
        assertEquals(ClientAction.Clipboard("hello"), a)
    }

    @Test
    fun `parses feedback action`() {
        val a = ClientActions.parse(parse("""{"type":"feedback","event":"success"}"""))
        assertEquals(ClientAction.Feedback("success"), a)
    }

    @Test
    fun `clipboard requires text field`() {
        assertNull(ClientActions.parse(parse("""{"type":"clipboard"}""")))
    }

    @Test
    fun `feedback requires non-blank event`() {
        assertNull(ClientActions.parse(parse("""{"type":"feedback","event":""}""")))
        assertNull(ClientActions.parse(parse("""{"type":"feedback"}""")))
    }

    @Test
    fun `unknown type round-trips as Unknown`() {
        val a = ClientActions.parse(parse("""{"type":"shell","cmd":"ls"}"""))
        assertTrue(a is ClientAction.Unknown)
        assertEquals("shell", (a as ClientAction.Unknown).type)
    }

    @Test
    fun `parseAll filters out non-objects`() {
        val all = ClientActions.parseAll(
            listOf(
                parse("""{"type":"clipboard","text":"x"}"""),
                parse(""""bare-string""""),
                parse("""42"""),
                parse("""{"type":"feedback","event":"error"}"""),
            ),
        )
        assertEquals(2, all.size)
        assertEquals(ClientAction.Clipboard("x"), all[0])
        assertEquals(ClientAction.Feedback("error"), all[1])
    }

    @Test
    fun `parse returns null when type is missing`() {
        assertNull(ClientActions.parse(parse("""{"text":"hello"}""")))
    }

    @Test
    fun `parse returns null when type is non-string`() {
        assertNull(ClientActions.parse(parse("""{"type":42}""")))
    }

    private fun parse(raw: String): JsonElement = Json.parseToJsonElement(raw)
}
