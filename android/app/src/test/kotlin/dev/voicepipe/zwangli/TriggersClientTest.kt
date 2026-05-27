package dev.voicepipe.zwangli

import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class TriggersClientTest {

    private lateinit var server: MockWebServer
    private lateinit var client: TriggersClient

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        client = TriggersClient()
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    private fun base(): String = server.url("/").toString().trimEnd('/')

    // ---------- list() ----------

    @Test
    fun `list returns triggers from GET response`() {
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """{"triggers":{"zwingli":{"action":"dispatch"},
                    "computer":{"action":"dispatch"}}}""".trimIndent(),
            ),
        )
        val result = client.list(base(), token = null) as TriggersClient.ListResult.Success
        assertEquals(listOf("computer", "zwingli"), result.triggers)
    }

    @Test
    fun `list returns AuthFailed on 401`() {
        server.enqueue(MockResponse().setResponseCode(401).setBody("nope"))
        val result = client.list(base(), token = "wrong")
        assertTrue(result is TriggersClient.ListResult.AuthFailed)
    }

    @Test
    fun `list returns Error on 500`() {
        server.enqueue(MockResponse().setResponseCode(500).setBody("oops"))
        val result = client.list(base(), token = null) as TriggersClient.ListResult.Error
        assertTrue("expected HTTP-500 in message, got '${result.message}'",
            result.message.contains("500"))
    }

    @Test
    fun `list returns Error on unreachable server`() {
        server.shutdown()
        val result = client.list(base(), token = null) as TriggersClient.ListResult.Error
        assertNotNull(result.message)
    }

    @Test
    fun `list sends Authorization header when token provided`() {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"triggers":{}}"""))
        client.list(base(), token = "my-secret")
        val req = server.takeRequest()
        assertEquals("Bearer my-secret", req.getHeader("Authorization"))
    }

    @Test
    fun `list omits Authorization header when no token`() {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"triggers":{}}"""))
        client.list(base(), token = null)
        val req = server.takeRequest()
        assertNull(req.getHeader("Authorization"))
    }

    @Test
    fun `list handles missing triggers section as empty list`() {
        server.enqueue(
            MockResponse().setResponseCode(200).setBody("""{"verbs":{"strip":{}}}"""),
        )
        val result = client.list(base(), token = null) as TriggersClient.ListResult.Success
        assertEquals(emptyList<String>(), result.triggers)
    }

    // ---------- patch() ----------

    @Test
    fun `patch success returns updated triggers list`() {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setBody("""{"ok":true,"triggers":["computer","zwingli","zwingly"]}"""),
        )
        val result = client.patch(
            base(), token = null, add = listOf("computer"),
        ) as TriggersClient.PatchResult.Success
        assertEquals(listOf("computer", "zwingli", "zwingly"), result.triggers)
    }

    @Test
    fun `patch sends correct JSON body`() {
        server.enqueue(
            MockResponse().setResponseCode(200).setBody("""{"ok":true,"triggers":[]}"""),
        )
        client.patch(base(), token = null, add = listOf("a", "b"), remove = listOf("c"))
        val req = server.takeRequest()
        assertEquals("PATCH", req.method)
        val body = req.body.readUtf8()
        assertTrue("body should contain add list, got: $body",
            body.contains("\"add\":[\"a\",\"b\"]"))
        assertTrue("body should contain remove list, got: $body",
            body.contains("\"remove\":[\"c\"]"))
    }

    @Test
    fun `patch invalid_phrase 400 maps to InvalidPhrase variant`() {
        server.enqueue(
            MockResponse().setResponseCode(400).setBody(
                """{"detail":{"error":"invalid_phrase","failures":[
                    {"phrase":"bad-phrase","reason":"must be lowercase letters"},
                    {"phrase":"x","reason":"too short"}
                ]}}""".trimIndent(),
            ),
        )
        val result = client.patch(
            base(), token = null, add = listOf("bad-phrase", "x"),
        ) as TriggersClient.PatchResult.InvalidPhrase
        assertEquals(2, result.failures.size)
        assertEquals("bad-phrase", result.failures[0].phrase)
        assertTrue(result.failures[0].reason.contains("lowercase"))
    }

    @Test
    fun `patch conflict 400 maps to Conflict variant`() {
        server.enqueue(
            MockResponse().setResponseCode(400).setBody(
                """{"detail":{"error":"conflict","overlapping":["computer"]}}""",
            ),
        )
        val result = client.patch(
            base(), token = null, add = listOf("computer"), remove = listOf("Computer"),
        ) as TriggersClient.PatchResult.Conflict
        assertEquals(listOf("computer"), result.overlapping)
    }

    @Test
    fun `patch 409 would_remove_all maps to WouldRemoveAll variant`() {
        server.enqueue(
            MockResponse().setResponseCode(409).setBody(
                """{"detail":{"error":"would_remove_all_triggers","message":"…"}}""",
            ),
        )
        val result = client.patch(
            base(), token = null, remove = listOf("zwingli"),
        )
        assertTrue(result is TriggersClient.PatchResult.WouldRemoveAll)
    }

    @Test
    fun `patch 401 maps to AuthFailed`() {
        server.enqueue(MockResponse().setResponseCode(401).setBody("nope"))
        val result = client.patch(base(), token = "wrong", add = listOf("x"))
        assertTrue(result is TriggersClient.PatchResult.AuthFailed)
    }

    @Test
    fun `patch unhandled 4xx falls through to ServerError`() {
        // 422 isn't an error we explicitly handle; should surface as generic.
        server.enqueue(MockResponse().setResponseCode(422).setBody("unprocessable"))
        val result = client.patch(base(), token = null, add = listOf("ok"))
        val err = result as TriggersClient.PatchResult.ServerError
        assertTrue(err.message.contains("422"))
    }

    @Test
    fun `patch 500 maps to ServerError`() {
        server.enqueue(MockResponse().setResponseCode(500).setBody("explode"))
        val result = client.patch(base(), token = null, add = listOf("ok"))
        assertTrue(result is TriggersClient.PatchResult.ServerError)
    }

    @Test
    fun `patch network failure maps to ServerError`() {
        server.shutdown()
        val result = client.patch(base(), token = null, add = listOf("ok"))
        assertTrue(result is TriggersClient.PatchResult.ServerError)
    }

    @Test
    fun `patch sends Authorization header when token provided`() {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"triggers":[]}"""))
        client.patch(base(), token = "shh", add = listOf("a"))
        val req = server.takeRequest()
        assertEquals("Bearer shh", req.getHeader("Authorization"))
    }

    @Test
    fun `patch empty add and remove still sends well-formed body`() {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"triggers":["zwingli"]}"""))
        val result = client.patch(base(), token = null)
        val ok = result as TriggersClient.PatchResult.Success
        assertEquals(listOf("zwingli"), ok.triggers)
        val req = server.takeRequest()
        val body = req.body.readUtf8()
        assertTrue(body.contains("\"add\":[]"))
        assertTrue(body.contains("\"remove\":[]"))
    }

    @Test
    fun `patch invalid URL surfaces as ServerError before any network call`() {
        val result = client.patch("not a url", token = null, add = listOf("ok"))
        assertTrue(result is TriggersClient.PatchResult.ServerError)
    }
}
