package dev.voicepipe.zwangli

import android.content.Intent
import android.os.Bundle
import android.provider.Settings as AndroidSettings
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
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
    private lateinit var transcript: EditText
    private lateinit var send: Button
    private lateinit var response: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        settings = Settings.from(this)
        accessibilityStatus = findViewById(R.id.accessibility_status)
        openAccessibilitySettings = findViewById(R.id.open_accessibility_settings)
        serverUrl = findViewById(R.id.server_url)
        token = findViewById(R.id.token)
        transcript = findViewById(R.id.transcript)
        send = findViewById(R.id.send)
        response = findViewById(R.id.response)
        serverUrl.setText(settings.serverUrl)
        token.setText(settings.token)
        send.setOnClickListener { onSend() }
        openAccessibilitySettings.setOnClickListener {
            startActivity(Intent(AndroidSettings.ACTION_ACCESSIBILITY_SETTINGS))
        }
    }

    override fun onResume() {
        super.onResume()
        refreshAccessibilityStatus()
    }

    private fun refreshAccessibilityStatus() {
        val on = ZwangliAccessibilityService.isConnected()
        accessibilityStatus.text = getString(
            if (on) R.string.status_accessibility_on else R.string.status_accessibility_off,
        )
        openAccessibilitySettings.visibility =
            if (on) android.view.View.GONE else android.view.View.VISIBLE
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
