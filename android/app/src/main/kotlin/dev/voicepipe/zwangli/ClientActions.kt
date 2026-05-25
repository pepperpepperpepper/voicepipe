package dev.voicepipe.zwangli

import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull

sealed class ClientAction {
    data class Clipboard(val text: String) : ClientAction()
    data class Feedback(val event: String) : ClientAction()
    data class Unknown(val type: String, val raw: JsonObject) : ClientAction()
}

object ClientActions {

    val CAPABILITIES: List<String> = listOf("clipboard", "audio_feedback")

    fun parseAll(actions: List<JsonElement>): List<ClientAction> =
        actions.mapNotNull(::parse)

    fun parse(element: JsonElement): ClientAction? {
        val obj = (element as? JsonObject) ?: return null
        val type = obj.stringField("type") ?: return null
        return when (type) {
            "clipboard" -> obj.stringField("text")?.let(ClientAction::Clipboard)
            "feedback" -> obj.stringField("event")
                ?.takeIf { it.isNotBlank() }
                ?.let(ClientAction::Feedback)
            else -> ClientAction.Unknown(type, obj)
        }
    }

    private fun JsonObject.stringField(name: String): String? {
        val prim = this[name] as? JsonPrimitive ?: return null
        if (!prim.isString) return null
        return prim.contentOrNull
    }
}
