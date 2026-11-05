package wiki.nufe.kebiao.remind

import android.app.Notification
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import wiki.nufe.kebiao.R
import wiki.nufe.kebiao.data.lessonMs
import wiki.nufe.kebiao.diag.Diag

/**
 * 流体云展示期间的前台守护。
 *
 * ColorOS 在用户清理后台时会连同 App 的 promoted 通知一起收起；把同一张卡片
 * 绑到前台服务上，系统就不会把进程当作可随手清理的后台应用。
 */
class LiveUpdateGuardService : Service() {
    private val handler = Handler(Looper.getMainLooper())
    private val stopRunnable = Runnable { stopSelf() }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent == null) {
            stopSelf()
            return START_NOT_STICKY
        }
        val item = LiveUpdates.itemFrom(intent)
        val startMs = lessonMs(item.date, item.start)
        val endMs = lessonMs(item.date, item.end)
        val dismissMs = minOf(endMs, startMs + Reminders.IN_CLASS_VISIBLE_MS)
        if (dismissMs <= System.currentTimeMillis()) {
            stopSelf()
            return START_NOT_STICKY
        }
        val notification = LiveUpdates.buildNotification(this, item)
        if (notification == null) {
            stopSelf()
            return START_NOT_STICKY
        }
        handler.removeCallbacks(stopRunnable)
        handler.postDelayed(stopRunnable, (dismissMs - System.currentTimeMillis()).coerceAtLeast(1_000L))
        val started = runCatching {
            if (Build.VERSION.SDK_INT >= 34) {
                startForeground(
                    LiveUpdates.NOTIFICATION_ID,
                    notification,
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE,
                )
            } else {
                startForeground(LiveUpdates.NOTIFICATION_ID, notification)
            }
        }.isSuccess
        if (!started) {
            // 个别 ROM 不接受 promoted 通知直接当前台通知；退回一张最小通知继续保活。
            val guard = Notification.Builder(this, LiveUpdates.CHANNEL)
                .setSmallIcon(R.drawable.ic_notify)
                .setContentTitle("课表提醒")
                .setContentText("保持流体云提醒")
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setSound(null)
                .build()
            val fallback = runCatching {
                startForeground(LiveUpdates.GUARD_NOTIFICATION_ID, guard)
            }.isSuccess
            if (!fallback) {
                stopSelf()
                return START_NOT_STICKY
            }
            Diag.w("live update guard using fallback notification")
        }
        Diag.i("live update guard started")
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        handler.removeCallbacks(stopRunnable)
        stopForeground(STOP_FOREGROUND_REMOVE)
        Diag.i("live update guard stopped")
        super.onDestroy()
    }
}
