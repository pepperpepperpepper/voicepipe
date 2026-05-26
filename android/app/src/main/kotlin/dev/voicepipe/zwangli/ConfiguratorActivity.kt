package dev.voicepipe.zwangli

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.provider.Settings as AndroidSettings
import android.text.Editable
import android.text.TextWatcher
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.google.android.material.appbar.MaterialToolbar
import com.google.android.material.switchmaterial.SwitchMaterial
import com.google.android.material.textfield.TextInputLayout
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json

/** The "configurator" screen: permissions dashboard + server settings + a
 *  live connection test against the configured dispatch server.
 *
 *  Keeps [MainActivity] free to be a pure dictation test bench. All state
 *  is read from / written to [Settings] (SharedPreferences-backed) so the
 *  two screens never drift.
 */
class ConfiguratorActivity : AppCompatActivity() {

    private lateinit var settings: Settings
    private val tester = ConnectionTester()

    private lateinit var badgeAccessibility: TextView
    private lateinit var badgeMicrophone: TextView
    private lateinit var badgeNotifications: TextView
    private lateinit var badgeService: TextView
    private lateinit var badgeAlarm: TextView

    private lateinit var buttonAccessibility: Button
    private lateinit var buttonMicrophone: Button
    private lateinit var buttonNotifications: Button
    private lateinit var buttonService: Button
    private lateinit var cardNotifications: View
    private lateinit var switchStartOnBoot: SwitchMaterial

    private lateinit var editServerUrl: EditText
    private lateinit var editToken: EditText
    private lateinit var buttonTest: Button
    private lateinit var textTestResult: TextView
    private lateinit var editSearchTemplate: EditText
    private lateinit var layoutSearchTemplate: TextInputLayout
    private lateinit var buttonTrySearch: Button

    private val requestMicPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) {
            refreshAll()
        }
    private val requestNotificationPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) {
            refreshAll()
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_configurator)
        setSupportActionBar(findViewById<MaterialToolbar>(R.id.toolbar))
        supportActionBar?.setTitle(R.string.configurator_title)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)

        settings = Settings.from(this)
        bindViews()
        wireListeners()
        editServerUrl.setText(settings.serverUrl)
        editToken.setText(settings.token)
        editSearchTemplate.setText(settings.searchUrlTemplate)
        switchStartOnBoot.isChecked = settings.startOnBoot
        validateSearchTemplate(settings.searchUrlTemplate)

        // The notifications permission only exists on SDK 33+.
        cardNotifications.visibility =
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) View.VISIBLE
            else View.GONE
    }

    override fun onResume() {
        super.onResume()
        refreshAll()
    }

    override fun onSupportNavigateUp(): Boolean {
        finish()
        return true
    }

    private fun bindViews() {
        badgeAccessibility = findViewById(R.id.badge_accessibility)
        badgeMicrophone = findViewById(R.id.badge_microphone)
        badgeNotifications = findViewById(R.id.badge_notifications)
        badgeService = findViewById(R.id.badge_service)
        badgeAlarm = findViewById(R.id.badge_alarm)
        buttonAccessibility = findViewById(R.id.button_accessibility)
        buttonMicrophone = findViewById(R.id.button_microphone)
        buttonNotifications = findViewById(R.id.button_notifications)
        buttonService = findViewById(R.id.button_service)
        cardNotifications = findViewById(R.id.card_notifications)
        switchStartOnBoot = findViewById(R.id.switch_start_on_boot)
        editServerUrl = findViewById(R.id.edit_server_url)
        editToken = findViewById(R.id.edit_token)
        buttonTest = findViewById(R.id.button_test)
        textTestResult = findViewById(R.id.text_test_result)
        editSearchTemplate = findViewById(R.id.edit_search_template)
        layoutSearchTemplate = findViewById(R.id.layout_search_template)
        buttonTrySearch = findViewById(R.id.button_try_search)
    }

    private fun wireListeners() {
        buttonAccessibility.setOnClickListener {
            startActivity(Intent(AndroidSettings.ACTION_ACCESSIBILITY_SETTINGS))
        }
        buttonMicrophone.setOnClickListener {
            if (hasMicPermission()) openAppDetails()
            else requestMicPermission.launch(Manifest.permission.RECORD_AUDIO)
        }
        buttonNotifications.setOnClickListener {
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return@setOnClickListener
            if (hasNotificationsPermission()) openAppDetails()
            else requestNotificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
        buttonService.setOnClickListener {
            if (ZwangliForegroundService.isRunning()) {
                ZwangliForegroundService.stop(this)
            } else {
                ZwangliForegroundService.start(this)
            }
            // Refresh after the service has had a tick to flip state.
            buttonService.post { refreshAll() }
        }
        switchStartOnBoot.setOnCheckedChangeListener { _, checked ->
            settings.startOnBoot = checked
        }
        editServerUrl.addTextChangedListener(savingTextWatcher { settings.serverUrl = it })
        editToken.addTextChangedListener(savingTextWatcher { settings.token = it })
        editSearchTemplate.addTextChangedListener(
            savingTextWatcher {
                settings.searchUrlTemplate = it
                validateSearchTemplate(it)
            },
        )
        buttonTest.setOnClickListener { runConnectionTest() }
        buttonTrySearch.setOnClickListener { runSearchProbe() }
    }

    private fun validateSearchTemplate(value: String) {
        val valid = Settings.isValidSearchUrlTemplate(value)
        layoutSearchTemplate.error = if (valid) {
            null
        } else {
            getString(R.string.error_search_template_invalid)
        }
        buttonTrySearch.isEnabled = valid
    }

    /** Fires a sample web_search through the live [ClientActionExecutor]
     *  so the user sees exactly what their template (or the system
     *  default fallback) will do. Uses the same Settings-backed provider
     *  that runtime searches use — no separate code path. */
    private fun runSearchProbe() {
        val executor = ClientActionExecutor(applicationContext)
        val action = Json.parseToJsonElement(
            """{"type":"web_search","query":"${getString(R.string.try_search_query)}"}""",
        )
        val summary = executor.execute(listOf(action))
        if (summary.intentsFired == 0) {
            Toast.makeText(this, R.string.try_search_no_handler, Toast.LENGTH_LONG).show()
        }
    }

    private fun savingTextWatcher(save: (String) -> Unit): TextWatcher = object : TextWatcher {
        override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) = Unit
        override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) = Unit
        override fun afterTextChanged(s: Editable?) {
            save(s?.toString().orEmpty())
        }
    }

    private fun refreshAll() {
        refreshAccessibility()
        refreshMicrophone()
        refreshNotifications()
        refreshService()
        refreshAlarm()
    }

    private fun refreshAccessibility() {
        val ok = ZwangliAccessibilityService.isConnected()
        renderBadge(badgeAccessibility, ok)
        buttonAccessibility.visibility = if (ok) View.GONE else View.VISIBLE
    }

    private fun refreshMicrophone() {
        val ok = hasMicPermission()
        renderBadge(badgeMicrophone, ok)
        buttonMicrophone.text = getString(if (ok) R.string.action_revisit else R.string.action_grant)
    }

    private fun refreshNotifications() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return
        val ok = hasNotificationsPermission()
        renderBadge(badgeNotifications, ok)
        buttonNotifications.text =
            getString(if (ok) R.string.action_revisit else R.string.action_grant)
    }

    private fun refreshService() {
        val running = ZwangliForegroundService.isRunning()
        renderBadge(badgeService, running)
        buttonService.text = getString(
            if (running) R.string.action_service_disable
            else R.string.action_service_enable,
        )
    }

    private fun refreshAlarm() {
        // SET_ALARM is a normal-protection permission auto-granted at install
        // (declared in AndroidManifest.xml). If the system ever revokes it,
        // checkSelfPermission will surface that; otherwise it's always granted.
        val ok = ContextCompat.checkSelfPermission(this, "com.android.alarm.permission.SET_ALARM") ==
            PackageManager.PERMISSION_GRANTED
        renderBadge(badgeAlarm, ok)
    }

    private fun renderBadge(badge: TextView, ok: Boolean) {
        badge.text = getString(if (ok) R.string.badge_granted else R.string.badge_missing)
    }

    private fun hasMicPermission(): Boolean = ContextCompat.checkSelfPermission(
        this, Manifest.permission.RECORD_AUDIO,
    ) == PackageManager.PERMISSION_GRANTED

    private fun hasNotificationsPermission(): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
            ContextCompat.checkSelfPermission(
                this, Manifest.permission.POST_NOTIFICATIONS,
            ) == PackageManager.PERMISSION_GRANTED

    private fun openAppDetails() {
        startActivity(
            Intent(AndroidSettings.ACTION_APPLICATION_DETAILS_SETTINGS).apply {
                data = android.net.Uri.fromParts("package", packageName, null)
            },
        )
    }

    private fun runConnectionTest() {
        val url = editServerUrl.text.toString().trim()
        val token = editToken.text.toString()
        buttonTest.isEnabled = false
        textTestResult.text = getString(R.string.test_result_running)
        lifecycleScope.launch {
            val result = withContext(Dispatchers.IO) {
                tester.test(url, token)
            }
            textTestResult.text = renderTestResult(result)
            buttonTest.isEnabled = true
        }
    }

    private fun renderTestResult(r: ConnectionTester.Result): String = buildString {
        if (!r.healthOk) {
            append("✗ ")
            append(r.error ?: getString(R.string.test_result_unreachable))
            return@buildString
        }
        append("✓ /health: OK")
        when (r.authRequired) {
            true -> append(" (auth required)")
            false -> append(" (auth not required)")
            null -> Unit
        }
        append('\n')
        when {
            r.triggersAuthFailed -> append("✗ /triggers: 401 — token rejected")
            r.verbs == null && r.error != null -> append("✗ /triggers: ").append(r.error)
            r.verbs == null -> append("? /triggers: unparseable response")
            r.verbs.isEmpty() -> append("✓ /triggers: server has no verbs configured")
            else -> {
                append("✓ /triggers: ")
                append(r.verbs.size).append(" verbs — ")
                append(r.verbs.joinToString(", "))
            }
        }
    }
}
