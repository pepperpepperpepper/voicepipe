package dev.voicepipe.zwangli

import android.util.Base64
import org.json.JSONObject

/**
 * Minimal, verification-free JWT helpers for the client side.
 *
 * The app does NOT trust these values for auth — the server fully verifies
 * the Google ID token (signature, aud, exp, email allowlist). The client only
 * peeks at the unverified `exp`/`email` claims to decide when to re-mint a
 * token before sending it, avoiding an obvious 401 round trip.
 */
object JwtUtil {

    /** Unix-seconds `exp` from the JWT payload, or null if absent/malformed. */
    fun expiryEpochSeconds(jwt: String): Long? = claimLong(jwt, "exp")

    /** The `email` claim, or null. */
    fun email(jwt: String): String? = claimString(jwt, "email")

    /**
     * True if the token is missing, malformed, or within [skewSeconds] of its
     * `exp`. Treats an unparseable/expiry-less token as expired (fail-safe →
     * re-mint). [nowEpochSeconds] is injected for testability.
     */
    fun isExpiredOrNearExpiry(
        jwt: String,
        nowEpochSeconds: Long,
        skewSeconds: Long = 300,
    ): Boolean {
        if (jwt.isBlank()) return true
        val exp = expiryEpochSeconds(jwt) ?: return true
        return nowEpochSeconds >= (exp - skewSeconds)
    }

    private fun payload(jwt: String): JSONObject? {
        val parts = jwt.split(".")
        if (parts.size < 2) return null
        return try {
            val bytes = Base64.decode(parts[1], Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING)
            JSONObject(String(bytes, Charsets.UTF_8))
        } catch (_: Exception) {
            null
        }
    }

    private fun claimLong(jwt: String, name: String): Long? {
        val obj = payload(jwt) ?: return null
        return if (obj.has(name)) obj.optLong(name).takeIf { it != 0L } else null
    }

    private fun claimString(jwt: String, name: String): String? {
        val obj = payload(jwt) ?: return null
        return obj.optString(name, "").takeIf { it.isNotBlank() }
    }
}
