package dev.voicepipe.zwangli

import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlinx.serialization.json.Json
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

class DispatchClient(
    private val httpClient: OkHttpClient = defaultHttpClient(),
    private val json: Json = defaultJson,
) {
    fun dispatch(
        baseUrl: String,
        token: String?,
        request: DispatchRequest,
    ): DispatchResponse {
        val url = baseUrl.trimEnd('/').toHttpUrl().newBuilder()
            .addPathSegment("dispatch")
            .build()
        val body = json.encodeToString(DispatchRequest.serializer(), request)
            .toRequestBody(JSON_MEDIA_TYPE)
        return execute(url, token, body)
    }

    /**
     * Upload a recorded audio clip to `/transcribe-dispatch`: the server runs
     * STT (Groq Whisper) then the same dispatcher as [dispatch], returning the
     * transcript plus client_actions. Metadata rides as query params since the
     * body is the raw audio.
     */
    fun transcribeDispatch(
        baseUrl: String,
        token: String?,
        audio: ByteArray,
        capabilities: List<String>? = null,
        filename: String = "clip.wav",
    ): DispatchResponse {
        val urlBuilder = baseUrl.trimEnd('/').toHttpUrl().newBuilder()
            .addPathSegment("transcribe-dispatch")
            .addQueryParameter("filename", filename)
        if (!capabilities.isNullOrEmpty()) {
            urlBuilder.addQueryParameter("capabilities", capabilities.joinToString(","))
        }
        val body = audio.toRequestBody(OCTET_STREAM_MEDIA_TYPE)
        return execute(urlBuilder.build(), token, body)
    }

    /**
     * Resolve a business/place name to a phone number via the server's
     * `/resolve-call` (which calls Serper). Used by the two-step "call" flow.
     */
    fun resolveCall(
        baseUrl: String,
        token: String?,
        query: String,
    ): ResolveCallResponse {
        val url = baseUrl.trimEnd('/').toHttpUrl().newBuilder()
            .addPathSegment("resolve-call")
            .build()
        val body = json.encodeToString(ResolveCallRequest.serializer(), ResolveCallRequest(query))
            .toRequestBody(JSON_MEDIA_TYPE)
        val builder = Request.Builder().url(url).post(body)
        if (!token.isNullOrBlank()) builder.header("Authorization", "Bearer $token")
        httpClient.newCall(builder.build()).execute().use { response ->
            val responseBody = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                throw IOException("HTTP ${response.code}: ${response.message}")
            }
            return json.decodeFromString(ResolveCallResponse.serializer(), responseBody)
        }
    }

    /** GET raw bytes from a URL (e.g. a hosted audio test sample). */
    fun fetchBytes(url: String): ByteArray {
        val request = Request.Builder().url(url).get().build()
        httpClient.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                throw IOException("HTTP ${response.code} fetching $url")
            }
            return response.body?.bytes() ?: throw IOException("empty body fetching $url")
        }
    }

    private fun execute(url: okhttp3.HttpUrl, token: String?, body: okhttp3.RequestBody): DispatchResponse {
        val builder = Request.Builder().url(url).post(body)
        if (!token.isNullOrBlank()) {
            builder.header("Authorization", "Bearer $token")
        }
        httpClient.newCall(builder.build()).execute().use { response ->
            val responseBody = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                throw IOException(
                    "HTTP ${response.code}: ${response.message} (${responseBody.take(200)})",
                )
            }
            return json.decodeFromString(DispatchResponse.serializer(), responseBody)
        }
    }

    companion object {
        private val JSON_MEDIA_TYPE = "application/json; charset=utf-8".toMediaType()
        private val OCTET_STREAM_MEDIA_TYPE = "application/octet-stream".toMediaType()
        private val defaultJson = Json {
            ignoreUnknownKeys = true
            explicitNulls = false
        }

        fun defaultHttpClient(): OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            // STT + LLM round trip (plus a possible Lambda cold start) can run
            // several seconds; keep generous headroom over the warm ~1s case.
            .readTimeout(60, TimeUnit.SECONDS)
            .build()
    }
}
