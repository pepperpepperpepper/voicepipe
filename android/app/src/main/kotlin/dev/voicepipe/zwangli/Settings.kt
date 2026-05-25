package dev.voicepipe.zwangli

import android.content.Context
import android.content.SharedPreferences

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

    companion object {
        const val PREFS_NAME = "zwangli"
        const val KEY_SERVER_URL = "server_url"
        const val KEY_TOKEN = "token"
        const val KEY_START_ON_BOOT = "start_on_boot"
        const val DEFAULT_SERVER_URL = "http://localhost:8765"

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
    }
}
