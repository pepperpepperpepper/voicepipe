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
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject

/** Operational test bench: mic → record audio → /transcribe-dispatch (server
 *  STT + routing) → response. The editable transcript field + Send button keep
 *  the text-in /dispatch path for replay/editing. All setup state (server URL,
 *  token, permissions, foreground service) lives in [ConfiguratorActivity],
 *  reached via the overflow menu.
 */
class MainActivity : AppCompatActivity(), Ptt.Listener {
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
        pendingAutoListen = shouldAutoListen(intent)
    }

    override fun onNewIntent(intent: Intent?) {
        super.onNewIntent(intent)
        setIntent(intent)
        // A second side-button/assist press while we're already foreground
        // toggles: start if idle, or stop+send if recording. onResume won't
        // re-fire here (we're already resumed), so act directly.
        if (shouldAutoListen(intent)) {
            mic.post { onMicClick() }
        }
    }

    /** True when we were launched to immediately start listening: the
     *  foreground-service tap, or the assist gesture / "Hold for Assistant"
     *  side button (ACTION_ASSIST / VOICE_COMMAND). */
    private fun shouldAutoListen(intent: Intent?): Boolean {
        if (intent == null) return false
        if (intent.getBooleanExtra(ZwangliForegroundService.EXTRA_AUTO_LISTEN, false)) return true
        return intent.action == Intent.ACTION_ASSIST ||
            intent.action == Intent.ACTION_VOICE_COMMAND
    }

    override fun onResume() {
        super.onResume()
        // PTT: registering applies a release that raced the launch. If we were
        // launched to start talking and the gesture is still held, begin now.
        Ptt.register(this)
        if (intent?.getBooleanExtra(Ptt.EXTRA_PTT_START, false) == true) {
            intent.removeExtra(Ptt.EXTRA_PTT_START)
            if (Ptt.isArmed() && !recorder.isRecording) startRecording()
        }
        maybeAutoListen()
    }

    override fun onPause() {
        Ptt.unregister(this)
        super.onPause()
    }

    // --- Ptt.Listener (volume-combo push-to-talk) ---
    override fun onPttStart() {
        runOnUiThread { if (!recorder.isRecording) startRecording() }
    }

    override fun onPttStop(send: Boolean) {
        runOnUiThread {
            if (!recorder.isRecording) return@runOnUiThread
            if (send) stopAndUpload() else cancelRecording()
        }
    }

    private fun cancelRecording() {
        recorder.cancel()
        mic.text = getString(R.string.action_mic_start)
        setStatus(getString(R.string.status_cancelled))
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        // Belt-and-suspenders: the mic needs the window focused to capture, and
        // on an assist launch onResume can run a hair before focus settles.
        if (hasFocus) maybeAutoListen()
    }

    private fun maybeAutoListen() {
        if (!pendingAutoListen) return
        if (recorder.isRecording) { pendingAutoListen = false; return }
        pendingAutoListen = false
        mic.post { onMicClick() }
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
        // No auto-stop: recording runs until the user stops it (tap the mic, or
        // press the assist/side button again — see onNewIntent toggle).
        val started = recorder.start()
        if (!started) {
            Toast.makeText(
                this,
                getString(R.string.mic_error_prefix) + "recorder unavailable",
                Toast.LENGTH_SHORT,
            ).show()
            return
        }
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
            if (!handleResolveDial(result.getOrNull(), normalizedUrl, settings.token)) {
                setStatus(statusForResult(result))
            }
            mic.isEnabled = true
            send.isEnabled = true
            // Record the server-returned transcript so the text path can replay it.
            val heard = result.getOrNull()?.transcript
            if (result.isSuccess && !heard.isNullOrEmpty()) {
                renderHistory(settings.recordTranscript(heard))
            }
        }
    }

    /**
     * If the response contains a `resolve_dial` action, run the two-step call
     * flow with live status: 🔎 Searching → resolve via /resolve-call → ✓ Found
     * → dial. Returns true if it handled one (and set its own status), so the
     * caller skips the generic status line.
     */
    private suspend fun handleResolveDial(
        resp: DispatchResponse?,
        url: String,
        bearer: String,
    ): Boolean {
        val query = resp?.clientActions
            ?.let { ClientActions.parseAll(it) }
            ?.filterIsInstance<ClientAction.ResolveDial>()
            ?.firstOrNull()?.query ?: return false
        setStatus(getString(R.string.status_searching, query))
        val res = runCatching {
            withContext(Dispatchers.IO) { client.resolveCall(url, bearer, query) }
        }.getOrNull()
        if (res?.ok != true || res.number.isNullOrBlank()) {
            setStatus(getString(R.string.status_no_number, query))
            return true
        }
        // Multiple matches → let the user choose; one → dial directly.
        val candidates = res.candidates.filter { it.phone.isNotBlank() }
        if (candidates.size > 1) {
            promptCandidateChoice(query, candidates)
        } else {
            val label = res.name?.takeIf { it.isNotBlank() } ?: query
            dialNumber(label, res.number)
        }
        return true
    }

    /** Show a chooser for ambiguous lookups; dial the picked candidate. */
    private fun promptCandidateChoice(query: String, candidates: List<CallCandidate>) {
        setStatus(getString(R.string.status_choose, query))
        val labels = candidates.map { c ->
            val name = c.name?.takeIf { it.isNotBlank() } ?: c.phone
            val addr = c.address?.takeIf { it.isNotBlank() }
            if (addr != null) "$name\n$addr — ${c.phone}" else "$name — ${c.phone}"
        }.toTypedArray()
        AlertDialog.Builder(this)
            .setTitle(getString(R.string.call_choose_title, query))
            .setItems(labels) { _, which ->
                val c = candidates[which]
                dialNumber(c.name?.takeIf { it.isNotBlank() } ?: query, c.phone)
            }
            .setNegativeButton(R.string.call_choose_cancel) { _, _ ->
                setStatus(null)
            }
            .show()
    }

    private fun dialNumber(label: String, number: String) {
        setStatus(getString(R.string.status_found, label, number))
        val dial = buildJsonObject {
            put("type", JsonPrimitive("dial"))
            put("number", JsonPrimitive(number))
        }
        executor.execute(listOf(dial))
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
            if (!handleResolveDial(result.getOrNull(), normalizedUrl, settings.token)) {
                setStatus(statusForResult(result))
            }
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
