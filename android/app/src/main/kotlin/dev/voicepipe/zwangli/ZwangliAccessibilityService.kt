package dev.voicepipe.zwangli

import android.accessibilityservice.AccessibilityService
import android.os.Bundle
import android.view.KeyEvent
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo

class ZwangliAccessibilityService : AccessibilityService() {

    // Push-to-talk: hold Volume-Up + Volume-Down to record; release Volume-Down
    // first → send, release Volume-Up first (keep Volume-Down) → cancel.
    private var volUp = false
    private var volDown = false
    private var pttActive = false
    private var consumeTrailingUp = false

    override fun onKeyEvent(event: KeyEvent): Boolean {
        val code = event.keyCode
        if (code != KeyEvent.KEYCODE_VOLUME_UP && code != KeyEvent.KEYCODE_VOLUME_DOWN) {
            return false
        }
        when (event.action) {
            KeyEvent.ACTION_DOWN -> {
                if (code == KeyEvent.KEYCODE_VOLUME_UP) volUp = true else volDown = true
                if (pttActive) return true // swallow auto-repeat while talking
                if (volUp && volDown) {
                    pttActive = true
                    Ptt.start(this)
                    return true
                }
                return false // a single volume key → let the system change volume
            }
            KeyEvent.ACTION_UP -> {
                val wasActive = pttActive
                if (code == KeyEvent.KEYCODE_VOLUME_UP) volUp = false else volDown = false
                if (wasActive) {
                    pttActive = false
                    consumeTrailingUp = true
                    // Released Volume-Down first → send; Volume-Up first → cancel.
                    Ptt.stop(send = code == KeyEvent.KEYCODE_VOLUME_DOWN)
                    return true
                }
                if (consumeTrailingUp) {
                    if (!volUp && !volDown) consumeTrailingUp = false
                    return true // swallow the other key's release
                }
                return false
            }
        }
        return false
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
        @Volatile
        private var instance: ZwangliAccessibilityService? = null

        fun typeIntoFocusedField(text: String): Boolean =
            instance?.typeIntoFocusedField(text) ?: false

        fun isConnected(): Boolean = instance != null

        fun performGlobal(actionId: Int): Boolean =
            instance?.performGlobal(actionId) ?: false
    }
}
