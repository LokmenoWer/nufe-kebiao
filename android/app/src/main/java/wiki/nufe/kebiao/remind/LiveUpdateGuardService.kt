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
import wiki.nufe.kebiao.diag.Diag

/**
 * 灵动卡片展示期间的前台守护。
 *
 * 国产定制系统在用户清理后台时会连同 App 的 promoted / 实况通知一起收起；把同一张卡片
 * 绑到前台服务上，系统就不会把进程当作可随手清理的后台应用。
 *
 * 服务由 startForegroundService 拉起，没调用 startForeground 就停止会让进程崩溃，
 * 所以即使不需要守护，也要先进入前台再停。
 */
class LiveUpdateGuardService : Service() {
    private val handler = Handler(Looper.getMainLooper())
    private var stopRunnable: Runnable? = null
    private var cardItem: Reminders.Item? = null
    private var cardConfig: LiveConfig? = null
    private var cardIsPreview: Boolean = false
    private var ticker: Runnable? = null

    private data class ActiveCard(
        val notification: Notification,
        val dismissMs: Long,
        val item: Reminders.Item,
        val config: LiveConfig,
        val isPreview: Boolean,
    )

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
        val (notification, dismissMs, item, config, isPreview) = card
        cardItem = item
        cardConfig = config
        cardIsPreview = isPreview
        LiveUpdates.remember(this, item, dismissMs, config, isPreview)
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
        // STICKY：被系统回收后尽量拉起，按持久化的卡片数据恢复。Android 12+ 后台重启时
        // 只有已解除电池优化（或允许自启动）才允许再进前台；不允许时上面的 runCatching 会退出，不会崩溃。
        return START_STICKY
    }

    /** 胶囊/卡片上的“剩 N 分钟”与进度条每跨一分钟重发一次；系统倒计时只有秒级。 */
    private fun startTicker() {
        ticker?.let(handler::removeCallbacks)
        ticker = object : Runnable {
            override fun run() {
                val item = cardItem ?: run {
                    stopSelf()
                    return
                }
                val config = cardConfig ?: LiveUpdates.configFrom(null, this@LiveUpdateGuardService)
                val delay = LiveUpdates.refresh(this@LiveUpdateGuardService, item, config, cardIsPreview)
                if (delay > 0) {
                    val dismissMs = LiveUpdates.resolveDismissMs(this@LiveUpdateGuardService, item, config, cardIsPreview)
                    val remainDismiss = (dismissMs - System.currentTimeMillis()).coerceAtLeast(1_000L)
                    stopRunnable?.let {
                        handler.removeCallbacks(it)
                        handler.postDelayed(it, remainDismiss)
                    }
                    handler.postDelayed(this, delay)
                } else {
                    LiveUpdates.clearRemembered(this@LiveUpdateGuardService)
                    stopSelf()
                }
            }
        }.also { it.run() }
    }

    override fun onDestroy() {
        stopRunnable?.let(handler::removeCallbacks)
        ticker?.let(handler::removeCallbacks)
        ticker = null
        cardItem = null
        cardConfig = null
        cardIsPreview = false
        stopForeground(STOP_FOREGROUND_REMOVE)
        Diag.i("live update guard stopped")
        super.onDestroy()
    }

    /** 还需要展示的卡片和它的收起时间；已过期或数据无效时返回 null。 */
    private fun liveCard(intent: Intent?): ActiveCard? {
        if (intent == null) return null
        return runCatching {
            val item = LiveUpdates.itemFrom(intent)
            val config = LiveUpdates.configFrom(intent, this)
            val isPreview = LiveUpdates.isPreview(intent, this)
            val dismissMs = LiveUpdates.resolveDismissMs(this, item, config, isPreview)
            if (dismissMs <= System.currentTimeMillis()) return null
            LiveUpdates.buildNotification(this, item, config, isUpdate = true, isPreview = isPreview)?.let {
                ActiveCard(it, dismissMs, item, config, isPreview)
            }
        }.getOrNull()
    }

    /** 进程被回收后系统以 null Intent 重启：按持久化的卡片数据恢复。 */
    private fun restoredCard(): ActiveCard? = runCatching {
        // 用户在此期间关掉了提醒 / 灵动显示，或在系统里收回了权限：不再恢复。
        if (!LiveUpdates.restorable(this)) return null
        val item = LiveUpdates.savedItem(this) ?: return null
        val config = LiveUpdates.configFrom(null, this)
        val isPreview = LiveUpdates.isPreview(null, this)
        val dismissMs = LiveUpdates.resolveDismissMs(this, item, config, isPreview)
        if (dismissMs <= System.currentTimeMillis()) return null
        LiveUpdates.buildNotification(this, item, config, isUpdate = true, isPreview = isPreview)?.let {
            ActiveCard(it, dismissMs, item, config, isPreview)
        }
    }.getOrNull()

    private fun guardNotification(): Notification {
        LiveUpdates.ensureChannel(this)
        return Notification.Builder(this, LiveUpdates.CHANNEL)
            .setSmallIcon(R.drawable.ic_notify_calendar)
            .setContentTitle("课表提醒")
            .setContentText("保持灵动提醒")
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
