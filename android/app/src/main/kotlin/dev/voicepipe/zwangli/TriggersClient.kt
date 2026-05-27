package dev.voicepipe.zwangli

import java.io.IOException
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.add
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

/** Reads and mutates the dispatch server's activation-phrase set.
 *
 *  Wraps the server's `GET /triggers` (to populate the configurator's
 *  chip group) and `PATCH /triggers` (to add/remove phrases). Every
 *  outcome is a [ListResult] or [PatchResult] variant — the UI binds
 *  to the variant rather than chasing exceptions, so error states like
 *  "auth failed" / "would brick dispatcher" / "phrase rejected" can be
 *  surfaced as inline messages instead of toast spam.
 */
class TriggersClient(
    private val httpClient: OkHttpClient = DispatchClient.defaultHttpClient(),
    private val json: Json = defaultJson,
) {

    sealed class ListResult {
        data class Success(val triggers: List<String>) : ListResult()
        data class AuthFailed(val message: String) : ListResult()
        data class Error(val message: String) : ListResult()
    }

    sealed class PatchResult {
        data class Success(val triggers: List<String>) : PatchResult()

        /** Server rejected one or more phrases for failing validation. */
        data class InvalidPhrase(val failures: List<Failure>) : PatchResult() {
            data class Failure(val phrase: String, val reason: String)
        }

        /** A phrase appeared in both `add` and `remove`. */
        data class Conflict(val overlapping: List<String>) : PatchResult()

        /** Patch would leave zero triggers — server refused. */
        object WouldRemoveAll : PatchResult()

        data class AuthFailed(val message: String) : PatchResult()
        data class ServerError(val message: String) : PatchResult()
    }

    fun list(rawUrl: String, token: String?): ListResult {
        val triggersUrl = try {
            buildBaseUrl(rawUrl).newBuilder().addPathSegment("triggers").build()
        } catch (e: Exception) {
            return ListResult.Error("Invalid URL: ${e.message}")
        }
        val req = Request.Builder().url(triggersUrl).get().apply {
            if (!token.isNullOrBlank()) header("Authorization", "Bearer $token")
        }.build()
        return try {
            httpClient.newCall(req).execute().use { r ->
                when {
                    r.code == 401 || r.code == 403 ->
                        ListResult.AuthFailed("HTTP ${r.code}: bearer token rejected")
                    !r.isSuccessful ->
                        ListResult.Error("GET /triggers → HTTP ${r.code} ${r.message}")
                    else -> {
                        val body = r.body?.string().orEmpty()
                        val parsed = parseTriggers(body)
                            ?: return@use ListResult.Error("Malformed /triggers response")
                        ListResult.Success(parsed)
                    }
                }
            }
        } catch (e: IOException) {
            ListResult.Error("Cannot reach server: ${e.message}")
        }
    }

    fun patch(
        rawUrl: String,
        token: String?,
        add: List<String> = emptyList(),
        remove: List<String> = emptyList(),
    ): PatchResult {
        val triggersUrl = try {
            buildBaseUrl(rawUrl).newBuilder().addPathSegment("triggers").build()
        } catch (e: Exception) {
            return PatchResult.ServerError("Invalid URL: ${e.message}")
        }
        val body = buildJsonObject {
            put("add", buildJsonArray { add.forEach { add(it) } })
            put("remove", buildJsonArray { remove.forEach { add(it) } })
        }.toString().toRequestBody(JSON_MEDIA_TYPE)
        val req = Request.Builder().url(triggersUrl).patch(body).apply {
            if (!token.isNullOrBlank()) header("Authorization", "Bearer $token")
        }.build()

        return try {
            httpClient.newCall(req).execute().use { r ->
                val text = r.body?.string().orEmpty()
                when {
                    r.code == 401 || r.code == 403 ->
                        PatchResult.AuthFailed("HTTP ${r.code}: bearer token rejected")
                    r.code == 400 -> parse400(text)
                        ?: PatchResult.ServerError("HTTP 400: ${truncate(text)}")
                    r.code == 409 -> parse409(text)
                        ?: PatchResult.ServerError("HTTP 409: ${truncate(text)}")
                    !r.isSuccessful ->
                        PatchResult.ServerError("HTTP ${r.code} ${r.message}: ${truncate(text)}")
                    else -> {
                        val parsed = parsePatchSuccess(text)
                            ?: return@use PatchResult.ServerError("Malformed PATCH response")
                        PatchResult.Success(parsed)
                    }
                }
            }
        } catch (e: IOException) {
            PatchResult.ServerError("Cannot reach server: ${e.message}")
        }
    }

    private fun buildBaseUrl(rawUrl: String) =
        Settings.normalizeUrl(rawUrl).trimEnd('/').toHttpUrl()

    private fun parseTriggers(body: String): List<String>? {
        if (body.isBlank()) return null
        return try {
            val obj = json.parseToJsonElement(body) as? JsonObject ?: return null
            val triggers = (obj["triggers"] as? JsonObject) ?: return emptyList()
            triggers.keys.sorted()
        } catch (e: Exception) {
            null
        }
    }

    private fun parsePatchSuccess(body: String): List<String>? {
        if (body.isBlank()) return null
        return try {
            val obj = json.parseToJsonElement(body) as? JsonObject ?: return null
            val arr = (obj["triggers"] as? JsonArray) ?: return null
            arr.map { it.jsonPrimitive.contentOrNull.orEmpty() }
        } catch (e: Exception) {
            null
        }
    }

    /** Parse the 400 error envelope into the matching PatchResult variant. */
    private fun parse400(body: String): PatchResult? {
        val detail = parseDetail(body) ?: return null
        return when (detail["error"]?.jsonPrimitive?.contentOrNull) {
            "invalid_phrase" -> {
                val failures = (detail["failures"] as? JsonArray).orEmpty().mapNotNull { el ->
                    val obj = el as? JsonObject ?: return@mapNotNull null
                    val phrase = obj["phrase"]?.jsonPrimitive?.contentOrNull ?: return@mapNotNull null
                    val reason = obj["reason"]?.jsonPrimitive?.contentOrNull ?: "invalid"
                    PatchResult.InvalidPhrase.Failure(phrase, reason)
                }
                PatchResult.InvalidPhrase(failures)
            }
            "conflict" -> {
                val overlapping = (detail["overlapping"] as? JsonArray).orEmpty()
                    .mapNotNull { it.jsonPrimitive.contentOrNull }
                PatchResult.Conflict(overlapping)
            }
            else -> null
        }
    }

    private fun parse409(body: String): PatchResult? {
        val detail = parseDetail(body) ?: return null
        return if (detail["error"]?.jsonPrimitive?.contentOrNull == "would_remove_all_triggers")
            PatchResult.WouldRemoveAll
        else null
    }

    private fun parseDetail(body: String): JsonObject? = try {
        val obj = json.parseToJsonElement(body) as? JsonObject
        obj?.get("detail") as? JsonObject
    } catch (e: Exception) {
        null
    }

    private fun JsonArray?.orEmpty(): List<kotlinx.serialization.json.JsonElement> =
        this ?: emptyList()

    private fun truncate(s: String, max: Int = 200): String =
        if (s.length <= max) s else s.take(max - 1) + "…"

    companion object {
        private val JSON_MEDIA_TYPE = "application/json; charset=utf-8".toMediaType()
        private val defaultJson = Json {
            ignoreUnknownKeys = true
            explicitNulls = false
        }
    }
}
