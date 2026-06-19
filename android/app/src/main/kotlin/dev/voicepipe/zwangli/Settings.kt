package dev.voicepipe.zwangli

import android.content.Context
import android.content.SharedPreferences
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive

class Settings(private val prefs: SharedPreferences) {

    var serverUrl: String
        get() = prefs.getString(KEY_SERVER_URL, DEFAULT_SERVER_URL) ?: DEFAULT_SERVER_URL
        set(value) {
            prefs.edit().putString(KEY_SERVER_URL, normalizeUrl(value)).apply()
        }

    var token: String
        get() = prefs.getString(KEY_TOKEN, "") ?: ""
        set(value) {
            prefs.edit().putString(KEY_TOKEN, value.trim()).apply()
        }


    var startOnBoot: Boolean
        get() = prefs.getBoolean(KEY_START_ON_BOOT, false)
        set(value) {
            prefs.edit().putBoolean(KEY_START_ON_BOOT, value).apply()
        }

    /** Optional URL template used to override the system default search
     *  engine for voice-driven `web_search` actions. Empty string means
     *  "fall back to `ACTION_WEB_SEARCH` and let Android route to the
     *  user's chosen default." When set, must contain `{query}` which
     *  will be substituted with the URL-encoded query. Malformed values
     *  (missing `{query}`) are stored as-is but ignored at fire time —
     *  see [ClientActionExecutor.fireWebSearch]. */
    var searchUrlTemplate: String
        get() = prefs.getString(KEY_SEARCH_URL_TEMPLATE, "") ?: ""
        set(value) {
            prefs.edit().putString(KEY_SEARCH_URL_TEMPLATE, value.trim()).apply()
        }

    /** Recent transcripts, most-recent first. Capped at [TRANSCRIPT_HISTORY_MAX].
     *  Used by [MainActivity] to render a chip strip the user can tap to
     *  reload a prior transcript without re-dictating it. */
    val transcriptHistory: List<String>
        get() {
            val raw = prefs.getString(KEY_TRANSCRIPT_HISTORY, null) ?: return emptyList()
            return try {
                val arr = Json.parseToJsonElement(raw) as? JsonArray ?: return emptyList()
                arr.mapNotNull { it.jsonPrimitive.contentOrNull?.takeIf { s -> s.isNotBlank() } }
            } catch (_: Exception) {
                emptyList()
            }
        }

    /** Push [transcript] to the front of history (move-to-front semantics
     *  if it already exists) and trim to the cap. Blank transcripts are
     *  ignored. Returns the new history list. */
    fun recordTranscript(transcript: String): List<String> {
        val trimmed = transcript.trim()
        if (trimmed.isEmpty()) return transcriptHistory
        val current = transcriptHistory
        val deduped = current.filter { it != trimmed }
        val updated = (listOf(trimmed) + deduped).take(TRANSCRIPT_HISTORY_MAX)
        writeHistory(updated)
        return updated
    }

    /** Drop a single entry. Returns the new history list. */
    fun removeTranscriptHistoryEntry(transcript: String): List<String> {
        val updated = transcriptHistory.filter { it != transcript }
        writeHistory(updated)
        return updated
    }

    fun clearTranscriptHistory() {
        prefs.edit().remove(KEY_TRANSCRIPT_HISTORY).apply()
    }

    private fun writeHistory(entries: List<String>) {
        val rendered = buildJsonArray {
            entries.forEach { add(JsonPrimitive(it)) }
        }.toString()
        prefs.edit().putString(KEY_TRANSCRIPT_HISTORY, rendered).apply()
    }

    companion object {
        const val PREFS_NAME = "zwangli"
        const val KEY_SERVER_URL = "server_url"
        const val KEY_TOKEN = "token"
        const val KEY_START_ON_BOOT = "start_on_boot"
        const val KEY_SEARCH_URL_TEMPLATE = "search_url_template"
        const val KEY_TRANSCRIPT_HISTORY = "transcript_history"
        const val TRANSCRIPT_HISTORY_MAX = 20
        // Bundled default: the live AWS Lambda backend. A fresh install works
        // out of the box with no URL entry; the configurator field remains an
        // optional override (e.g. for a local dev server).
        const val DEFAULT_SERVER_URL = "https://s3ksuw6v2gahe3phqmwdswkiue0ehasy.lambda-url.us-east-1.on.aws"
        const val SEARCH_TEMPLATE_PLACEHOLDER = "{query}"

        fun from(context: Context): Settings =
            Settings(context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE))

        fun normalizeUrl(raw: String): String {
            val trimmed = raw.trim().removeSuffix("/")
            if (trimmed.isEmpty() || trimmed in SCHEME_ONLY) return DEFAULT_SERVER_URL
            return if (trimmed.startsWith("http://") || trimmed.startsWith("https://"))
                trimmed
            else
                "http://$trimmed"
        }

        private val SCHEME_ONLY = setOf("http:", "https:", "http:/", "https:/")

        fun isValidUrl(raw: String): Boolean {
            val trimmed = raw.trim().removeSuffix("/")
            if (trimmed.isEmpty() || trimmed in SCHEME_ONLY) return false
            val withScheme =
                if (trimmed.startsWith("http://") || trimmed.startsWith("https://"))
                    trimmed
                else
                    "http://$trimmed"
            return try {
                val url = java.net.URL(withScheme)
                !url.host.isNullOrBlank()
            } catch (_: java.net.MalformedURLException) {
                false
            }
        }

        /** A search URL template is valid iff:
         *  - it is empty (meaning "use system default search"), OR
         *  - it parses as an http(s) URL AND contains `{query}` so the
         *    user's query has somewhere to land. */
        fun isValidSearchUrlTemplate(raw: String): Boolean {
            val trimmed = raw.trim()
            if (trimmed.isEmpty()) return true
            if (!trimmed.contains(SEARCH_TEMPLATE_PLACEHOLDER)) return false
            // Substitute a sample query and try to URL-parse the result.
            val sample = trimmed.replace(SEARCH_TEMPLATE_PLACEHOLDER, "test")
            if (!sample.startsWith("http://") && !sample.startsWith("https://")) {
                return false
            }
            return try {
                java.net.URL(sample).host?.isNotBlank() == true
            } catch (_: java.net.MalformedURLException) {
                false
            }
        }
    }
}
