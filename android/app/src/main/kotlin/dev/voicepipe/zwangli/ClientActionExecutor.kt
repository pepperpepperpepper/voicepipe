package dev.voicepipe.zwangli

import android.accessibilityservice.AccessibilityService
import android.app.SearchManager
import android.content.ActivityNotFoundException
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.media.MediaPlayer
import android.net.Uri
import android.provider.AlarmClock
import android.provider.CalendarContract
import android.util.Log
import java.util.Calendar
import kotlinx.serialization.json.JsonElement

class ClientActionExecutor(
    private val context: Context,
    private val feedbackListener: FeedbackListener? = null,
    private val searchUrlTemplateProvider: () -> String? = {
        Settings.from(context).searchUrlTemplate.takeIf { it.isNotBlank() }
    },
) {

    interface FeedbackListener {
        fun onCompleted(event: String, success: Boolean)
    }

    fun execute(actions: List<JsonElement>): Summary {
        var clipboardCount = 0
        var feedbackCount = 0
        var intentsFired = 0
        var globalActionsFired = 0
        var unknownCount = 0
        for (action in ClientActions.parseAll(actions)) {
            when (action) {
                is ClientAction.Clipboard -> {
                    if (applyClipboard(action.text)) clipboardCount++
                }
                is ClientAction.Feedback -> {
                    if (playFeedback(action.event)) feedbackCount++
                }
                is ClientAction.WebSearch -> {
                    if (fireWebSearch(action.query)) intentsFired++
                }
                is ClientAction.OpenUrl -> {
                    if (fireOpenUrl(action.url)) intentsFired++
                }
                is ClientAction.SetAlarm -> {
                    if (fireSetAlarm(action)) {
                        intentsFired++
                    }
                }
                is ClientAction.SetTimer -> {
                    if (fireSetTimer(action.seconds, action.message)) intentsFired++
                }
                is ClientAction.Dial -> {
                    if (fireDial(action.number)) intentsFired++
                }
                is ClientAction.ResolveDial -> {
                    // Handled asynchronously by MainActivity (resolve → dial,
                    // with status). No-op here so it isn't counted as unknown.
                }
                is ClientAction.ReachContact -> {
                    // Handled by MainActivity (contact lookup + chooser, then
                    // fireContactCall/fireContactMessage/fireSms). No-op here.
                }
                is ClientAction.OpenApp -> {
                    if (fireOpenApp(action.app, action.query)) intentsFired++
                }
                is ClientAction.Navigate -> {
                    if (fireNavigate(action.destination, action.mode)) intentsFired++
                }
                is ClientAction.MapSearch -> {
                    if (fireMapSearch(action.query)) intentsFired++
                }
                is ClientAction.AccessibilityGlobal -> {
                    if (fireAccessibilityGlobal(action.action)) globalActionsFired++
                }
                is ClientAction.CalendarEvent -> {
                    if (fireCalendarEvent(action.title)) intentsFired++
                }
                is ClientAction.Email -> {
                    if (fireEmail(action.to, action.subject, action.body)) intentsFired++
                }
                is ClientAction.Unknown -> {
                    unknownCount++
                    Log.i(TAG, "Skipping unknown client_action type=${action.type}")
                }
            }
        }
        return Summary(
            clipboardCount,
            feedbackCount,
            intentsFired,
            globalActionsFired,
            unknownCount,
        )
    }

    data class Summary(
        val clipboardApplied: Int,
        val feedbackPlayed: Int,
        val intentsFired: Int,
        val globalActionsFired: Int,
        val unknownSkipped: Int,
    ) {
        fun anything(): Boolean =
            clipboardApplied + feedbackPlayed + intentsFired +
                globalActionsFired + unknownSkipped > 0
    }

    private fun applyClipboard(text: String): Boolean {
        val mgr = context.getSystemService(ClipboardManager::class.java) ?: return false
        return try {
            mgr.setPrimaryClip(ClipData.newPlainText("zwangli", text))
            true
        } catch (e: SecurityException) {
            Log.w(TAG, "Clipboard access denied", e)
            false
        }
    }

    private fun playFeedback(event: String): Boolean {
        val resId = FeedbackSounds.resourceFor(event) ?: return false
        return try {
            val player = MediaPlayer.create(context, resId) ?: run {
                feedbackListener?.onCompleted(event, false)
                return false
            }
            player.setOnCompletionListener {
                it.release()
                feedbackListener?.onCompleted(event, true)
            }
            player.setOnErrorListener { mp, _, _ ->
                mp.release()
                feedbackListener?.onCompleted(event, false)
                true
            }
            player.start()
            true
        } catch (e: Exception) {
            Log.w(TAG, "Feedback playback failed for event=$event", e)
            feedbackListener?.onCompleted(event, false)
            false
        }
    }

    private fun fireWebSearch(query: String): Boolean {
        val template = searchUrlTemplateProvider()
        if (!template.isNullOrBlank() &&
            template.contains(Settings.SEARCH_TEMPLATE_PLACEHOLDER)
        ) {
            val url = template.replace(
                Settings.SEARCH_TEMPLATE_PLACEHOLDER,
                Uri.encode(query),
            )
            val uri = try {
                Uri.parse(url)
            } catch (e: Exception) {
                Log.w(TAG, "web_search: cannot parse override url '$url'", e)
                return fireSystemSearch(query)
            }
            return fireIntent(Intent(Intent.ACTION_VIEW, uri), "web_search")
        }
        return fireSystemSearch(query)
    }

    private fun fireSystemSearch(query: String): Boolean = fireIntent(
        Intent(Intent.ACTION_WEB_SEARCH).putExtra(SearchManager.QUERY, query),
        "web_search",
    )

    private fun fireOpenUrl(url: String): Boolean {
        val uri = try {
            Uri.parse(url)
        } catch (e: Exception) {
            Log.w(TAG, "open_url: cannot parse url '$url'", e)
            return false
        }
        return fireIntent(Intent(Intent.ACTION_VIEW, uri), "open_url")
    }

    private fun fireSetAlarm(action: ClientAction.SetAlarm): Boolean {
        // Resolve a relative offset ("in 30 seconds") to a local wall-clock
        // time here on the device, where the timezone is known. Absolute
        // alarms use the hour/minutes the server already computed.
        val (hour, minutes) = if (action.inSeconds != null) {
            relativeAlarmHourMinute(Calendar.getInstance(), action.inSeconds)
        } else {
            (action.hour ?: return false) to (action.minutes ?: return false)
        }
        val intent = Intent(AlarmClock.ACTION_SET_ALARM).apply {
            putExtra(AlarmClock.EXTRA_HOUR, hour)
            putExtra(AlarmClock.EXTRA_MINUTES, minutes)
            if (!action.message.isNullOrBlank()) {
                putExtra(AlarmClock.EXTRA_MESSAGE, action.message)
            }
        }
        return fireIntent(intent, "set_alarm")
    }

    private fun fireSetTimer(seconds: Int, message: String?): Boolean {
        val intent = Intent(AlarmClock.ACTION_SET_TIMER).apply {
            putExtra(AlarmClock.EXTRA_LENGTH, seconds)
            if (!message.isNullOrBlank()) putExtra(AlarmClock.EXTRA_MESSAGE, message)
        }
        return fireIntent(intent, "set_timer")
    }

    private fun fireCalendarEvent(title: String): Boolean {
        // ACTION_INSERT opens the calendar app's new-event screen pre-filled
        // with the title (Google Calendar on a GMS device); the user picks the
        // time. No WRITE_CALENDAR permission needed.
        val intent = Intent(Intent.ACTION_INSERT)
            .setData(CalendarContract.Events.CONTENT_URI)
            .putExtra(CalendarContract.Events.TITLE, title)
        return fireIntent(intent, "calendar_event")
    }

    private fun fireEmail(to: String?, subject: String?, body: String?): Boolean {
        // ACTION_SENDTO + mailto: targets email apps only and opens the
        // composer pre-filled. The user picks the From account and sends.
        // If `to` is a spoken NAME (not an address), resolve it against the
        // device Contacts so the mail is actually addressed; fall back to the
        // raw name (Gmail will let the user fix it) if there's no match or no
        // READ_CONTACTS permission.
        val recipient = to?.trim()?.takeIf { it.isNotBlank() }?.let { name ->
            if (ContactResolver.looksLikeEmail(name)) name
            else ContactResolver.emailForName(context, name) ?: name
        }
        val intent = Intent(Intent.ACTION_SENDTO, Uri.parse("mailto:")).apply {
            if (recipient != null) putExtra(Intent.EXTRA_EMAIL, arrayOf(recipient))
            if (!subject.isNullOrBlank()) putExtra(Intent.EXTRA_SUBJECT, subject)
            if (!body.isNullOrBlank()) putExtra(Intent.EXTRA_TEXT, body)
        }
        return fireIntent(intent, "email")
    }

    private fun fireDial(number: String): Boolean {
        val uri = try {
            Uri.fromParts("tel", number, null)
        } catch (e: Exception) {
            Log.w(TAG, "dial: cannot build tel uri for '$number'", e)
            return false
        }
        return fireIntent(Intent(Intent.ACTION_DIAL, uri), "dial")
    }

    /**
     * Open a contact's WhatsApp/Signal action — a voice call, video call, or
     * chat — by ACTION_VIEW'ing the contact-data row [rowId] with the app's
     * registered MIME type. Both apps publish per-contact data rows
     * (vnd.com.whatsapp.* / vnd.org.thoughtcrime.securesms.*) whose row is the
     * deep link into that action. Resolution of name → rowId happens in
     * MainActivity (ContactResolver); this just fires the intent.
     */
    fun fireContactDataRow(rowId: Long, mimeType: String): Boolean {
        val uri = android.content.ContentUris.withAppendedId(
            android.provider.ContactsContract.Data.CONTENT_URI,
            rowId,
        )
        val intent = Intent(Intent.ACTION_VIEW).setDataAndType(uri, mimeType)
        return fireIntent(intent, "reach_contact:$mimeType")
    }

    /**
     * Open the SMS composer to [number], pre-filled with [body] if present.
     * ACTION_SENDTO + smsto: targets messaging apps only (no chooser noise),
     * and the user taps send.
     */
    fun fireSms(number: String, body: String?): Boolean {
        val uri = Uri.fromParts("smsto", number, null)
        val intent = Intent(Intent.ACTION_SENDTO, uri).apply {
            if (!body.isNullOrBlank()) putExtra("sms_body", body)
        }
        return fireIntent(intent, "sms")
    }

    /**
     * Launch a named app to its home screen. If [query] is given, copy it to
     * the clipboard first (with a toast hint) so the user can paste it into
     * the app's own search — WhatsApp/WeChat/etc. expose no external search
     * deep link. Returns false if the app isn't installed / not resolvable.
     */
    private fun fireOpenApp(app: String, query: String?): Boolean {
        val pkg = APP_PACKAGES[app.trim().lowercase()] ?: run {
            Log.w(TAG, "open_app: unknown app '$app'")
            return false
        }
        val launch = context.packageManager.getLaunchIntentForPackage(pkg) ?: run {
            Log.w(TAG, "open_app: '$app' ($pkg) not installed")
            return false
        }
        if (!query.isNullOrBlank()) {
            applyClipboard(query)
            android.widget.Toast.makeText(
                context,
                context.getString(R.string.open_app_query_copied, query),
                android.widget.Toast.LENGTH_LONG,
            ).show()
        }
        launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        return try {
            context.startActivity(launch)
            true
        } catch (e: ActivityNotFoundException) {
            Log.w(TAG, "open_app: launch failed for '$app'", e)
            false
        }
    }

    private fun fireAccessibilityGlobal(action: String): Boolean {
        val actionId = when (action) {
            "back" -> AccessibilityService.GLOBAL_ACTION_BACK
            "home" -> AccessibilityService.GLOBAL_ACTION_HOME
            "recents" -> AccessibilityService.GLOBAL_ACTION_RECENTS
            "notifications" -> AccessibilityService.GLOBAL_ACTION_NOTIFICATIONS
            "quick_settings" -> AccessibilityService.GLOBAL_ACTION_QUICK_SETTINGS
            else -> {
                Log.w(TAG, "accessibility_global: unknown action '$action'")
                return false
            }
        }
        if (!ZwangliAccessibilityService.isConnected()) {
            Log.w(
                TAG,
                "accessibility_global '$action' skipped — service not connected",
            )
            return false
        }
        val ok = ZwangliAccessibilityService.performGlobal(actionId)
        if (!ok) {
            Log.w(TAG, "accessibility_global: performGlobal returned false for '$action'")
        }
        return ok
    }

    private fun fireNavigate(destination: String, mode: String?): Boolean {
        // Prefer google.navigation: — Google Maps takes this and starts
        // turn-by-turn immediately. Fall back to geo:0,0?q=… which any
        // maps app (incl. OsmAnd, Organic Maps) can handle but just opens
        // the destination without auto-routing.
        val encoded = Uri.encode(destination)
        val modeChar = when (mode) {
            "driving" -> "d"
            "walking" -> "w"
            "bicycling" -> "b"
            "transit" -> "r"
            else -> null
        }
        val navUri = buildString {
            append("google.navigation:q=")
            append(encoded)
            if (modeChar != null) append("&mode=").append(modeChar)
        }
        val navIntent = Intent(Intent.ACTION_VIEW, Uri.parse(navUri))
        if (fireIntent(navIntent, "navigate")) return true
        val geoIntent = Intent(Intent.ACTION_VIEW, Uri.parse("geo:0,0?q=$encoded"))
        return fireIntent(geoIntent, "navigate")
    }

    private fun fireMapSearch(query: String): Boolean {
        // geo:0,0?q=<query> opens a maps app with a SEARCH for the query near
        // the current location (a list of results), as opposed to
        // google.navigation: which starts turn-by-turn to one place. Fall back
        // to the Google Maps web search URL if no geo: handler is installed.
        val encoded = Uri.encode(query)
        val geo = Intent(Intent.ACTION_VIEW, Uri.parse("geo:0,0?q=$encoded"))
        if (fireIntent(geo, "map_search")) return true
        val web = Intent(
            Intent.ACTION_VIEW,
            Uri.parse("https://www.google.com/maps/search/?api=1&query=$encoded"),
        )
        return fireIntent(web, "map_search")
    }

    private fun fireIntent(intent: Intent, label: String): Boolean {
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        // NB: do NOT pre-check intent.resolveActivity() here. On Android 11+
        // (API 30) package visibility makes resolveActivity()/queryIntentActivities()
        // return null for implicit intents (mailto:, ACTION_VIEW, …) unless the
        // app declares matching <queries> — even when a handler is installed.
        // startActivity() itself is NOT restricted, so we just launch and let
        // ActivityNotFoundException report the genuine "no app installed" case.
        return try {
            context.startActivity(intent)
            true
        } catch (e: ActivityNotFoundException) {
            Log.w(TAG, "ActivityNotFoundException firing $label", e)
            false
        } catch (e: SecurityException) {
            Log.w(TAG, "SecurityException firing $label", e)
            false
        }
    }

    companion object {
        private const val TAG = "ClientActionExecutor"

        /**
         * Canonical app token (as normalized by the server's open_app handler)
         * → Android package name. Used to launch a named app. Apps not listed
         * here can't be opened by name (fireOpenApp returns false).
         */
        private val APP_PACKAGES: Map<String, String> = mapOf(
            "whatsapp" to "com.whatsapp",
            "wechat" to "com.tencent.mm",
            "signal" to "org.thoughtcrime.securesms",
            "telegram" to "org.telegram.messenger",
            "instagram" to "com.instagram.android",
            "messenger" to "com.facebook.orca",
            "facebook" to "com.facebook.katana",
            "twitter" to "com.twitter.android",
            "snapchat" to "com.snapchat.android",
            "discord" to "com.discord",
            "slack" to "com.Slack",
            "viber" to "com.viber.voip",
            "line" to "jp.naver.line.android",
        )

        /**
         * Wall-clock (hour, minute) for a relative alarm offset.
         *
         * ACTION_SET_ALARM is minute-granular and fires at hh:mm:00, so if we
         * naively truncated `now + inSeconds` to its minute, any sub-minute
         * remainder would put that minute boundary in the *past* and the clock
         * app would schedule the alarm for TOMORROW (the "30 seconds from now →
         * tomorrow" bug). Round UP to the next whole minute whenever there's a
         * remainder, so a relative alarm always fires in the near future.
         */
        internal fun relativeAlarmHourMinute(now: Calendar, inSeconds: Int): Pair<Int, Int> {
            val target = (now.clone() as Calendar).apply { add(Calendar.SECOND, inSeconds) }
            if (target.get(Calendar.SECOND) > 0 || target.get(Calendar.MILLISECOND) > 0) {
                target.add(Calendar.MINUTE, 1)
                target.set(Calendar.SECOND, 0)
                target.set(Calendar.MILLISECOND, 0)
            }
            return target.get(Calendar.HOUR_OF_DAY) to target.get(Calendar.MINUTE)
        }
    }
}
