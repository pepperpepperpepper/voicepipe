package dev.voicepipe.zwangli

import android.app.SearchManager
import android.content.ActivityNotFoundException
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.media.MediaPlayer
import android.net.Uri
import android.provider.AlarmClock
import android.util.Log
import kotlinx.serialization.json.JsonElement

class ClientActionExecutor(
    private val context: Context,
    private val feedbackListener: FeedbackListener? = null,
) {

    interface FeedbackListener {
        fun onCompleted(event: String, success: Boolean)
    }

    fun execute(actions: List<JsonElement>): Summary {
        var clipboardCount = 0
        var feedbackCount = 0
        var intentsFired = 0
        var unknownCount = 0
        for (action in ClientActions.parseAll(actions)) {
            when (action) {
                is ClientAction.Clipboard -> {
                    if (applyClipboard(action.text)) clipboardCount++
                }
                is ClientAction.Feedback -> {
                    if (playFeedback(action.event)) feedbackCount++
                }
                is ClientAction.WebSearch -> {
                    if (fireWebSearch(action.query)) intentsFired++
                }
                is ClientAction.OpenUrl -> {
                    if (fireOpenUrl(action.url)) intentsFired++
                }
                is ClientAction.SetAlarm -> {
                    if (fireSetAlarm(action.hour, action.minutes, action.message)) {
                        intentsFired++
                    }
                }
                is ClientAction.SetTimer -> {
                    if (fireSetTimer(action.seconds, action.message)) intentsFired++
                }
                is ClientAction.Dial -> {
                    if (fireDial(action.number)) intentsFired++
                }
                is ClientAction.Unknown -> {
                    unknownCount++
                    Log.i(TAG, "Skipping unknown client_action type=${action.type}")
                }
            }
        }
        return Summary(clipboardCount, feedbackCount, intentsFired, unknownCount)
    }

    data class Summary(
        val clipboardApplied: Int,
        val feedbackPlayed: Int,
        val intentsFired: Int,
        val unknownSkipped: Int,
    ) {
        fun anything(): Boolean =
            clipboardApplied + feedbackPlayed + intentsFired + unknownSkipped > 0
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
            val player = MediaPlayer.create(context, resId) ?: run {
                feedbackListener?.onCompleted(event, false)
                return false
            }
            player.setOnCompletionListener {
                it.release()
                feedbackListener?.onCompleted(event, true)
            }
            player.setOnErrorListener { mp, _, _ ->
                mp.release()
                feedbackListener?.onCompleted(event, false)
                true
            }
            player.start()
            true
        } catch (e: Exception) {
            Log.w(TAG, "Feedback playback failed for event=$event", e)
            feedbackListener?.onCompleted(event, false)
            false
        }
    }

    private fun fireWebSearch(query: String): Boolean = fireIntent(
        Intent(Intent.ACTION_WEB_SEARCH).putExtra(SearchManager.QUERY, query),
        "web_search",
    )

    private fun fireOpenUrl(url: String): Boolean {
        val uri = try {
            Uri.parse(url)
        } catch (e: Exception) {
            Log.w(TAG, "open_url: cannot parse url '$url'", e)
            return false
        }
        return fireIntent(Intent(Intent.ACTION_VIEW, uri), "open_url")
    }

    private fun fireSetAlarm(hour: Int, minutes: Int, message: String?): Boolean {
        val intent = Intent(AlarmClock.ACTION_SET_ALARM).apply {
            putExtra(AlarmClock.EXTRA_HOUR, hour)
            putExtra(AlarmClock.EXTRA_MINUTES, minutes)
            if (!message.isNullOrBlank()) putExtra(AlarmClock.EXTRA_MESSAGE, message)
        }
        return fireIntent(intent, "set_alarm")
    }

    private fun fireSetTimer(seconds: Int, message: String?): Boolean {
        val intent = Intent(AlarmClock.ACTION_SET_TIMER).apply {
            putExtra(AlarmClock.EXTRA_LENGTH, seconds)
            if (!message.isNullOrBlank()) putExtra(AlarmClock.EXTRA_MESSAGE, message)
        }
        return fireIntent(intent, "set_timer")
    }

    private fun fireDial(number: String): Boolean {
        val uri = try {
            Uri.fromParts("tel", number, null)
        } catch (e: Exception) {
            Log.w(TAG, "dial: cannot build tel uri for '$number'", e)
            return false
        }
        return fireIntent(Intent(Intent.ACTION_DIAL, uri), "dial")
    }

    private fun fireIntent(intent: Intent, label: String): Boolean {
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        if (intent.resolveActivity(context.packageManager) == null) {
            Log.w(TAG, "No activity to handle $label (action=${intent.action})")
            return false
        }
        return try {
            context.startActivity(intent)
            true
        } catch (e: ActivityNotFoundException) {
            Log.w(TAG, "ActivityNotFoundException firing $label", e)
            false
        } catch (e: SecurityException) {
            Log.w(TAG, "SecurityException firing $label", e)
            false
        }
    }

    companion object {
        private const val TAG = "ClientActionExecutor"
    }
}
