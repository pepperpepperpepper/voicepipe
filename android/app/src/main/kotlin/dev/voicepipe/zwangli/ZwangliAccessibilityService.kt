package dev.voicepipe.zwangli

import android.accessibilityservice.AccessibilityService
import android.os.Bundle
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo

class ZwangliAccessibilityService : AccessibilityService() {

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
