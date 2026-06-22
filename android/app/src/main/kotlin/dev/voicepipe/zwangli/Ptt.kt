package dev.voicepipe.zwangli

import android.content.Context
import android.content.Intent

/**
 * Push-to-talk bus between [ZwangliAccessibilityService] (which detects the
 * Volume-Up + Volume-Down hold) and [MainActivity] (which owns the recorder and
 * must be foreground to capture the mic). The service can't record from the
 * background, so a PTT start brings MainActivity to the front; release then
 * sends or cancels.
 */
object Ptt {
    interface Listener {
        fun onPttStart()
        fun onPttStop(send: Boolean)
    }

    const val EXTRA_PTT_START = "dev.voicepipe.zwangli.PTT_START"

    @Volatile private var listener: Listener? = null
    @Volatile private var armed: Boolean = false
    // A stop that arrived before MainActivity finished launching (very fast
    // release): null = none, true = send, false = cancel.
    @Volatile private var pendingStop: Boolean? = null

    fun isArmed(): Boolean = armed

    fun register(l: Listener) {
        listener = l
        val ps = pendingStop
        if (ps != null) {
            pendingStop = null
            armed = false
            l.onPttStop(ps)
        }
    }

    fun unregister(l: Listener) {
        if (listener === l) listener = null
    }

    /** Combo pressed: start talking (bring the activity up if needed). */
    fun start(context: Context) {
        armed = true
        pendingStop = null
        val l = listener
        if (l != null) {
            l.onPttStart()
        } else {
            context.startActivity(
                Intent(context, MainActivity::class.java)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    .putExtra(EXTRA_PTT_START, true),
            )
        }
    }

    /** Combo released: [send] true = stop & dispatch, false = discard. */
    fun stop(send: Boolean) {
        if (!armed) return
        val l = listener
        if (l != null) {
            armed = false
            l.onPttStop(send)
        } else {
            // Activity still launching; remember and apply on register().
            pendingStop = send
        }
    }
}
