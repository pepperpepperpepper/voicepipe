package dev.voicepipe.zwangli.debug

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import dev.voicepipe.zwangli.DispatchPipeline
import dev.voicepipe.zwangli.Settings
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

class InjectTranscriptReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        val transcript = intent.getStringExtra(EXTRA_TRANSCRIPT)
        if (transcript.isNullOrBlank()) {
            Log.w(TAG, "INJECT_TRANSCRIPT missing '$EXTRA_TRANSCRIPT' extra; ignoring")
            return
        }
        val urlOverride = intent.getStringExtra(EXTRA_SERVER_URL)
        val tokenOverride = intent.getStringExtra(EXTRA_TOKEN)
        val appContext = context.applicationContext
        val pending = goAsync()
        CoroutineScope(Dispatchers.IO).launch {
            try {
                val settings = Settings.from(appContext)
                val url = urlOverride ?: settings.serverUrl
                val token = tokenOverride ?: settings.token
                val pipeline = DispatchPipeline(appContext)
                val outcome = pipeline.run(url, token, transcript)
                val reply = Intent(ACTION_RESULT).apply {
                    `package` = appContext.packageName
                    putExtra(EXTRA_OK, outcome.error == null)
                    outcome.error?.let { putExtra(EXTRA_ERROR, it.toString()) }
                    outcome.response?.let { putExtra(EXTRA_OUTPUT_TEXT, it.outputText) }
                    outcome.summary?.let {
                        putExtra(EXTRA_CLIPBOARD_APPLIED, it.clipboardApplied)
                        putExtra(EXTRA_FEEDBACK_PLAYED, it.feedbackPlayed)
                        putExtra(EXTRA_INTENTS_FIRED, it.intentsFired)
                        putExtra(EXTRA_UNKNOWN_SKIPPED, it.unknownSkipped)
                    }
                }
                appContext.sendBroadcast(reply)
                Log.i(TAG, "Injected transcript='$transcript' ok=${outcome.error == null}")
            } catch (e: Throwable) {
                Log.e(TAG, "Inject failed", e)
            } finally {
                pending.finish()
            }
        }
    }

    companion object {
        const val ACTION = "dev.voicepipe.zwangli.INJECT_TRANSCRIPT"
        const val ACTION_RESULT = "dev.voicepipe.zwangli.INJECT_TRANSCRIPT_RESULT"
        const val EXTRA_TRANSCRIPT = "transcript"
        const val EXTRA_SERVER_URL = "server_url"
        const val EXTRA_TOKEN = "token"
        const val EXTRA_OK = "ok"
        const val EXTRA_ERROR = "error"
        const val EXTRA_OUTPUT_TEXT = "output_text"
        const val EXTRA_CLIPBOARD_APPLIED = "clipboard_applied"
        const val EXTRA_FEEDBACK_PLAYED = "feedback_played"
        const val EXTRA_INTENTS_FIRED = "intents_fired"
        const val EXTRA_UNKNOWN_SKIPPED = "unknown_skipped"
        private const val TAG = "InjectTranscriptReceiver"
    }
}
