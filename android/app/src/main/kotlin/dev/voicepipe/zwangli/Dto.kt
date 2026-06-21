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
data class ResolveCallRequest(
    val query: String,
)

@Serializable
data class CallCandidate(
    val name: String? = null,
    val phone: String,
    val address: String? = null,
)

@Serializable
data class ResolveCallResponse(
    val ok: Boolean,
    val number: String? = null,
    val name: String? = null,
    val address: String? = null,
    val candidates: List<CallCandidate> = emptyList(),
    val error: String? = null,
)

@Serializable
data class DispatchResponse(
    val ok: Boolean,
    @SerialName("output_text") val outputText: String,
    val payload: JsonObject? = null,
    @SerialName("client_actions") val clientActions: List<JsonElement> = emptyList(),
    // Populated by /transcribe-dispatch (server-side STT) so the client can
    // show what was heard; null on the text-in /dispatch path.
    val transcript: String? = null,
)
