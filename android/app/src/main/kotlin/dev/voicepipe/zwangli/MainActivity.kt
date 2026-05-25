package dev.voicepipe.zwangli

import android.os.Bundle
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

    private lateinit var serverUrl: EditText
    private lateinit var token: EditText
    private lateinit var transcript: EditText
    private lateinit var send: Button
    private lateinit var response: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        serverUrl = findViewById(R.id.server_url)
        token = findViewById(R.id.token)
        transcript = findViewById(R.id.transcript)
        send = findViewById(R.id.send)
        response = findViewById(R.id.response)
        send.setOnClickListener { onSend() }
    }

    private fun onSend() {
        val url = serverUrl.text.toString().trim()
        val bearer = token.text.toString()
        val text = transcript.text.toString()
        if (url.isEmpty() || text.isEmpty()) {
            response.text = getString(R.string.response_placeholder)
            return
        }
        send.isEnabled = false
        response.text = "…"
        lifecycleScope.launch {
            val rendered = runCatching {
                withContext(Dispatchers.IO) {
                    client.dispatch(
                        url,
                        bearer,
                        DispatchRequest(transcript = text),
                    )
                }
            }.fold(
                onSuccess = { formatResponse(it) },
                onFailure = { "⚠ HTTP error: ${it.message}" },
            )
            response.text = rendered
            send.isEnabled = true
        }
    }

    private fun formatResponse(resp: DispatchResponse): String = buildString {
        append("ok=").append(resp.ok).append('\n')
        append("output_text=").append(resp.outputText).append('\n')
        if (resp.clientActions.isNotEmpty()) {
            append("client_actions=").append(resp.clientActions).append('\n')
        }
        resp.payload?.let { append("payload=").append(it).append('\n') }
    }
}
