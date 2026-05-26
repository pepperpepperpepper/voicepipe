package dev.voicepipe.zwangli

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.Build
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import dev.voicepipe.zwangli.debug.InjectTranscriptReceiver
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class InjectTranscriptReceiverTest {

    private val context get() = InstrumentationRegistry.getInstrumentation().targetContext
    private lateinit var server: MockWebServer

    @Before
    fun startServer() {
        server = MockWebServer()
        server.start()
    }

    @After
    fun stopServer() {
        server.shutdown()
    }

    @Test
    fun broadcast_drives_dispatch_and_reports_feedback_count() {
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """{"ok":true,"output_text":"hello from injection",""" +
                    """"client_actions":[{"type":"feedback","event":"success"}]}""",
            ),
        )

        val result = sendInjectAndAwaitResult(
            transcript = "zwingli inject smoke",
            serverUrl = serverBaseUrl(),
        )

        val recorded = server.takeRequest(10, TimeUnit.SECONDS)
        assertNotNull("dispatch request should have arrived at MockWebServer", recorded)
        assertEquals("/dispatch", recorded!!.path)
        val sentBody = recorded.body.readUtf8()
        assertTrue(
            "request body should contain injected transcript ($sentBody)",
            sentBody.contains("zwingli inject smoke"),
        )
        assertTrue(
            "request body should advertise capabilities ($sentBody)",
            sentBody.contains("audio_feedback"),
        )

        assertEquals(true, result.ok)
        assertNull(result.error)
        assertEquals("hello from injection", result.outputText)
        assertEquals(1, result.feedbackPlayed)
        assertEquals(0, result.clipboardApplied)
        assertEquals(0, result.unknownSkipped)
    }

    @Test
    fun broadcast_reports_error_when_server_unreachable() {
        // Shut down the mock server immediately so the dispatch call fails.
        val unreachable = server.url("/").toString().trimEnd('/')
        server.shutdown()

        val result = sendInjectAndAwaitResult(
            transcript = "should fail",
            serverUrl = unreachable,
        )

        assertEquals(false, result.ok)
        assertNotNull("error should be reported back ($result)", result.error)
    }

    private data class InjectResult(
        val ok: Boolean,
        val error: String?,
        val outputText: String?,
        val feedbackPlayed: Int,
        val clipboardApplied: Int,
        val unknownSkipped: Int,
    )

    private fun serverBaseUrl(): String = server.url("/").toString().trimEnd('/')

    private fun sendInjectAndAwaitResult(transcript: String, serverUrl: String): InjectResult {
        val latch = CountDownLatch(1)
        val captured = AtomicReference<InjectResult?>(null)
        val resultReceiver = object : BroadcastReceiver() {
            override fun onReceive(ctx: Context, intent: Intent) {
                captured.set(
                    InjectResult(
                        ok = intent.getBooleanExtra(InjectTranscriptReceiver.EXTRA_OK, false),
                        error = intent.getStringExtra(InjectTranscriptReceiver.EXTRA_ERROR),
                        outputText =
                            intent.getStringExtra(InjectTranscriptReceiver.EXTRA_OUTPUT_TEXT),
                        feedbackPlayed = intent.getIntExtra(
                            InjectTranscriptReceiver.EXTRA_FEEDBACK_PLAYED,
                            -1,
                        ),
                        clipboardApplied = intent.getIntExtra(
                            InjectTranscriptReceiver.EXTRA_CLIPBOARD_APPLIED,
                            -1,
                        ),
                        unknownSkipped = intent.getIntExtra(
                            InjectTranscriptReceiver.EXTRA_UNKNOWN_SKIPPED,
                            -1,
                        ),
                    ),
                )
                latch.countDown()
            }
        }
        val filter = IntentFilter(InjectTranscriptReceiver.ACTION_RESULT)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            context.registerReceiver(resultReceiver, filter, Context.RECEIVER_EXPORTED)
        } else {
            @Suppress("UnspecifiedRegisterReceiverFlag")
            context.registerReceiver(resultReceiver, filter)
        }

        try {
            val inject = Intent(InjectTranscriptReceiver.ACTION).apply {
                `package` = context.packageName
                putExtra(InjectTranscriptReceiver.EXTRA_TRANSCRIPT, transcript)
                putExtra(InjectTranscriptReceiver.EXTRA_SERVER_URL, serverUrl)
            }
            context.sendBroadcast(inject)
            assertTrue(
                "result broadcast should arrive within timeout",
                latch.await(15, TimeUnit.SECONDS),
            )
        } finally {
            context.unregisterReceiver(resultReceiver)
        }
        return captured.get() ?: error("no result captured")
    }
}
