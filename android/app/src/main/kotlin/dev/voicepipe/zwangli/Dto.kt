package dev.voicepipe.zwangli

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject

@Serializable
data class DispatchRequest(
    val transcript: String,
    @SerialName("session_id") val sessionId: String? = null,
    val capabilities: List<String>? = null,
)

@Serializable
data class DispatchResponse(
    val ok: Boolean,
    @SerialName("output_text") val outputText: String,
    val payload: JsonObject? = null,
    @SerialName("client_actions") val clientActions: List<JsonElement> = emptyList(),
)
