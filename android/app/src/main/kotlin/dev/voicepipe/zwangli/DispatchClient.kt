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
        private val defaultJson = Json {
            ignoreUnknownKeys = true
            explicitNulls = false
        }

        fun defaultHttpClient(): OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .build()
    }
}
