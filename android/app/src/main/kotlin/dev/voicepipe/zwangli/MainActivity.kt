package dev.voicepipe.zwangli

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.view.Menu
import android.view.MenuItem
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.google.android.material.appbar.MaterialToolbar
import com.google.android.material.chip.Chip
import com.google.android.material.chip.ChipGroup
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** Operational test bench: mic → record audio → /transcribe-dispatch (server
 *  STT + routing) → response. The editable transcript field + Send button keep
 *  the text-in /dispatch path for replay/editing. All setup state (server URL,
 *  token, permissions, foreground service) lives in [ConfiguratorActivity],
 *  reached via the overflow menu.
 */
class MainActivity : AppCompatActivity() {
    private val client = DispatchClient()
    private lateinit var settings: Settings
    private lateinit var executor: ClientActionExecutor

    private lateinit var mic: Button
    private lateinit var transcript: EditText
    private lateinit var send: Button
    private lateinit var status: TextView
    private lateinit var response: TextView
    private lateinit var historySection: LinearLayout
    private lateinit var historyChips: ChipGroup
    private lateinit var historyClear: Button

    private val recorder = AudioRecorder()
    private var pendingAutoListen: Boolean = false

    private val requestMicPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (granted) startRecording()
            else Toast.makeText(this, R.string.mic_permission_denied, Toast.LENGTH_SHORT).show()
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        setSupportActionBar(findViewById<MaterialToolbar>(R.id.toolbar))
        settings = Settings.from(this)
        executor = ClientActionExecutor(applicationContext)
        mic = findViewById(R.id.mic)
        transcript = findViewById(R.id.transcript)
        send = findViewById(R.id.send)
        status = findViewById(R.id.status)
        response = findViewById(R.id.response)
        historySection = findViewById(R.id.history_section)
        historyChips = findViewById(R.id.history_chips)
        historyClear = findViewById(R.id.history_clear)
        historyClear.setOnClickListener { confirmClearHistory() }
        send.setOnClickListener { onSend() }
        configureMic()
        renderHistory(settings.transcriptHistory)
        pendingAutoListen = intent?.getBooleanExtra(
            ZwangliForegroundService.EXTRA_AUTO_LISTEN,
            false,
        ) == true
    }

    override fun onNewIntent(intent: Intent?) {
        super.onNewIntent(intent)
        setIntent(intent)
        if (intent?.getBooleanExtra(ZwangliForegroundService.EXTRA_AUTO_LISTEN, false) == true) {
            pendingAutoListen = true
        }
    }

    override fun onResume() {
        super.onResume()
        if (pendingAutoListen) {
            pendingAutoListen = false
            mic.post { onMicClick() }
        }
    }

    override fun onDestroy() {
        recorder.cancel()
        super.onDestroy()
    }

    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menuInflater.inflate(R.menu.main_menu, menu)
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean = when (item.itemId) {
        R.id.menu_configurator -> {
            startActivity(Intent(this, ConfiguratorActivity::class.java))
            true
        }
        R.id.menu_self_test -> {
            startActivity(Intent(this, TestActivity::class.java))
            true
        }
        else -> super.onOptionsItemSelected(item)
    }

    private fun configureMic() {
        mic.setOnClickListener { onMicClick() }
    }

    /** Drive the status pane. Pass null to hide it. */
    private fun setStatus(text: String?) {
        if (text.isNullOrEmpty()) {
            status.visibility = View.GONE
            status.text = ""
        } else {
            status.text = text
            status.visibility = View.VISIBLE
        }
    }

    private fun onMicClick() {
        if (recorder.isRecording) {
            stopAndUpload()
            return
        }
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            == PackageManager.PERMISSION_GRANTED
        ) {
            startRecording()
        } else {
            requestMicPermission.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    private fun startRecording() {
        if (!recorder.start()) {
            Toast.makeText(
                this,
                getString(R.string.mic_error_prefix) + "recorder unavailable",
                Toast.LENGTH_SHORT,
            ).show()
            return
        }
        // Tap again to stop + upload. (No on-device VAD; manual stop for v1.)
        mic.text = getString(R.string.action_mic_listening)
        setStatus(getString(R.string.status_listening))
    }

    private fun stopAndUpload() {
        val audio = recorder.stop()
        mic.text = getString(R.string.action_mic_start)
        if (audio == null) {
            setStatus(null)
            Toast.makeText(
                this,
                getString(R.string.mic_error_prefix) + "no audio captured",
                Toast.LENGTH_SHORT,
            ).show()
            return
        }
        val url = settings.serverUrl
        if (url.isEmpty()) {
            setStatus(null)
            response.text = getString(R.string.response_placeholder)
            return
        }
        val normalizedUrl = Settings.normalizeUrl(url)
        setStatus(getString(R.string.status_uploading))
        mic.isEnabled = false
        send.isEnabled = false
        response.text = "…"
        lifecycleScope.launch {
            setStatus(getString(R.string.status_working))
            val bearer = settings.token
            val result = runCatching {
                withContext(Dispatchers.IO) {
                    client.transcribeDispatch(
                        normalizedUrl,
                        bearer,
                        audio,
                        ClientActions.CAPABILITIES,
                    )
                }
            }
            val rendered = result.fold(
                onSuccess = { resp ->
                    resp.transcript?.let { transcript.setText(it) }
                    renderSuccess(resp)
                },
                onFailure = { "⚠ HTTP error: ${it.message}" },
            )
            response.text = rendered
            setStatus(statusForResult(result))
            mic.isEnabled = true
            send.isEnabled = true
            // Record the server-returned transcript so the text path can replay it.
            val heard = result.getOrNull()?.transcript
            if (result.isSuccess && !heard.isNullOrEmpty()) {
                renderHistory(settings.recordTranscript(heard))
            }
        }
    }

    /** Concise status-pane line summarizing a dispatch round-trip outcome. */
    private fun statusForResult(result: Result<DispatchResponse>): String =
        result.fold(
            onSuccess = { resp ->
                val heard = resp.transcript?.takeIf { it.isNotBlank() }
                if (heard != null) getString(R.string.status_heard_done, heard)
                else getString(R.string.status_done)
            },
            onFailure = { getString(R.string.status_error, it.message ?: "request failed") },
        )

    private fun onSend() {
        val text = transcript.text.toString()
        val url = settings.serverUrl
        if (url.isEmpty() || text.isEmpty()) {
            response.text = getString(R.string.response_placeholder)
            return
        }
        val normalizedUrl = Settings.normalizeUrl(url)
        send.isEnabled = false
        response.text = "…"
        setStatus(getString(R.string.status_working))
        lifecycleScope.launch {
            val bearer = settings.token
            val result = runCatching {
                withContext(Dispatchers.IO) {
                    client.dispatch(
                        normalizedUrl,
                        bearer,
                        DispatchRequest(
                            transcript = text,
                            capabilities = ClientActions.CAPABILITIES,
                        ),
                    )
                }
            }
            val rendered = result.fold(
                onSuccess = { renderSuccess(it) },
                onFailure = { "⚠ HTTP error: ${it.message}" },
            )
            response.text = rendered
            setStatus(statusForResult(result))
            send.isEnabled = true
            // Record the transcript on every successful HTTP round-trip,
            // even when the server returned ok=false. The history is the
            // user's, not the dispatcher's: a malformed verb is still
            // something they may want to replay and edit.
            if (result.isSuccess) {
                renderHistory(settings.recordTranscript(text))
            }
        }
    }

    private fun renderHistory(entries: List<String>) {
        historyChips.removeAllViews()
        if (entries.isEmpty()) {
            historySection.visibility = View.GONE
            return
        }
        historySection.visibility = View.VISIBLE
        for (entry in entries) {
            historyChips.addView(buildHistoryChip(entry))
        }
    }

    private fun buildHistoryChip(entry: String): Chip {
        return Chip(this).apply {
            text = entry
            isCheckable = false
            isClickable = true
            // Tap = load into the field, don't auto-send. The user almost
            // always wants to tweak before re-firing; an auto-send would
            // make destructive verbs (clipboard overwrite, alarm set)
            // surprisingly easy to re-trigger.
            setOnClickListener { transcript.setText(entry) }
            setOnLongClickListener {
                confirmRemoveFromHistory(entry)
                true
            }
        }
    }

    private fun confirmRemoveFromHistory(entry: String) {
        AlertDialog.Builder(this)
            .setTitle(R.string.history_remove_confirm_title)
            .setMessage(getString(R.string.history_remove_confirm_message, entry))
            .setNegativeButton(R.string.history_remove_confirm_negative, null)
            .setPositiveButton(R.string.history_remove_confirm_positive) { _, _ ->
                renderHistory(settings.removeTranscriptHistoryEntry(entry))
            }
            .show()
    }

    private fun confirmClearHistory() {
        val current = settings.transcriptHistory
        if (current.isEmpty()) return
        AlertDialog.Builder(this)
            .setTitle(R.string.history_clear_confirm_title)
            .setMessage(getString(R.string.history_clear_confirm_message, current.size))
            .setNegativeButton(R.string.history_clear_confirm_negative, null)
            .setPositiveButton(R.string.history_clear_confirm_positive) { _, _ ->
                settings.clearTranscriptHistory()
                renderHistory(emptyList())
            }
            .show()
    }

    private fun renderSuccess(resp: DispatchResponse): String {
        // Only attempt to type when there's actually text to type. Action-only
        // verbs (email/alarm/calendar/…) return empty output_text, and typing
        // "" would otherwise surface a misleading "no editable field" warning.
        val typingResult =
            if (resp.outputText.isEmpty()) null else tryTypeOutput(resp.outputText)
        val summary = executor.execute(resp.clientActions)
        return buildString {
            append("ok=").append(resp.ok).append('\n')
            append("output_text=").append(resp.outputText).append('\n')
            if (typingResult != null) append(typingResult).append('\n')
            if (resp.clientActions.isNotEmpty()) {
                append("client_actions=").append(resp.clientActions).append('\n')
                append("applied: clipboard=").append(summary.clipboardApplied)
                    .append(" feedback=").append(summary.feedbackPlayed)
                    .append(" intents=").append(summary.intentsFired)
                if (summary.globalActionsFired > 0) {
                    append(" global=").append(summary.globalActionsFired)
                }
                if (summary.unknownSkipped > 0) {
                    append(" unknown=").append(summary.unknownSkipped)
                }
                append('\n')
            }
            resp.payload?.let { append("payload=").append(it).append('\n') }
        }
    }

    private fun tryTypeOutput(text: String): String {
        if (text.isEmpty()) return getString(R.string.typed_failed)
        return if (ZwangliAccessibilityService.typeIntoFocusedField(text))
            getString(R.string.typed_ok)
        else
            getString(R.string.typed_failed)
    }
}
