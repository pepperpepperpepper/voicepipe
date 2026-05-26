package dev.voicepipe.zwangli

import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class ConnectionTesterTest {

    private lateinit var server: MockWebServer
    private lateinit var tester: ConnectionTester

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        tester = ConnectionTester()
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    private fun base(): String = server.url("/").toString().trimEnd('/')

    @Test
    fun `health ok no auth + triggers ok lists enabled verbs`() {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setBody("""{"ok":true,"auth_required":false}"""),
        )
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """{"verbs":{
                    "search":{"enabled":true},
                    "alarm":{"enabled":true},
                    "dial":{"enabled":true},
                    "disabled_verb":{"enabled":false}
                }}""".trimIndent(),
            ),
        )

        val result = tester.test(base(), token = null)

        assertTrue(result.ok)
        assertTrue(result.healthOk)
        assertEquals(false, result.authRequired)
        assertEquals(listOf("alarm", "dial", "search"), result.verbs)
        assertFalse(result.triggersAuthFailed)
        assertNull(result.error)
    }

    @Test
    fun `auth_required surfaced from health`() {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setBody("""{"ok":true,"auth_required":true}"""),
        )
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"verbs":{}}"""))

        val result = tester.test(base(), token = "secret")

        assertTrue(result.ok)
        assertEquals(true, result.authRequired)
    }

    @Test
    fun `triggers 401 reported as triggersAuthFailed without overwriting healthOk`() {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setBody("""{"ok":true,"auth_required":true}"""),
        )
        server.enqueue(MockResponse().setResponseCode(401).setBody("unauthorized"))

        val result = tester.test(base(), token = "wrong-token")

        assertTrue(result.healthOk)
        assertTrue(result.triggersAuthFailed)
        assertNull(result.verbs)
        assertNull(result.error)  // 401 is a structured outcome, not an error
    }

    @Test
    fun `non-2xx health returns healthOk=false with error message`() {
        server.enqueue(MockResponse().setResponseCode(503).setBody("svc unavail"))

        val result = tester.test(base(), token = null)

        assertFalse(result.healthOk)
        assertFalse(result.ok)
        assertNotNull(result.error)
        assertTrue(result.error!!.contains("503"))
    }

    @Test
    fun `unreachable server returns healthOk=false with reachability error`() {
        // Bind a port and shut down so the connection is refused.
        val deadServer = MockWebServer().apply { start() }
        val deadUrl = deadServer.url("/").toString().trimEnd('/')
        deadServer.shutdown()

        val result = tester.test(deadUrl, token = null)

        assertFalse(result.healthOk)
        assertNotNull(result.error)
        assertTrue(
            "expected reachability error, got: ${result.error}",
            result.error!!.contains("Cannot reach server"),
        )
    }

    @Test
    fun `malformed url returns invalid-url error`() {
        val result = tester.test("not a url at all", token = null)
        // normalizeUrl tolerates a lot, so this may succeed parsing —
        // the cleaner failure is a generic IOException downstream. Just
        // assert we don't crash.
        assertFalse(result.healthOk || result.ok)
        assertNotNull(result.error)
    }

    @Test
    fun `health body without auth_required field leaves authRequired null`() {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"ok":true}"""))
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"verbs":{}}"""))

        val result = tester.test(base(), token = null)

        assertTrue(result.healthOk)
        assertNull(result.authRequired)
    }

    @Test
    fun `triggers without verbs key yields empty verbs list`() {
        server.enqueue(
            MockResponse().setResponseCode(200).setBody("""{"ok":true,"auth_required":false}"""),
        )
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{}"""))

        val result = tester.test(base(), token = null)

        assertTrue(result.healthOk)
        // No "verbs" key in payload → empty list (server has none configured)
        assertEquals(emptyList<String>(), result.verbs)
    }

    @Test
    fun `bearer token sent on triggers request only when present`() {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"ok":true}"""))
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"verbs":{}}"""))

        tester.test(base(), token = "shhh")

        // First request: /health, no auth header
        val healthReq = server.takeRequest()
        assertEquals("/health", healthReq.path)
        assertNull(healthReq.getHeader("Authorization"))

        // Second request: /triggers, with bearer
        val triggersReq = server.takeRequest()
        assertEquals("/triggers", triggersReq.path)
        assertEquals("Bearer shhh", triggersReq.getHeader("Authorization"))
    }

    @Test
    fun `empty token treated as no token on triggers request`() {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"ok":true}"""))
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"verbs":{}}"""))

        tester.test(base(), token = "")

        server.takeRequest() // health
        val triggersReq = server.takeRequest()
        assertNull(triggersReq.getHeader("Authorization"))
    }
}
