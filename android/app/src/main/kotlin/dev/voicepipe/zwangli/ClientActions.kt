package dev.voicepipe.zwangli

import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull

sealed class ClientAction {
    data class Clipboard(val text: String) : ClientAction()
    data class Feedback(val event: String) : ClientAction()
    data class WebSearch(val query: String) : ClientAction()
    data class OpenUrl(val url: String) : ClientAction()
    data class SetAlarm(
        val hour: Int,
        val minutes: Int,
        val message: String?,
    ) : ClientAction()
    data class SetTimer(
        val seconds: Int,
        val message: String?,
    ) : ClientAction()
    data class Dial(val number: String) : ClientAction()
    data class Unknown(val type: String, val raw: JsonObject) : ClientAction()
}

object ClientActions {

    val CAPABILITIES: List<String> = listOf(
        "clipboard",
        "audio_feedback",
        "web_search",
        "open_url",
        "set_alarm",
        "set_timer",
        "dial",
    )

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
            "web_search" -> obj.stringField("query")
                ?.takeIf { it.isNotBlank() }
                ?.let(ClientAction::WebSearch)
            "open_url" -> obj.stringField("url")
                ?.takeIf { it.isNotBlank() }
                ?.let(ClientAction::OpenUrl)
            "set_alarm" -> parseSetAlarm(obj)
            "set_timer" -> parseSetTimer(obj)
            "dial" -> obj.stringField("number")
                ?.takeIf { it.isNotBlank() }
                ?.let(ClientAction::Dial)
            else -> ClientAction.Unknown(type, obj)
        }
    }

    private fun parseSetAlarm(obj: JsonObject): ClientAction.SetAlarm? {
        val hour = obj.intField("hour") ?: return null
        val minutes = obj.intField("minutes") ?: return null
        if (hour !in 0..23 || minutes !in 0..59) return null
        val message = obj.stringField("message")?.takeIf { it.isNotBlank() }
        return ClientAction.SetAlarm(hour, minutes, message)
    }

    private fun parseSetTimer(obj: JsonObject): ClientAction.SetTimer? {
        val seconds = obj.intField("seconds") ?: return null
        if (seconds !in 1..MAX_TIMER_SECONDS) return null
        val message = obj.stringField("message")?.takeIf { it.isNotBlank() }
        return ClientAction.SetTimer(seconds, message)
    }

    private const val MAX_TIMER_SECONDS = 86_400

    private fun JsonObject.stringField(name: String): String? {
        val prim = this[name] as? JsonPrimitive ?: return null
        if (!prim.isString) return null
        return prim.contentOrNull
    }

    private fun JsonObject.intField(name: String): Int? {
        val prim = this[name] as? JsonPrimitive ?: return null
        if (prim.isString) return null
        return prim.intOrNull
    }
}
