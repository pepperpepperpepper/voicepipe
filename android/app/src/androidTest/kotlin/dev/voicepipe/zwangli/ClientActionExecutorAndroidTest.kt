package dev.voicepipe.zwangli

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class ClientActionExecutorAndroidTest {

    private val context get() = InstrumentationRegistry.getInstrumentation().targetContext

    @Test
    fun success_tone_plays_to_completion() = runFeedback("success")

    @Test
    fun error_tone_plays_to_completion() = runFeedback("error")

    @Test
    fun match_tone_plays_to_completion() = runFeedback("match")

    @Test
    fun all_three_events_play_to_completion_in_one_batch() {
        val latch = CountDownLatch(3)
        val completed = ConcurrentHashMap.newKeySet<String>()
        val failed = ConcurrentHashMap.newKeySet<String>()
        val listener = object : ClientActionExecutor.FeedbackListener {
            override fun onCompleted(event: String, success: Boolean) {
                if (success) completed.add(event) else failed.add(event)
                latch.countDown()
            }
        }
        val executor = ClientActionExecutor(context, listener)
        val summary = executor.execute(
            listOf(
                feedbackAction("success"),
                feedbackAction("error"),
                feedbackAction("match"),
            ),
        )
        assertEquals(3, summary.feedbackPlayed)
        assertEquals(0, summary.unknownSkipped)
        assertTrue(
            "all 3 tones should complete within timeout (completed=$completed failed=$failed)",
            latch.await(15, TimeUnit.SECONDS),
        )
        assertEquals(setOf("success", "error", "match"), completed)
        assertEquals(emptySet<String>(), failed)
    }

    @Test
    fun unknown_event_is_counted_but_not_played() {
        val unhandledLatch = CountDownLatch(1)
        val played = mutableListOf<String>()
        val listener = object : ClientActionExecutor.FeedbackListener {
            override fun onCompleted(event: String, success: Boolean) {
                played.add(event)
                unhandledLatch.countDown()
            }
        }
        val executor = ClientActionExecutor(context, listener)
        val summary = executor.execute(listOf(feedbackAction("nonexistent_event")))
        assertEquals(
            "feedback with unmapped event should not increment feedbackPlayed",
            0,
            summary.feedbackPlayed,
        )
        // Listener should never fire for an event that has no audio resource.
        assertTrue(
            "listener should not be called for unmapped event",
            !unhandledLatch.await(2, TimeUnit.SECONDS),
        )
        assertEquals(emptyList<String>(), played)
    }

    private fun runFeedback(event: String) {
        val latch = CountDownLatch(1)
        var completedEvent: String? = null
        var completedOk = false
        val listener = object : ClientActionExecutor.FeedbackListener {
            override fun onCompleted(e: String, success: Boolean) {
                completedEvent = e
                completedOk = success
                latch.countDown()
            }
        }
        val executor = ClientActionExecutor(context, listener)
        val summary = executor.execute(listOf(feedbackAction(event)))
        assertEquals(1, summary.feedbackPlayed)
        assertTrue(
            "tone '$event' should complete within timeout",
            latch.await(10, TimeUnit.SECONDS),
        )
        assertEquals(event, completedEvent)
        assertTrue("tone '$event' should report success", completedOk)
    }

    private fun feedbackAction(event: String): JsonElement =
        Json.parseToJsonElement("""{"type":"feedback","event":"$event"}""")
}
