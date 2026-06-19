package dev.voicepipe.zwangli

import android.os.Bundle
import android.widget.Button
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull

/**
 * In-app audio self-test. Pulls the hosted sample clips listed in
 * assets/test_manifest.json and runs each through the REAL
 * /transcribe-dispatch pipeline (server STT → routing), asserting that the
 * expected client_action came back. This is "mic injection" in the practical
 * sense — it feeds known audio bytes into the same upload the mic uses,
 * deterministically, without needing to speak.
 */
class TestActivity : AppCompatActivity() {
    private val client = DispatchClient()
    private lateinit var settings: Settings
    private lateinit var runButton: Button
    private lateinit var log: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_test)
        supportActionBar?.setTitle(R.string.test_title)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
        settings = Settings.from(this)
        runButton = findViewById(R.id.button_run_tests)
        log = findViewById(R.id.text_test_log)
        runButton.setOnClickListener { runAll() }
    }

    override fun onSupportNavigateUp(): Boolean {
        finish()
        return true
    }

    private fun runAll() {
        val manifest = try {
            loadManifest()
        } catch (e: Exception) {
            log.text = getString(R.string.test_manifest_error, e.message ?: "")
            return
        }
        val url = Settings.normalizeUrl(settings.serverUrl)
        val bearer = settings.token
        if (bearer.isBlank()) {
            log.text = getString(R.string.test_no_token)
            return
        }
        runButton.isEnabled = false
        log.text = "Running ${manifest.samples.size} audio samples…\n\n"
        lifecycleScope.launch {
            var passed = 0
            for (sample in manifest.samples) {
                log.append("▶ ${sample.label}\n")
                val result = runCatching {
                    withContext(Dispatchers.IO) {
                        val audio = client.fetchBytes(sample.url)
                        client.transcribeDispatch(
                            url, bearer, audio, ClientActions.CAPABILITIES, "clip.mp3",
                        )
                    }
                }
                result.fold(
                    onSuccess = { resp ->
                        val ok = matches(resp.clientActions, sample)
                        if (ok) passed++
                        log.append("  ${if (ok) "✓ PASS" else "✗ FAIL"} — heard: ${resp.transcript ?: ""}\n")
                        log.append("  actions: ${resp.clientActions}\n\n")
                    },
                    onFailure = { log.append("  ✗ ERROR: ${it.message}\n\n") },
                )
            }
            log.append("Done: $passed/${manifest.samples.size} passed.\n")
            runButton.isEnabled = true
        }
    }

    private fun loadManifest(): TestManifest =
        assets.open("test_manifest.json").use { input ->
            JSON.decodeFromString(TestManifest.serializer(), input.readBytes().decodeToString())
        }

    /** True if any returned client_action matches the sample's expected type
     *  (and action, for accessibility_global). Loose on other args, which can
     *  vary with STT. */
    private fun matches(actions: List<JsonElement>, sample: TestSample): Boolean =
        actions.any { el ->
            val obj = el as? JsonObject ?: return@any false
            val type = (obj["type"] as? JsonPrimitive)?.contentOrNull
            if (type != sample.expectType) return@any false
            val expectAction = sample.expectAction ?: return@any true
            (obj["action"] as? JsonPrimitive)?.contentOrNull == expectAction
        }

    companion object {
        private val JSON = Json { ignoreUnknownKeys = true }
    }
}
