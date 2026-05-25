package dev.voicepipe.zwangli

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.provider.Settings as AndroidSettings
import android.speech.SpeechRecognizer
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class MainActivity : AppCompatActivity() {
    private val client = DispatchClient()
    private lateinit var settings: Settings

    private lateinit var accessibilityStatus: TextView
    private lateinit var openAccessibilitySettings: Button
    private lateinit var serverUrl: EditText
    private lateinit var token: EditText
    private lateinit var serviceToggle: Button
    private lateinit var startOnBoot: CheckBox
    private lateinit var mic: Button
    private lateinit var transcript: EditText
    private lateinit var send: Button
    private lateinit var response: TextView

    private var speech: SpeechRecognitionController? = null
    private var pendingAutoListen: Boolean = false

    private val requestMicPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (granted) startListening()
            else Toast.makeText(this, R.string.mic_permission_denied, Toast.LENGTH_SHORT).show()
        }

    private val requestNotificationPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            startServiceAfterPermission(granted)
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        settings = Settings.from(this)
        accessibilityStatus = findViewById(R.id.accessibility_status)
        openAccessibilitySettings = findViewById(R.id.open_accessibility_settings)
        serverUrl = findViewById(R.id.server_url)
        token = findViewById(R.id.token)
        serviceToggle = findViewById(R.id.service_toggle)
        startOnBoot = findViewById(R.id.start_on_boot)
        mic = findViewById(R.id.mic)
        transcript = findViewById(R.id.transcript)
        send = findViewById(R.id.send)
        response = findViewById(R.id.response)
        serverUrl.setText(settings.serverUrl)
        token.setText(settings.token)
        startOnBoot.isChecked = settings.startOnBoot
        startOnBoot.setOnCheckedChangeListener { _, checked ->
            settings.startOnBoot = checked
        }
        serviceToggle.setOnClickListener { onServiceToggle() }
        send.setOnClickListener { onSend() }
        openAccessibilitySettings.setOnClickListener {
            startActivity(Intent(AndroidSettings.ACTION_ACCESSIBILITY_SETTINGS))
        }
        configureMic()
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
        refreshAccessibilityStatus()
        refreshServiceToggle()
        if (pendingAutoListen) {
            pendingAutoListen = false
            mic.post { onMicClick() }
        }
    }

    override fun onDestroy() {
        speech?.destroy()
        speech = null
        super.onDestroy()
    }

    private fun refreshAccessibilityStatus() {
        val on = ZwangliAccessibilityService.isConnected()
        accessibilityStatus.text = getString(
            if (on) R.string.status_accessibility_on else R.string.status_accessibility_off,
        )
        openAccessibilitySettings.visibility =
            if (on) android.view.View.GONE else android.view.View.VISIBLE
    }

    private fun refreshServiceToggle() {
        serviceToggle.text = getString(
            if (ZwangliForegroundService.isRunning())
                R.string.action_service_disable
            else
                R.string.action_service_enable,
        )
    }

    private fun onServiceToggle() {
        if (ZwangliForegroundService.isRunning()) {
            ZwangliForegroundService.stop(this)
            refreshServiceToggle()
            return
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED
        ) {
            requestNotificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
            return
        }
        startServiceAfterPermission(granted = true)
    }

    private fun startServiceAfterPermission(granted: Boolean) {
        if (!granted) {
            Toast.makeText(this, R.string.mic_permission_denied, Toast.LENGTH_SHORT).show()
            return
        }
        ZwangliForegroundService.start(this)
        // Service flips `running` synchronously in onStartCommand on the main thread —
        // but startForegroundService dispatches via the system, so reflect optimistically.
        serviceToggle.text = getString(R.string.action_service_disable)
    }

    private fun configureMic() {
        if (!SpeechRecognizer.isRecognitionAvailable(this)) {
            mic.isEnabled = false
            mic.text = getString(R.string.action_mic_unavailable)
            return
        }
        speech = SpeechRecognitionController(this, micCallbacks)
        mic.setOnClickListener { onMicClick() }
    }

    private fun onMicClick() {
        val controller = speech ?: return
        if (controller.isListening) {
            controller.stop()
            return
        }
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            == PackageManager.PERMISSION_GRANTED
        ) {
            startListening()
        } else {
            requestMicPermission.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    private fun startListening() {
        speech?.start()
    }

    private val micCallbacks = object : SpeechRecognitionController.Callbacks {
        override fun onListeningStart() {
            mic.text = getString(R.string.action_mic_listening)
        }

        override fun onPartial(text: String) {
            transcript.setText(text)
        }

        override fun onFinal(text: String) {
            transcript.setText(text)
            onSend()
        }

        override fun onError(message: String, recoverable: Boolean) {
            Toast.makeText(
                this@MainActivity,
                getString(R.string.mic_error_prefix) + message,
                Toast.LENGTH_SHORT,
            ).show()
        }

        override fun onListeningStop() {
            mic.text = getString(R.string.action_mic_start)
        }
    }

    private fun onSend() {
        val url = serverUrl.text.toString().trim()
        val bearer = token.text.toString()
        val text = transcript.text.toString()
        if (url.isEmpty() || text.isEmpty()) {
            response.text = getString(R.string.response_placeholder)
            return
        }
        settings.serverUrl = url
        settings.token = bearer
        val normalizedUrl = Settings.normalizeUrl(url)
        send.isEnabled = false
        response.text = "…"
        lifecycleScope.launch {
            val rendered = runCatching {
                withContext(Dispatchers.IO) {
                    client.dispatch(
                        normalizedUrl,
                        bearer,
                        DispatchRequest(transcript = text),
                    )
                }
            }.fold(
                onSuccess = { renderSuccess(it) },
                onFailure = { "⚠ HTTP error: ${it.message}" },
            )
            response.text = rendered
            send.isEnabled = true
        }
    }

    private fun renderSuccess(resp: DispatchResponse): String {
        val typingResult = tryTypeOutput(resp.outputText)
        return buildString {
            append("ok=").append(resp.ok).append('\n')
            append("output_text=").append(resp.outputText).append('\n')
            append(typingResult).append('\n')
            if (resp.clientActions.isNotEmpty()) {
                append("client_actions=").append(resp.clientActions).append('\n')
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
