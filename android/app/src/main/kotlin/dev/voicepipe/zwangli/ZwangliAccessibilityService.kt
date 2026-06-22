package dev.voicepipe.zwangli

import android.accessibilityservice.AccessibilityService
import android.content.Context
import android.media.AudioManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.KeyEvent
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo

class ZwangliAccessibilityService : AccessibilityService() {

    // Push-to-talk on the volume rocker. We consume EVERY volume press and wait
    // a short window to see if the other key joins:
    //   • both keys down  → record (release Volume-Down → send, Volume-Up → cancel)
    //   • single key only → re-emit a normal volume change ourselves
    // This is the only way to suppress the volume jump the combo would otherwise
    // cause, at the cost of a ~140ms delay on normal volume presses.
    private val handler = Handler(Looper.getMainLooper())
    private var volUp = false
    private var volDown = false
    private var pttActive = false
    private var consumeTrailingUp = false
    private var pendingVolumeKey = 0   // key awaiting the combo-window decision
    private var pendingRunnable: Runnable? = null
    private var adjustingVolumeKey = 0 // key committed to as a volume change

    override fun onKeyEvent(event: KeyEvent): Boolean {
        val code = event.keyCode
        if (code != KeyEvent.KEYCODE_VOLUME_UP && code != KeyEvent.KEYCODE_VOLUME_DOWN) {
            return false
        }
        when (event.action) {
            KeyEvent.ACTION_DOWN -> {
                if (code == KeyEvent.KEYCODE_VOLUME_UP) volUp = true else volDown = true
                if (pttActive) return true // swallow auto-repeat while talking
                if (volUp && volDown) { startPtt(); return true }
                // Auto-repeat after we've committed to a volume change → keep adjusting.
                if (adjustingVolumeKey == code) { adjustVolume(code); return true }
                // First press of a single key → start the combo window.
                if (event.repeatCount == 0 && pendingVolumeKey == 0) {
                    pendingVolumeKey = code
                    val r = Runnable {
                        if (!pttActive && pendingVolumeKey == code) {
                            pendingVolumeKey = 0
                            pendingRunnable = null
                            adjustingVolumeKey = code
                            adjustVolume(code) // window elapsed → real volume change
                        }
                    }
                    pendingRunnable = r
                    handler.postDelayed(r, COMBO_WINDOW_MS)
                }
                return true // consume → suppress the immediate system volume change
            }
            KeyEvent.ACTION_UP -> {
                val wasPtt = pttActive
                if (code == KeyEvent.KEYCODE_VOLUME_UP) volUp = false else volDown = false
                if (wasPtt) {
                    pttActive = false
                    consumeTrailingUp = true
                    // Released Volume-Down first → send; Volume-Up first → cancel.
                    Ptt.stop(send = code == KeyEvent.KEYCODE_VOLUME_DOWN)
                    return true
                }
                if (consumeTrailingUp) {
                    if (!volUp && !volDown) consumeTrailingUp = false
                    return true
                }
                if (adjustingVolumeKey == code) {
                    adjustingVolumeKey = 0
                    return true
                }
                if (pendingVolumeKey == code) {
                    // Quick tap (released before the window) → adjust once now.
                    cancelPendingVolume()
                    adjustVolume(code)
                    return true
                }
                return true
            }
        }
        return false
    }

    private fun startPtt() {
        cancelPendingVolume()
        adjustingVolumeKey = 0
        pttActive = true
        Ptt.start(this)
    }

    private fun cancelPendingVolume() {
        pendingRunnable?.let { handler.removeCallbacks(it) }
        pendingRunnable = null
        pendingVolumeKey = 0
    }

    private fun adjustVolume(keyCode: Int) {
        val dir = if (keyCode == KeyEvent.KEYCODE_VOLUME_UP) {
            AudioManager.ADJUST_RAISE
        } else {
            AudioManager.ADJUST_LOWER
        }
        // adjustVolume() targets the system's context-relevant stream (music,
        // call, ring, …) — closer to native behavior than a hardcoded stream.
        (getSystemService(Context.AUDIO_SERVICE) as AudioManager)
            .adjustVolume(dir, AudioManager.FLAG_SHOW_UI)
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        // No-op: we don't react to events; we only use this service as a
        // typing actuator invoked by MainActivity via the static handle.
    }

    override fun onInterrupt() {
        instance = null
    }

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
    }

    override fun onUnbind(intent: android.content.Intent?): Boolean {
        cancelPendingVolume()
        instance = null
        return super.onUnbind(intent)
    }

    fun typeIntoFocusedField(text: String): Boolean {
        val target = findEditableFocus() ?: return false
        val args = Bundle().apply {
            putCharSequence(
                AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,
                text,
            )
        }
        return target.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
    }

    fun performGlobal(actionId: Int): Boolean = performGlobalAction(actionId)

    private fun findEditableFocus(): AccessibilityNodeInfo? {
        // Prefer an input-focused field; fall back to the accessibility focus.
        val input = findFocus(AccessibilityNodeInfo.FOCUS_INPUT)
        if (input != null && input.isEditable) return input
        val accessibility = findFocus(AccessibilityNodeInfo.FOCUS_ACCESSIBILITY)
        if (accessibility != null && accessibility.isEditable) return accessibility
        // As a last resort, walk the active window for an editable node.
        val root = rootInActiveWindow ?: return null
        return findEditableDescendant(root)
    }

    private fun findEditableDescendant(node: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        if (node.isEditable) return node
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            findEditableDescendant(child)?.let { return it }
        }
        return null
    }

    companion object {
        // How long to wait for the second volume key before treating a press as
        // a normal volume change. Long enough to catch a "simultaneous" squeeze,
        // short enough to keep volume feeling responsive.
        private const val COMBO_WINDOW_MS = 140L

        @Volatile
        private var instance: ZwangliAccessibilityService? = null

        fun typeIntoFocusedField(text: String): Boolean =
            instance?.typeIntoFocusedField(text) ?: false

        fun isConnected(): Boolean = instance != null

        fun performGlobal(actionId: Int): Boolean =
            instance?.performGlobal(actionId) ?: false
    }
}
