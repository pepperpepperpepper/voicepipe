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
        // Absolute wall-clock time. Null when [inSeconds] is set (relative).
        val hour: Int?,
        val minutes: Int?,
        val message: String?,
        // Relative offset: set an alarm this many seconds from now. The
        // device resolves it to a local wall-clock time at fire time.
        val inSeconds: Int? = null,
    ) : ClientAction()
    data class SetTimer(
        val seconds: Int,
        val message: String?,
    ) : ClientAction()
    data class Dial(val number: String) : ClientAction()
    // Resolve a business/place name → number via /resolve-call, then dial.
    // Handled by MainActivity (async + status), not the synchronous executor.
    data class ResolveDial(val query: String) : ClientAction()
    // Reach a saved contact by name through WhatsApp / Signal / SMS. platform
    // ∈ {whatsapp, signal, sms}; mode ∈ {call, video, message}. Resolved
    // on-device (contact data row, or phone number for sms). Handled by
    // MainActivity (contact lookup + chooser), not the synchronous executor.
    data class ReachContact(
        val name: String,
        val platform: String,
        val mode: String,
        val body: String?,
    ) : ClientAction()
    // Launch a named app to its home screen; if [query] is set, copy it to the
    // clipboard first so the user can paste it into the app's own search.
    data class OpenApp(val app: String, val query: String?) : ClientAction()
    data class Navigate(
        val destination: String,
        val mode: String?,
    ) : ClientAction()
    data class AccessibilityGlobal(val action: String) : ClientAction()
    data class CalendarEvent(val title: String) : ClientAction()
    data class Email(
        val to: String?,
        val subject: String?,
        val body: String?,
    ) : ClientAction()
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
        "resolve_dial",
        "reach_contact",
        "open_app",
        "navigate",
        "accessibility_global",
        "calendar",
        "email",
    )

    val ACCESSIBILITY_GLOBAL_ACTIONS: Set<String> = setOf(
        "back",
        "home",
        "recents",
        "notifications",
        "quick_settings",
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
            "resolve_dial" -> obj.stringField("query")
                ?.takeIf { it.isNotBlank() }
                ?.let(ClientAction::ResolveDial)
            "reach_contact" -> parseReachContact(obj)
            "open_app" -> obj.stringField("app")
                ?.takeIf { it.isNotBlank() }
                ?.let { ClientAction.OpenApp(it, obj.stringField("query")?.takeIf { q -> q.isNotBlank() }) }
            "navigate" -> parseNavigate(obj)
            "accessibility_global" -> parseAccessibilityGlobal(obj)
            "calendar_event" -> obj.stringField("title")
                ?.takeIf { it.isNotBlank() }
                ?.let(ClientAction::CalendarEvent)
            "email" -> ClientAction.Email(
                to = obj.stringField("to")?.takeIf { it.isNotBlank() },
                subject = obj.stringField("subject")?.takeIf { it.isNotBlank() },
                body = obj.stringField("body")?.takeIf { it.isNotBlank() },
            )
            else -> ClientAction.Unknown(type, obj)
        }
    }

    private val REACH_PLATFORMS = setOf("whatsapp", "signal", "sms")
    private val REACH_MODES = setOf("call", "video", "message")

    private fun parseReachContact(obj: JsonObject): ClientAction.ReachContact? {
        val name = obj.stringField("name")?.takeIf { it.isNotBlank() } ?: return null
        val platform = obj.stringField("platform")?.takeIf { it in REACH_PLATFORMS } ?: return null
        val mode = obj.stringField("mode")?.takeIf { it in REACH_MODES } ?: return null
        val body = obj.stringField("body")?.takeIf { it.isNotBlank() }
        return ClientAction.ReachContact(name, platform, mode, body)
    }

    private fun parseSetAlarm(obj: JsonObject): ClientAction.SetAlarm? {
        val message = obj.stringField("message")?.takeIf { it.isNotBlank() }
        val inSeconds = obj.intField("in_seconds")
        if (inSeconds != null) {
            // Relative alarm: resolved to a wall-clock time on-device.
            if (inSeconds !in 1..MAX_TIMER_SECONDS) return null
            return ClientAction.SetAlarm(null, null, message, inSeconds)
        }
        val hour = obj.intField("hour") ?: return null
        val minutes = obj.intField("minutes") ?: return null
        if (hour !in 0..23 || minutes !in 0..59) return null
        return ClientAction.SetAlarm(hour, minutes, message)
    }

    private fun parseSetTimer(obj: JsonObject): ClientAction.SetTimer? {
        val seconds = obj.intField("seconds") ?: return null
        if (seconds !in 1..MAX_TIMER_SECONDS) return null
        val message = obj.stringField("message")?.takeIf { it.isNotBlank() }
        return ClientAction.SetTimer(seconds, message)
    }

    private fun parseNavigate(obj: JsonObject): ClientAction.Navigate? {
        val destination = obj.stringField("destination")
            ?.takeIf { it.isNotBlank() } ?: return null
        val mode = obj.stringField("mode")
            ?.takeIf { it in NAVIGATE_MODES }
        return ClientAction.Navigate(destination, mode)
    }

    private fun parseAccessibilityGlobal(
        obj: JsonObject,
    ): ClientAction.AccessibilityGlobal? {
        val action = obj.stringField("action")
            ?.takeIf { it in ACCESSIBILITY_GLOBAL_ACTIONS } ?: return null
        return ClientAction.AccessibilityGlobal(action)
    }

    private val NAVIGATE_MODES = setOf("driving", "walking", "bicycling", "transit")

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
