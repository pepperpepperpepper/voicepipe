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
    private lateinit var cancel: Button
    private lateinit var status: TextView
    private lateinit var statusBanner: View
    private lateinit var progressSpinner: View
    private lateinit var levelMeter: android.widget.ProgressBar
    private lateinit var response: TextView
    private lateinit var historySection: LinearLayout
    private lateinit var historyChips: ChipGroup
    private lateinit var historyClear: Button

    private val recorder = AudioRecorder()
    // The in-flight dispatch/upload coroutine, so Cancel can abort it.
    private var inFlight: kotlinx.coroutines.Job? = null
    private var pendingAutoListen: Boolean = false
    // True while the activity is stopped (backgrounded). Used to tell an assist
    // press that RE-OPENS us (→ just open) from one while already foreground
    // (→ advance the open→record→send cycle). Survives transient pause/resume.
    private var wasStopped: Boolean = true

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
        cancel = findViewById(R.id.cancel)
        cancel.setOnClickListener { cancelCurrent() }
        status = findViewById(R.id.status)
        statusBanner = findViewById(R.id.status_banner)
        progressSpinner = findViewById(R.id.progress_spinner)
        levelMeter = findViewById(R.id.level_meter)
        response = findViewById(R.id.response)
        historySection = findViewById(R.id.history_section)
        historyChips = findViewById(R.id.history_chips)
        historyClear = findViewById(R.id.history_clear)
        historyClear.setOnClickListener { confirmClearHistory() }
        send.setOnClickListener { onSend() }
        configureMic()
        renderHistory(settings.transcriptHistory)
        // Foreground-service notification tap auto-records; an assist launch
        // only OPENS (no mic) — the user presses again to record, again to send.
        pendingAutoListen = intent?.getBooleanExtra(
            ZwangliForegroundService.EXTRA_AUTO_LISTEN, false,
        ) == true
    }

    override fun onNewIntent(intent: Intent?) {
        super.onNewIntent(intent)
        setIntent(intent)
        if (intent?.getBooleanExtra(ZwangliForegroundService.EXTRA_AUTO_LISTEN, false) == true) {
            pendingAutoListen = true
            return
        }
        // Assist long-press while already foreground = the next step in the
        // open → record → send cycle (onMicClick toggles record/stop). If we're
        // coming from the background (wasStopped), this press just re-opens us;
        // the user presses again to record. onStart (which clears wasStopped)
        // fires AFTER onNewIntent on a background→foreground bring-up, so this
        // read is correct.
        if (isAssistIntent(intent) && !wasStopped) {
            mic.post { onMicClick() }
        }
    }

    override fun onStart() {
        super.onStart()
        wasStopped = false
    }

    override fun onStop() {
        wasStopped = true
        super.onStop()
    }

    private fun isAssistIntent(intent: Intent?): Boolean =
        intent?.action == Intent.ACTION_ASSIST || intent?.action == Intent.ACTION_VOICE_COMMAND

    override fun onResume() {
        super.onResume()
        // PTT bus is dormant (no caller) but kept registered harmlessly.
        Ptt.register(this)
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
        setPhase(Phase.ERROR, getString(R.string.status_cancelled))
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

    private enum class Phase { LISTENING, WORKING, DONE, ERROR, NONE }

    /** Drive the status pane: text + color, a spinner during work, a live mic
     *  meter while listening. */
    private fun setPhase(phase: Phase, text: String?) {
        if (text.isNullOrEmpty() || phase == Phase.NONE) {
            statusBanner.visibility = View.GONE
            status.text = ""
        } else {
            status.text = text
            statusBanner.visibility = View.VISIBLE
            // Tint the whole banner per phase; the rounded drawable keeps its
            // corners under the tint. White bold text reads on every color.
            statusBanner.backgroundTintList = android.content.res.ColorStateList.valueOf(
                when (phase) {
                    Phase.LISTENING -> 0xFFD32F2F.toInt() // red — recording
                    Phase.WORKING -> 0xFF1565C0.toInt()   // blue — thinking/working
                    Phase.DONE -> 0xFF2E7D32.toInt()      // green
                    Phase.ERROR -> 0xFFC62828.toInt()     // red
                    else -> getColor(R.color.status_neutral)
                },
            )
        }
        progressSpinner.visibility = if (phase == Phase.WORKING) View.VISIBLE else View.GONE
        if (phase == Phase.LISTENING) {
            levelMeter.visibility = View.VISIBLE
        } else {
            levelMeter.visibility = View.GONE
            levelMeter.progress = 0
        }
        // Cancel is available while we're recording or waiting on a request.
        cancel.visibility =
            if (phase == Phase.LISTENING || phase == Phase.WORKING) View.VISIBLE else View.GONE
    }

    /** Cancel whatever is in progress: discard a recording and/or abort an
     *  in-flight dispatch so it never fires an action. */
    private fun cancelCurrent() {
        inFlight?.cancel()
        inFlight = null
        if (recorder.isRecording) {
            recorder.cancel()
            mic.text = getString(R.string.action_mic_start)
        }
        mic.isEnabled = true
        send.isEnabled = true
        response.text = getString(R.string.response_placeholder)
        setPhase(Phase.ERROR, getString(R.string.status_cancelled))
    }

    /** Back-compat shim: plain text shown in the neutral/working style. */
    private fun setStatus(text: String?) =
        setPhase(if (text == null) Phase.NONE else Phase.WORKING, text)

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
        // No silence auto-stop: recording runs until the user stops it (tap the
        // mic, press the assist button again, or release the volume combo). A
        // hard 45s cap releases the mic if a recording is ever left running.
        val started = recorder.start(
            onMaxReached = { runOnUiThread { if (recorder.isRecording) cancelRecording() } },
            onLevel = { lvl -> runOnUiThread { levelMeter.progress = (lvl * 100).toInt() } },
        )
        if (!started) {
            Toast.makeText(
                this,
                getString(R.string.mic_error_prefix) + "recorder unavailable",
                Toast.LENGTH_SHORT,
            ).show()
            return
        }
        mic.text = getString(R.string.action_mic_listening)
        setPhase(Phase.LISTENING, getString(R.string.status_listening))
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
        setPhase(Phase.WORKING, getString(R.string.status_transcribing))
        mic.isEnabled = false
        send.isEnabled = false
        response.text = "…"
        inFlight = lifecycleScope.launch {
            val bearer = settings.token
            // Leg 1 — transcribe (STT only). Surfacing the recognized text here,
            // before any action fires, is the whole point of the two-call split.
            val sttResult = runCatching {
                withContext(Dispatchers.IO) { client.transcribe(normalizedUrl, bearer, audio) }
            }
            val heard = sttResult.getOrNull()?.transcript?.takeIf { it.isNotBlank() }
            if (heard == null) {
                val msg = sttResult.exceptionOrNull()?.message ?: "no transcript"
                response.text = "⚠ HTTP error: $msg"
                setPhase(Phase.ERROR, getString(R.string.status_error, msg))
                inFlight = null
                mic.isEnabled = true
                send.isEnabled = true
                return@launch
            }
            transcript.setText(heard)
            // Leg 2 — route + execute. The banner shows what was heard so a
            // misrecognition is visible during the (short) routing window.
            setPhase(Phase.WORKING, getString(R.string.status_routing, heard))
            val result = runCatching {
                withContext(Dispatchers.IO) {
                    client.dispatch(
                        normalizedUrl,
                        bearer,
                        DispatchRequest(
                            transcript = heard,
                            capabilities = ClientActions.CAPABILITIES,
                            // We captured this audio, so it's a command —
                            // "zwangli" is optional when the app is already open.
                            assumeCommand = true,
                        ),
                    )
                }
            }
            val rendered = result.fold(
                onSuccess = { resp -> renderSuccess(resp) },
                onFailure = { "⚠ HTTP error: ${it.message}" },
            )
            response.text = rendered
            if (!handleReachContact(result.getOrNull()) &&
                !handleResolveDial(result.getOrNull(), normalizedUrl, bearer)
            ) {
                setPhase(phaseForResult(result), statusForResult(result))
            }
            inFlight = null
            mic.isEnabled = true
            send.isEnabled = true
            if (result.isSuccess) renderHistory(settings.recordTranscript(heard))
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
        // Contacts first: "call Sam Spears" should dial a person in your phone
        // book, not trigger a web business search. Only fall through to the
        // server's Serper lookup when no contact matches the spoken name.
        val contacts = ContactResolver.phonesForName(this, query)
        if (contacts.isNotEmpty()) {
            if (contacts.size > 1) {
                promptCandidateChoice(query, contacts)
            } else {
                dialNumber(contacts[0].name?.takeIf { it.isNotBlank() } ?: query, contacts[0].phone)
            }
            return true
        }
        setPhase(Phase.WORKING, getString(R.string.status_searching, query))
        val res = runCatching {
            withContext(Dispatchers.IO) { client.resolveCall(url, bearer, query) }
        }.getOrNull()
        if (res?.ok != true || res.number.isNullOrBlank()) {
            setPhase(Phase.ERROR, getString(R.string.status_no_number, query))
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
        setPhase(Phase.WORKING, getString(R.string.status_choose, query))
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
        setPhase(Phase.DONE, getString(R.string.status_found, label, number))
        val dial = buildJsonObject {
            put("type", JsonPrimitive("dial"))
            put("number", JsonPrimitive(number))
        }
        executor.execute(listOf(dial))
    }

    /**
     * Reach a saved contact through WhatsApp / Signal / SMS. Resolves the
     * spoken name on-device: SMS → phone number; WhatsApp/Signal → the app's
     * per-contact action row. Multiple matches show a chooser; one fires
     * directly. Returns true if it handled a reach_contact action.
     */
    private fun handleReachContact(resp: DispatchResponse?): Boolean {
        val action = resp?.clientActions
            ?.let { ClientActions.parseAll(it) }
            ?.filterIsInstance<ClientAction.ReachContact>()
            ?.firstOrNull() ?: return false
        val name = action.name
        if (action.platform == "sms") {
            val contacts = ContactResolver.phonesForName(this, name)
            if (contacts.isEmpty()) {
                setPhase(Phase.ERROR, getString(R.string.status_no_contact, name))
                return true
            }
            if (contacts.size > 1) {
                AlertDialog.Builder(this)
                    .setTitle(getString(R.string.reach_choose_title, name))
                    .setItems(contacts.map { contactLabel(it.name, it.phone) }.toTypedArray()) { _, w ->
                        sendSms(contacts[w].name ?: name, contacts[w].phone, action.body)
                    }
                    .setNegativeButton(R.string.call_choose_cancel) { _, _ -> setStatus(null) }
                    .show()
            } else {
                sendSms(contacts[0].name ?: name, contacts[0].phone, action.body)
            }
            return true
        }
        // WhatsApp / Signal: ACTION_VIEW the contact's app-specific data row.
        val mime = ContactResolver.mimeTypeFor(action.platform, action.mode) ?: return false
        val rows = ContactResolver.dataRowsForName(this, name, mime)
        val platformLabel = platformLabel(action.platform)
        if (rows.isEmpty()) {
            setPhase(Phase.ERROR, getString(R.string.status_no_contact_app, name, platformLabel))
            return true
        }
        if (rows.size > 1) {
            AlertDialog.Builder(this)
                .setTitle(getString(R.string.reach_choose_title, name))
                .setItems(rows.map { it.name ?: name }.toTypedArray()) { _, w ->
                    fireReach(rows[w].name ?: name, rows[w].id, mime, action.platform, action.mode)
                }
                .setNegativeButton(R.string.call_choose_cancel) { _, _ -> setStatus(null) }
                .show()
        } else {
            fireReach(rows[0].name ?: name, rows[0].id, mime, action.platform, action.mode)
        }
        return true
    }

    private fun fireReach(label: String, rowId: Long, mime: String, platform: String, mode: String) {
        val verb = if (mode == "message") getString(R.string.reach_verb_message)
        else getString(R.string.reach_verb_call)
        setPhase(Phase.DONE, getString(R.string.status_reaching, verb, label, platformLabel(platform)))
        if (!executor.fireContactDataRow(rowId, mime)) {
            setPhase(Phase.ERROR, getString(R.string.status_no_contact_app, label, platformLabel(platform)))
        }
    }

    private fun sendSms(label: String, number: String, body: String?) {
        setPhase(Phase.DONE, getString(R.string.status_reaching,
            getString(R.string.reach_verb_message), label, getString(R.string.platform_sms)))
        executor.fireSms(number, body)
    }

    private fun contactLabel(name: String?, phone: String): String =
        if (!name.isNullOrBlank()) "$name — $phone" else phone

    private fun platformLabel(platform: String): String = when (platform) {
        "whatsapp" -> getString(R.string.platform_whatsapp)
        "signal" -> getString(R.string.platform_signal)
        else -> getString(R.string.platform_sms)
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

    private fun phaseForResult(result: Result<DispatchResponse>): Phase =
        if (result.isSuccess) Phase.DONE else Phase.ERROR

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
        inFlight = lifecycleScope.launch {
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
            if (!handleReachContact(result.getOrNull()) &&
                !handleResolveDial(result.getOrNull(), normalizedUrl, settings.token)
            ) {
                setPhase(phaseForResult(result), statusForResult(result))
            }
            inFlight = null
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
