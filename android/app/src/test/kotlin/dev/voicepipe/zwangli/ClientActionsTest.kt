package dev.voicepipe.zwangli

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ClientActionsTest {

    @Test
    fun `capabilities advertise clipboard, audio, and intent action types`() {
        val expected = listOf(
            "clipboard",
            "audio_feedback",
            "web_search",
            "open_url",
            "set_alarm",
            "set_timer",
            "dial",
            "resolve_dial",
            "reach_contact",
            "navigate",
            "accessibility_global",
            "calendar",
            "email",
        )
        assertEquals(expected, ClientActions.CAPABILITIES)
    }

    @Test
    fun `reach_contact parses platform, mode, name, and optional body`() {
        val a = ClientActions.parse(
            parse("""{"type":"reach_contact","name":"Sam Spears","platform":"whatsapp","mode":"video"}"""),
        )
        assertEquals(ClientAction.ReachContact("Sam Spears", "whatsapp", "video", null), a)

        val b = ClientActions.parse(
            parse("""{"type":"reach_contact","name":"Mom","platform":"signal","mode":"message","body":"call me"}"""),
        )
        assertEquals(ClientAction.ReachContact("Mom", "signal", "message", "call me"), b)
    }

    @Test
    fun `reach_contact rejects unknown platform or mode`() {
        assertNull(
            ClientActions.parse(
                parse("""{"type":"reach_contact","name":"X","platform":"telegram","mode":"call"}"""),
            ),
        )
        assertNull(
            ClientActions.parse(
                parse("""{"type":"reach_contact","name":"X","platform":"sms","mode":"fax"}"""),
            ),
        )
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

    @Test
    fun `parses web_search action`() {
        val a = ClientActions.parse(parse("""{"type":"web_search","query":"weather tokyo"}"""))
        assertEquals(ClientAction.WebSearch("weather tokyo"), a)
    }

    @Test
    fun `web_search rejects blank or missing query`() {
        assertNull(ClientActions.parse(parse("""{"type":"web_search","query":""}""")))
        assertNull(ClientActions.parse(parse("""{"type":"web_search","query":"   "}""")))
        assertNull(ClientActions.parse(parse("""{"type":"web_search"}""")))
    }

    @Test
    fun `parses open_url action`() {
        val a = ClientActions.parse(parse("""{"type":"open_url","url":"https://example.com"}"""))
        assertEquals(ClientAction.OpenUrl("https://example.com"), a)
    }

    @Test
    fun `open_url rejects blank url`() {
        assertNull(ClientActions.parse(parse("""{"type":"open_url","url":""}""")))
        assertNull(ClientActions.parse(parse("""{"type":"open_url"}""")))
    }

    @Test
    fun `parses set_alarm with message`() {
        val a = ClientActions.parse(
            parse("""{"type":"set_alarm","hour":7,"minutes":30,"message":"wake up"}"""),
        )
        assertEquals(ClientAction.SetAlarm(7, 30, "wake up"), a)
    }

    @Test
    fun `set_alarm message is optional`() {
        val a = ClientActions.parse(parse("""{"type":"set_alarm","hour":7,"minutes":0}"""))
        assertEquals(ClientAction.SetAlarm(7, 0, null), a)
    }

    @Test
    fun `set_alarm blank message normalizes to null`() {
        val a = ClientActions.parse(
            parse("""{"type":"set_alarm","hour":7,"minutes":0,"message":""}"""),
        )
        assertEquals(ClientAction.SetAlarm(7, 0, null), a)
    }

    @Test
    fun `parses relative set_alarm with in_seconds`() {
        val a = ClientActions.parse(
            parse("""{"type":"set_alarm","in_seconds":120,"message":"standup"}"""),
        )
        assertEquals(ClientAction.SetAlarm(null, null, "standup", 120), a)
    }

    @Test
    fun `relative set_alarm rejects out-of-range in_seconds`() {
        assertNull(ClientActions.parse(parse("""{"type":"set_alarm","in_seconds":0}""")))
        assertNull(
            ClientActions.parse(parse("""{"type":"set_alarm","in_seconds":86401}""")),
        )
    }

    @Test
    fun `set_alarm rejects out-of-range hour or minutes`() {
        assertNull(ClientActions.parse(parse("""{"type":"set_alarm","hour":24,"minutes":0}""")))
        assertNull(ClientActions.parse(parse("""{"type":"set_alarm","hour":-1,"minutes":0}""")))
        assertNull(ClientActions.parse(parse("""{"type":"set_alarm","hour":0,"minutes":60}""")))
        assertNull(ClientActions.parse(parse("""{"type":"set_alarm","hour":0,"minutes":-1}""")))
    }

    @Test
    fun `set_alarm requires hour and minutes as numbers`() {
        assertNull(ClientActions.parse(parse("""{"type":"set_alarm","hour":7}""")))
        assertNull(ClientActions.parse(parse("""{"type":"set_alarm","minutes":30}""")))
        assertNull(
            ClientActions.parse(parse("""{"type":"set_alarm","hour":"7","minutes":"30"}""")),
        )
    }

    @Test
    fun `parses set_timer with seconds and message`() {
        val a = ClientActions.parse(
            parse("""{"type":"set_timer","seconds":300,"message":"pasta"}"""),
        )
        assertEquals(ClientAction.SetTimer(300, "pasta"), a)
    }

    @Test
    fun `set_timer message is optional`() {
        val a = ClientActions.parse(parse("""{"type":"set_timer","seconds":60}"""))
        assertEquals(ClientAction.SetTimer(60, null), a)
    }

    @Test
    fun `set_timer rejects out-of-range seconds`() {
        assertNull(ClientActions.parse(parse("""{"type":"set_timer","seconds":0}""")))
        assertNull(ClientActions.parse(parse("""{"type":"set_timer","seconds":-1}""")))
        assertNull(ClientActions.parse(parse("""{"type":"set_timer","seconds":86401}""")))
    }

    @Test
    fun `set_timer requires seconds`() {
        assertNull(ClientActions.parse(parse("""{"type":"set_timer","message":"x"}""")))
    }

    @Test
    fun `parses dial action`() {
        val a = ClientActions.parse(parse("""{"type":"dial","number":"+15555551234"}"""))
        assertEquals(ClientAction.Dial("+15555551234"), a)
    }

    @Test
    fun `dial rejects blank or missing number`() {
        assertNull(ClientActions.parse(parse("""{"type":"dial","number":""}""")))
        assertNull(ClientActions.parse(parse("""{"type":"dial"}""")))
    }

    @Test
    fun `parses navigate action with destination only`() {
        val a = ClientActions.parse(parse("""{"type":"navigate","destination":"paris"}"""))
        assertEquals(ClientAction.Navigate("paris", null), a)
    }

    @Test
    fun `parses navigate action with destination and mode`() {
        val a = ClientActions.parse(
            parse("""{"type":"navigate","destination":"the library","mode":"walking"}"""),
        )
        assertEquals(ClientAction.Navigate("the library", "walking"), a)
    }

    @Test
    fun `navigate rejects blank or missing destination`() {
        assertNull(ClientActions.parse(parse("""{"type":"navigate","destination":""}""")))
        assertNull(ClientActions.parse(parse("""{"type":"navigate"}""")))
        assertNull(
            ClientActions.parse(parse("""{"type":"navigate","destination":"  "}""")),
        )
    }

    @Test
    fun `navigate normalizes unknown mode to null`() {
        // Server validates mode strings but if anything weird slips through
        // (e.g. spelling drift) we drop it rather than rejecting the action
        // entirely — destination still opens in Maps without auto-routing.
        val a = ClientActions.parse(
            parse("""{"type":"navigate","destination":"paris","mode":"flying"}"""),
        )
        assertEquals(ClientAction.Navigate("paris", null), a)
    }

    @Test
    fun `navigate accepts all four canonical modes`() {
        for (mode in listOf("driving", "walking", "bicycling", "transit")) {
            val a = ClientActions.parse(
                parse("""{"type":"navigate","destination":"x","mode":"$mode"}"""),
            )
            assertEquals(
                "mode '$mode' should round-trip",
                ClientAction.Navigate("x", mode),
                a,
            )
        }
    }

    @Test
    fun `accessibility_global parses all five canonical actions`() {
        for (a in listOf("back", "home", "recents", "notifications", "quick_settings")) {
            val parsed = ClientActions.parse(
                parse("""{"type":"accessibility_global","action":"$a"}"""),
            )
            assertEquals(
                "action '$a' should round-trip",
                ClientAction.AccessibilityGlobal(a),
                parsed,
            )
        }
    }

    @Test
    fun `accessibility_global rejects unknown action`() {
        assertNull(
            ClientActions.parse(
                parse("""{"type":"accessibility_global","action":"reboot"}"""),
            ),
        )
    }

    @Test
    fun `accessibility_global rejects missing or blank action`() {
        assertNull(
            ClientActions.parse(parse("""{"type":"accessibility_global"}""")),
        )
        assertNull(
            ClientActions.parse(
                parse("""{"type":"accessibility_global","action":""}"""),
            ),
        )
    }

    private fun parse(raw: String): JsonElement = Json.parseToJsonElement(raw)
}
