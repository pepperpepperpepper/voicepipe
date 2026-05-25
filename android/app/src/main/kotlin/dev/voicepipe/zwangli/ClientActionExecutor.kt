package dev.voicepipe.zwangli

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.media.MediaPlayer
import android.util.Log
import kotlinx.serialization.json.JsonElement

class ClientActionExecutor(private val context: Context) {

    fun execute(actions: List<JsonElement>): Summary {
        var clipboardCount = 0
        var feedbackCount = 0
        var unknownCount = 0
        for (action in ClientActions.parseAll(actions)) {
            when (action) {
                is ClientAction.Clipboard -> {
                    if (applyClipboard(action.text)) clipboardCount++
                }
                is ClientAction.Feedback -> {
                    if (playFeedback(action.event)) feedbackCount++
                }
                is ClientAction.Unknown -> {
                    unknownCount++
                    Log.i(TAG, "Skipping unknown client_action type=${action.type}")
                }
            }
        }
        return Summary(clipboardCount, feedbackCount, unknownCount)
    }

    data class Summary(
        val clipboardApplied: Int,
        val feedbackPlayed: Int,
        val unknownSkipped: Int,
    ) {
        fun anything(): Boolean = clipboardApplied + feedbackPlayed + unknownSkipped > 0
    }

    private fun applyClipboard(text: String): Boolean {
        val mgr = context.getSystemService(ClipboardManager::class.java) ?: return false
        return try {
            mgr.setPrimaryClip(ClipData.newPlainText("zwangli", text))
            true
        } catch (e: SecurityException) {
            Log.w(TAG, "Clipboard access denied", e)
            false
        }
    }

    private fun playFeedback(event: String): Boolean {
        val resId = FeedbackSounds.resourceFor(event) ?: return false
        return try {
            val player = MediaPlayer.create(context, resId) ?: return false
            player.setOnCompletionListener { it.release() }
            player.setOnErrorListener { mp, _, _ -> mp.release(); true }
            player.start()
            true
        } catch (e: Exception) {
            Log.w(TAG, "Feedback playback failed for event=$event", e)
            false
        }
    }

    companion object {
        private const val TAG = "ClientActionExecutor"
    }
}
