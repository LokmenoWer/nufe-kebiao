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
 *
 * 服务由 startForegroundService 拉起，没调用 startForeground 就停止会让进程崩溃，
 * 所以即使不需要守护，也要先进入前台再停。
 */
class LiveUpdateGuardService : Service() {
    private val handler = Handler(Looper.getMainLooper())
    private var stopRunnable: Runnable? = null
    private var cardItem: Reminders.Item? = null
    private var ticker: Runnable? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val card = liveCard(intent) ?: restoredCard()
        if (card == null) {
            // 进入前台的通知随即被移除，系统不会真正展示出来。
            runCatching { enterForeground(LiveUpdates.GUARD_NOTIFICATION_ID, guardNotification()) }
                .onFailure { Diag.w("live update guard stop failed: ${it.javaClass.simpleName}") }
            stopSelf(startId)
            return START_NOT_STICKY
        }
        val (notification, dismissMs, item) = card
        cardItem = item
        stopRunnable?.let(handler::removeCallbacks)
        stopRunnable = Runnable { stopSelf(startId) }.also {
            handler.postDelayed(it, (dismissMs - System.currentTimeMillis()).coerceAtLeast(1_000L))
        }
        val started = runCatching { enterForeground(LiveUpdates.NOTIFICATION_ID, notification) }.isSuccess
        if (!started) {
            // 个别 ROM 不接受 promoted 通知直接当前台通知；退回一张最小通知继续保活。
            val fallback = runCatching {
                enterForeground(LiveUpdates.GUARD_NOTIFICATION_ID, guardNotification())
            }.isSuccess
            if (!fallback) {
                stopSelf(startId)
                return START_NOT_STICKY
            }
            Diag.w("live update guard using fallback notification")
        }
        Diag.i("live update guard started")
        startTicker()
        // STICKY：被系统回收后尽量拉起，按持久化的卡片数据恢复流体云。Android 12+ 后台重启时
        // 只有已解除电池优化（或允许自启动）才允许再进前台；不允许时上面的 runCatching 会退出，不会崩溃。
        return START_STICKY
    }

    /** 胶囊/卡片上的“剩 N 分钟”每跨一分钟重发一次；系统倒计时只有秒级。 */
    private fun startTicker() {
        ticker?.let(handler::removeCallbacks)
        ticker = object : Runnable {
            override fun run() {
                val item = cardItem ?: return
                val delay = LiveUpdates.refresh(this@LiveUpdateGuardService, item)
                if (delay > 0) handler.postDelayed(this, delay)
            }
        }.also { it.run() }
    }

    override fun onDestroy() {
        stopRunnable?.let(handler::removeCallbacks)
        ticker?.let(handler::removeCallbacks)
        ticker = null
        cardItem = null
        stopForeground(STOP_FOREGROUND_REMOVE)
        Diag.i("live update guard stopped")
        super.onDestroy()
    }

    /** 还需要展示的卡片和它的收起时间；已过期或数据无效时返回 null。 */
    private fun liveCard(intent: Intent?): Triple<Notification, Long, Reminders.Item>? {
        if (intent == null) return null
        return runCatching {
            val item = LiveUpdates.itemFrom(intent)
            val startMs = lessonMs(item.date, item.start)
            val endMs = lessonMs(item.date, item.end)
            val dismissMs = minOf(endMs, startMs + Reminders.IN_CLASS_VISIBLE_MS)
            if (dismissMs <= System.currentTimeMillis()) return null
            LiveUpdates.buildNotification(this, item)?.let { Triple(it, dismissMs, item) }
        }.getOrNull()
    }

    /** 进程被回收后系统以 null Intent 重启：按持久化的卡片数据恢复流体云。 */
    private fun restoredCard(): Triple<Notification, Long, Reminders.Item>? = runCatching {
        // 用户在此期间关掉了提醒 / 灵动显示，或在系统里收回了流体云权限：不再恢复。
        if (!LiveUpdates.restorable(this)) return null
        val item = LiveUpdates.savedItem(this) ?: return null
        val startMs = lessonMs(item.date, item.start)
        val endMs = lessonMs(item.date, item.end)
        val dismissMs = minOf(endMs, startMs + Reminders.IN_CLASS_VISIBLE_MS)
        if (dismissMs <= System.currentTimeMillis()) return null
        LiveUpdates.buildNotification(this, item)?.let { Triple(it, dismissMs, item) }
    }.getOrNull()

    private fun guardNotification(): Notification {
        LiveUpdates.ensureChannel(this)
        return Notification.Builder(this, LiveUpdates.CHANNEL)
            .setSmallIcon(R.drawable.ic_notify_calendar)
            .setContentTitle("课表提醒")
            .setContentText("保持流体云提醒")
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setSound(null)
            .build()
    }

    private fun enterForeground(id: Int, notification: Notification) {
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(id, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(id, notification)
        }
    }
}
