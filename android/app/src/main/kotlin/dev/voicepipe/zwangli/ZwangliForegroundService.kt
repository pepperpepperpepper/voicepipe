package dev.voicepipe.zwangli

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat

class ZwangliForegroundService : Service() {

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopForegroundCompat()
            stopSelf()
            running = false
            return START_NOT_STICKY
        }
        ensureChannel(this)
        startForeground(NOTIFICATION_ID, buildNotification(this))
        running = true
        return START_STICKY
    }

    override fun onDestroy() {
        running = false
        super.onDestroy()
    }

    private fun stopForegroundCompat() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            stopForeground(STOP_FOREGROUND_REMOVE)
        } else {
            @Suppress("DEPRECATION")
            stopForeground(true)
        }
    }

    companion object {
        const val CHANNEL_ID = "zwangli_dictation"
        const val NOTIFICATION_ID = 0x5A57
        const val ACTION_STOP = "dev.voicepipe.zwangli.ACTION_STOP"
        const val EXTRA_AUTO_LISTEN = "dev.voicepipe.zwangli.AUTO_LISTEN"

        @Volatile
        private var running: Boolean = false

        fun isRunning(): Boolean = running

        fun start(context: Context) {
            val intent = Intent(context, ZwangliForegroundService::class.java)
            ContextCompat.startForegroundService(context, intent)
        }

        fun stop(context: Context) {
            val intent = Intent(context, ZwangliForegroundService::class.java).apply {
                action = ACTION_STOP
            }
            context.startService(intent)
        }

        fun ensureChannel(context: Context) {
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
            val mgr = context.getSystemService(NotificationManager::class.java) ?: return
            if (mgr.getNotificationChannel(CHANNEL_ID) != null) return
            val channel = NotificationChannel(
                CHANNEL_ID,
                context.getString(R.string.notification_channel_name),
                NotificationManager.IMPORTANCE_LOW,
            ).apply {
                description = context.getString(R.string.notification_channel_description)
                setShowBadge(false)
            }
            mgr.createNotificationChannel(channel)
        }

        fun buildNotification(context: Context): android.app.Notification {
            val dictate = Intent(context, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_NEW_TASK or
                    Intent.FLAG_ACTIVITY_CLEAR_TOP or
                    Intent.FLAG_ACTIVITY_SINGLE_TOP
                putExtra(EXTRA_AUTO_LISTEN, true)
            }
            val dictatePending = PendingIntent.getActivity(
                context,
                REQUEST_DICTATE,
                dictate,
                pendingFlags(),
            )
            val openApp = Intent(context, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
            }
            val openAppPending = PendingIntent.getActivity(
                context,
                REQUEST_OPEN,
                openApp,
                pendingFlags(),
            )
            val stop = Intent(context, ZwangliForegroundService::class.java).apply {
                action = ACTION_STOP
            }
            val stopPending = PendingIntent.getService(
                context,
                REQUEST_STOP,
                stop,
                pendingFlags(),
            )
            return NotificationCompat.Builder(context, CHANNEL_ID)
                .setSmallIcon(android.R.drawable.ic_btn_speak_now)
                .setContentTitle(context.getString(R.string.notification_title))
                .setContentText(context.getString(R.string.notification_text))
                .setContentIntent(openAppPending)
                .setOngoing(true)
                .setPriority(NotificationCompat.PRIORITY_LOW)
                .setShowWhen(false)
                .addAction(
                    android.R.drawable.ic_btn_speak_now,
                    context.getString(R.string.notification_action_dictate),
                    dictatePending,
                )
                .addAction(
                    android.R.drawable.ic_menu_close_clear_cancel,
                    context.getString(R.string.notification_action_stop),
                    stopPending,
                )
                .build()
        }

        private const val REQUEST_DICTATE = 1
        private const val REQUEST_OPEN = 2
        private const val REQUEST_STOP = 3

        private fun pendingFlags(): Int =
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
    }
}
