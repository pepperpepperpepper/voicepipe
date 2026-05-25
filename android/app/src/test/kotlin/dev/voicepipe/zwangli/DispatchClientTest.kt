package dev.voicepipe.zwangli

import java.io.IOException
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test

class DispatchClientTest {
    private lateinit var server: MockWebServer
    private lateinit var client: DispatchClient

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        client = DispatchClient()
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    @Test
    fun `sends correct json body`() {
        server.enqueue(okResponse())

        client.dispatch(
            baseUrl = server.url("/").toString(),
            token = null,
            request = DispatchRequest(transcript = "zwingli strip hello"),
        )

        val recorded = server.takeRequest()
        assertEquals("POST", recorded.method)
        assertEquals("/dispatch", recorded.path)
        assertTrue(recorded.getHeader("Content-Type")!!.startsWith("application/json"))

        val sent = Json.parseToJsonElement(recorded.body.readUtf8()).jsonObject
        assertEquals("zwingli strip hello", sent["transcript"]?.jsonPrimitive?.content)
        assertNull(sent["session_id"])
        assertNull(sent["capabilities"])
    }

    @Test
    fun `attaches bearer when token set`() {
        server.enqueue(okResponse())

        client.dispatch(
            baseUrl = server.url("/").toString(),
            token = "abc123",
            request = DispatchRequest(transcript = "hi"),
        )

        assertEquals("Bearer abc123", server.takeRequest().getHeader("Authorization"))
    }

    @Test
    fun `omits bearer when token blank`() {
        server.enqueue(okResponse())

        client.dispatch(
            baseUrl = server.url("/").toString(),
            token = "   ",
            request = DispatchRequest(transcript = "hi"),
        )

        assertNull(server.takeRequest().getHeader("Authorization"))
    }

    @Test
    fun `omits bearer when token null`() {
        server.enqueue(okResponse())

        client.dispatch(
            baseUrl = server.url("/").toString(),
            token = null,
            request = DispatchRequest(transcript = "hi"),
        )

        assertNull(server.takeRequest().getHeader("Authorization"))
    }

    @Test
    fun `parses typical response`() {
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """
                {
                  "ok": true,
                  "output_text": "hello",
                  "payload": {"matched": "strip"},
                  "client_actions": [
                    {"type": "clipboard", "text": "hello"}
                  ]
                }
                """.trimIndent(),
            ),
        )

        val resp = client.dispatch(
            baseUrl = server.url("/").toString(),
            token = null,
            request = DispatchRequest(transcript = "zwingli strip hello"),
        )

        assertTrue(resp.ok)
        assertEquals("hello", resp.outputText)
        assertEquals(1, resp.clientActions.size)
        assertNotNull(resp.payload)
        assertEquals("strip", resp.payload!!["matched"]?.jsonPrimitive?.content)
    }

    @Test
    fun `parses empty client actions`() {
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """{"ok": true, "output_text": "x"}""",
            ),
        )

        val resp = client.dispatch(
            baseUrl = server.url("/").toString(),
            token = null,
            request = DispatchRequest(transcript = "x"),
        )

        assertTrue(resp.clientActions.isEmpty())
        assertNull(resp.payload)
    }

    @Test
    fun `surfaces non-2xx as exception`() {
        server.enqueue(MockResponse().setResponseCode(500).setBody("boom"))

        try {
            client.dispatch(
                baseUrl = server.url("/").toString(),
                token = null,
                request = DispatchRequest(transcript = "x"),
            )
            fail("expected IOException")
        } catch (e: IOException) {
            assertTrue(e.message!!.contains("500"))
        }
    }

    @Test
    fun `forwards capabilities when supplied`() {
        server.enqueue(okResponse())

        client.dispatch(
            baseUrl = server.url("/").toString(),
            token = null,
            request = DispatchRequest(
                transcript = "zwingli strip x",
                capabilities = listOf("clipboard", "audio_feedback"),
            ),
        )

        val sent = Json.parseToJsonElement(server.takeRequest().body.readUtf8()).jsonObject
        val caps = sent["capabilities"]!!
        assertEquals("""["clipboard","audio_feedback"]""", caps.toString())
    }

    private fun okResponse(): MockResponse = MockResponse()
        .setResponseCode(200)
        .setBody("""{"ok": true, "output_text": ""}""")
}
