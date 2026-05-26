package dev.voicepipe.zwangli

import java.io.IOException
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request

/** Probes a dispatch server to confirm the configured URL + token actually work.
 *
 *  Two calls in one round-trip:
 *    `GET /health`    — no auth; tells us the server is reachable + whether
 *                       it requires a bearer token at all.
 *    `GET /triggers`  — auth (if a token is configured); tells us which
 *                       verbs the server advertises so the configurator
 *                       can show what's actually available.
 *
 *  Returns a structured [Result] rather than raising — the UI wants to
 *  render every failure mode (unreachable, 401, malformed response)
 *  rather than show a stack trace.
 */
class ConnectionTester(
    private val httpClient: OkHttpClient = DispatchClient.defaultHttpClient(),
    private val json: Json = defaultJson,
) {

    data class Result(
        val healthOk: Boolean,
        val authRequired: Boolean? = null,
        val verbs: List<String>? = null,
        val triggersAuthFailed: Boolean = false,
        val error: String? = null,
    ) {
        val ok: Boolean get() = healthOk && error == null
    }

    fun test(rawUrl: String, token: String?): Result {
        val baseUrl = try {
            Settings.normalizeUrl(rawUrl).trimEnd('/').toHttpUrl()
        } catch (e: Exception) {
            return Result(healthOk = false, error = "Invalid URL: ${e.message}")
        }

        val healthUrl = baseUrl.newBuilder().addPathSegment("health").build()
        val healthBody = try {
            httpClient.newCall(Request.Builder().url(healthUrl).get().build())
                .execute().use { r ->
                    if (!r.isSuccessful) {
                        return Result(
                            healthOk = false,
                            error = "GET /health → HTTP ${r.code} ${r.message}",
                        )
                    }
                    r.body?.string().orEmpty()
                }
        } catch (e: IOException) {
            return Result(healthOk = false, error = "Cannot reach server: ${e.message}")
        }

        val authRequired = parseAuthRequired(healthBody)

        val triggersUrl = baseUrl.newBuilder().addPathSegment("triggers").build()
        val triggersReq = Request.Builder().url(triggersUrl).get().apply {
            if (!token.isNullOrBlank()) header("Authorization", "Bearer $token")
        }.build()
        val (verbs, authFailed, triggersError) = try {
            httpClient.newCall(triggersReq).execute().use { r ->
                when {
                    r.code == 401 || r.code == 403 ->
                        Triple<List<String>?, Boolean, String?>(null, true, null)
                    !r.isSuccessful ->
                        Triple<List<String>?, Boolean, String?>(
                            null, false,
                            "GET /triggers → HTTP ${r.code} ${r.message}",
                        )
                    else -> {
                        val body = r.body?.string().orEmpty()
                        Triple<List<String>?, Boolean, String?>(
                            parseVerbs(body), false, null,
                        )
                    }
                }
            }
        } catch (e: IOException) {
            Triple<List<String>?, Boolean, String?>(
                null, false, "Cannot fetch /triggers: ${e.message}"
            )
        }

        return Result(
            healthOk = true,
            authRequired = authRequired,
            verbs = verbs,
            triggersAuthFailed = authFailed,
            error = triggersError,
        )
    }

    private fun parseAuthRequired(body: String): Boolean? {
        if (body.isBlank()) return null
        return try {
            val obj = json.parseToJsonElement(body) as? JsonObject ?: return null
            val v = obj["auth_required"] ?: return null
            v.jsonPrimitive.boolean
        } catch (e: Exception) {
            null
        }
    }

    private fun parseVerbs(body: String): List<String>? {
        if (body.isBlank()) return null
        return try {
            val obj = json.parseToJsonElement(body) as? JsonObject ?: return null
            val verbsObj = (obj["verbs"] as? JsonObject) ?: return emptyList()
            verbsObj.entries
                .filter { (_, v) ->
                    val cfg = v as? JsonObject ?: return@filter false
                    cfg["enabled"]?.jsonPrimitive?.boolean != false
                }
                .map { it.key }
                .sorted()
        } catch (e: Exception) {
            null
        }
    }

    companion object {
        private val defaultJson = Json {
            ignoreUnknownKeys = true
            explicitNulls = false
        }
    }
}
