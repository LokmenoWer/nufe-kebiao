package wiki.nufe.kebiao.remind

import android.app.AlarmManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.graphics.drawable.Icon
import android.os.Build
import androidx.core.content.ContextCompat
import wiki.nufe.kebiao.R
import wiki.nufe.kebiao.data.Now
import wiki.nufe.kebiao.data.Repository
import wiki.nufe.kebiao.data.lessonMs
import wiki.nufe.kebiao.diag.Diag
import wiki.nufe.kebiao.ui.MainActivity

/**
 * Android 16 Live Updates / ColorOS 流体云上课提醒。
 *
 * 复用 Reminders 的课前闹钟作为触发点：到点后发一条 promoted ongoing 通知，
 * 再在上课/下课时更新或撤销。数据来自 /widget/<token>.json 的本地缓存，
 * 该缓存由 Sync 在课前检查点刷新，不另抓网页。
 */
object LiveUpdates {
    const val ACTION_START = "wiki.nufe.kebiao.LIVE_START"
    const val ACTION_END = "wiki.nufe.kebiao.LIVE_END"

    internal const val CHANNEL = "class_live_update"
    internal const val NOTIFICATION_ID = 9201
    internal const val GUARD_NOTIFICATION_ID = 9200
    private const val REQ_START = 9201
    private const val REQ_END = 9202
    private const val GUARD_PREFS = "live_update_guard"

    fun itemFrom(intent: Intent): Reminders.Item = Reminders.Item(
        atMs = intent.getLongExtra("at", 0L),
        title = intent.getStringExtra("title").orEmpty(),
        text = intent.getStringExtra("text").orEmpty(),
        date = intent.getStringExtra("date").orEmpty(),
        start = intent.getStringExtra("start").orEmpty(),
        end = intent.getStringExtra("end").orEmpty(),
        name = intent.getStringExtra("name").orEmpty(),
        room = intent.getStringExtra("room").orEmpty(),
        changed = intent.getBooleanExtra("changed", false),
        was = intent.getStringExtra("was"),
    )

    private fun available(context: Context): Boolean {
        // 流体云目前只按 ColorOS 16 的展示行为适配；其他平台统一走普通通知
        if (!isColorOs()) return false
        if (Build.VERSION.SDK_INT < 36) return false
        val manager = context.getSystemService(NotificationManager::class.java) ?: return false
        if (!manager.areNotificationsEnabled()) return false
        // 用户可能只关掉流体云通道：此时退回普通提醒通道
        val channel = manager.getNotificationChannel(CHANNEL)
        if (channel != null && channel.importance == NotificationManager.IMPORTANCE_NONE) return false
        return manager.canPostPromotedNotifications()
    }

    private fun isColorOs(): Boolean {
        val maker = Build.MANUFACTURER.lowercase()
        val brand = Build.BRAND.lowercase()
        return maker in setOf("oppo", "oneplus", "realme") ||
            brand in setOf("oppo", "oneplus", "realme")
    }

    /** debug 预览：不等到课前，直接拿缓存里的下一节课发一张真实卡片。 */
    fun preview(context: Context): Boolean {
        if (!available(context)) return false
        val repo = Repository(context)
        val payload = repo.cached()?.payload ?: return false
        val lead = if (repo.remindMinutes > 0) repo.remindMinutes else 10
        val item = Reminders.plan(payload, Now.current(), lead).firstOrNull() ?: return false
        return post(context, item)
    }

    /** 课前闹钟到点：发 Live Update，并安排上课更新和开课 5 分钟后的撤销。 */
    fun post(context: Context, item: Reminders.Item): Boolean {
        if (!available(context) || item.date.isBlank() || item.start.isBlank() || item.end.isBlank()) return false
        val startMs = lessonMs(item.date, item.start)
        val endMs = lessonMs(item.date, item.end)
        val dismissMs = minOf(endMs, startMs + Reminders.IN_CLASS_VISIBLE_MS)
        if (dismissMs <= System.currentTimeMillis()) return false
        ensureChannel(context)
        val phase = if (startMs > System.currentTimeMillis()) Phase.BEFORE else Phase.DURING
        notify(context, item, phase, startMs, endMs)
        schedule(context, item, ACTION_START, startMs, REQ_START)
        schedule(context, item, ACTION_END, dismissMs, REQ_END)
        remember(context, item, dismissMs)
        startGuard(context, item)
        return true
    }

    /** 上课时刻：把“即将上课”切换成“正在上课”，开课 5 分钟后静默撤销。 */
    fun startClass(context: Context, intent: Intent) {
        if (!available(context)) {
            cancel(context)
            return
        }
        val item = itemFrom(intent)
        val startMs = lessonMs(item.date, item.start)
        val endMs = lessonMs(item.date, item.end)
        val dismissMs = minOf(endMs, startMs + Reminders.IN_CLASS_VISIBLE_MS)
        if (dismissMs <= System.currentTimeMillis()) {
            cancel(context)
            return
        }
        ensureChannel(context)
        notify(context, item, Phase.DURING, startMs, endMs)
        schedule(context, item, ACTION_END, dismissMs, REQ_END)
        remember(context, item, dismissMs)
        startGuard(context, item)
    }

    fun cancel(context: Context) {
        stopGuard(context)
        context.getSharedPreferences(GUARD_PREFS, Context.MODE_PRIVATE).edit().clear().apply()
        context.getSystemService(NotificationManager::class.java)?.apply {
            cancel(NOTIFICATION_ID)
            cancel(GUARD_NOTIFICATION_ID)
        }
        cancelPhase(context, ACTION_START, REQ_START)
        cancelPhase(context, ACTION_END, REQ_END)
    }

    private enum class Phase { BEFORE, DURING }

    private fun notify(context: Context, item: Reminders.Item, phase: Phase, startMs: Long, endMs: Long) {
        val notification = buildNotification(context, item, phase, startMs, endMs) ?: return
        context.getSystemService(NotificationManager::class.java).notify(NOTIFICATION_ID, notification)
    }

    /** 前台守护服务复用同一张卡片；没有这张卡片时不需要保活。 */
    internal fun buildNotification(context: Context, item: Reminders.Item): Notification? {
        if (Build.VERSION.SDK_INT < 36) return null
        ensureChannel(context)
        val startMs = lessonMs(item.date, item.start)
        val endMs = lessonMs(item.date, item.end)
        val phase = if (startMs > System.currentTimeMillis()) Phase.BEFORE else Phase.DURING
        return buildNotification(context, item, phase, startMs, endMs)
    }

    private fun buildNotification(
        context: Context,
        item: Reminders.Item,
        phase: Phase,
        startMs: Long,
        endMs: Long,
    ): Notification? {
        if (Build.VERSION.SDK_INT < 36) return null
        val progressStyle = Notification.ProgressStyle()
            .setProgress(0)
            .setProgressSegments(
                listOf(
                    Notification.ProgressStyle.Segment(50).setColor(0xFF4B63E0.toInt()),
                    Notification.ProgressStyle.Segment(50).setColor(0xFFB9C3FF.toInt()),
                ),
            )
            .setProgressTrackerIcon(Icon.createWithResource(context, R.drawable.ic_notify))
            .setStyledByProgress(true)

        val room = item.room.ifBlank { "地点待定" }
        val changed = if (item.changed) " · 已调课${item.was?.let { "（$it）" } ?: ""}" else ""
        val title = "${item.start}–${item.end} · $room"
        val course = (item.name.ifBlank { "下一节课" }) + changed
        val status = if (phase == Phase.BEFORE) "准备上课" else "正在上课"
        val dismissMs = minOf(endMs, startMs + Reminders.IN_CLASS_VISIBLE_MS)
        val timeout = (dismissMs - System.currentTimeMillis()).coerceAtLeast(1L)
        val open = PendingIntent.getActivity(
            context,
            0,
            Intent(context, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val builder = Notification.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_notify)
            .setContentTitle(title)
            .setContentText(status)
            .setSubText(course)
            .setContentIntent(open)
            .setOngoing(true)
            .setCategory(Notification.CATEGORY_PROGRESS)
            .setOnlyAlertOnce(true)
            .setStyle(progressStyle)
            .setWhen(if (phase == Phase.BEFORE) startMs else endMs)
            .setShowWhen(true)
            .setUsesChronometer(true)
            .setChronometerCountDown(true)
            .setTimeoutAfter(timeout)
        applyPromotedFlags(builder, "剩${remainingMinutes(phase, startMs, endMs)}分钟")
        return builder.build()
    }

    /** 剩余分钟数：课前倒数到开课，课上倒数到下课。 */
    private fun remainingMinutes(phase: Phase, startMs: Long, endMs: Long): Long {
        val target = if (phase == Phase.BEFORE) startMs else endMs
        return ((target - System.currentTimeMillis() + 59_999L) / 60_000L).coerceAtLeast(1L)
    }

    /**
     * 剩余分钟变化时重发同一张卡片（系统倒计时只有秒级，胶囊也只显示一段文字）。
     * 返回距下一次分钟跳变的毫秒数；0 表示卡片已到收起时间。
     */
    internal fun refresh(context: Context, item: Reminders.Item): Long {
        val now = System.currentTimeMillis()
        val startMs = lessonMs(item.date, item.start)
        val endMs = lessonMs(item.date, item.end)
        val dismissMs = minOf(endMs, startMs + Reminders.IN_CLASS_VISIBLE_MS)
        if (dismissMs <= now) return 0L
        buildNotification(context, item)?.let {
            context.getSystemService(NotificationManager::class.java)?.notify(NOTIFICATION_ID, it)
        }
        val target = if (now < startMs) startMs else endMs
        val left = target - now
        if (left <= 0) return 0L
        val rem = left % 60_000L
        return if (rem == 0L) 60_000L else rem.coerceAtLeast(1_000L)
    }

    /** HyperOS 的 API 36 框架可能没实现这两个方法，反射调用，缺失就跳过。 */
    private fun applyPromotedFlags(builder: Notification.Builder, shortText: String) {
        try {
            Notification.Builder::class.java
                .getMethod("setRequestPromotedOngoing", Boolean::class.javaPrimitiveType)
                .invoke(builder, true)
        } catch (_: Throwable) {
        }
        try {
            Notification.Builder::class.java
                .getMethod("setShortCriticalText", String::class.java)
                .invoke(builder, shortText)
        } catch (_: Throwable) {
        }
    }

    internal fun ensureChannel(context: Context) {
        val manager = context.getSystemService(NotificationManager::class.java) ?: return
        if (manager.getNotificationChannel(CHANNEL) != null) return
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL, "流体云上课提醒", NotificationManager.IMPORTANCE_LOW).apply {
                description = "Android 16 Live Updates / ColorOS 流体云上课提醒"
            },
        )
    }

    private fun schedule(context: Context, item: Reminders.Item, action: String, atMs: Long, requestCode: Int) {
        if (atMs <= System.currentTimeMillis()) return
        val alarms = context.getSystemService(AlarmManager::class.java)
        val intent = Intent(context, ReminderReceiver::class.java).setAction(action).also { putExtras(it, item) }
        val pending = PendingIntent.getBroadcast(
            context, requestCode, intent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        // ColorOS 会把普通精确闹钟放进最长 1 小时的弹性窗口；改用用户可见的 alarm clock
        // 才能保证开课时刻切换，关闭则依靠通知 timeout。
        val show = PendingIntent.getActivity(
            context,
            requestCode,
            Intent(context, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        runCatching {
            alarms.setAlarmClock(AlarmManager.AlarmClockInfo(atMs, show), pending)
        }.onFailure {
            if (Build.VERSION.SDK_INT < 31 || alarms.canScheduleExactAlarms()) {
                alarms.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, atMs, pending)
            } else {
                alarms.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, atMs, pending)
            }
        }
    }

    private fun cancelPhase(context: Context, action: String, requestCode: Int) {
        val alarms = context.getSystemService(AlarmManager::class.java)
        val intent = Intent(context, ReminderReceiver::class.java).setAction(action)
        val pending = PendingIntent.getBroadcast(
            context, requestCode, intent, PendingIntent.FLAG_NO_CREATE or PendingIntent.FLAG_IMMUTABLE,
        ) ?: return
        alarms.cancel(pending)
        pending.cancel()
    }

    /** 记住当前卡片，App 下次打开时如果还没到收起时间，就重新拉起守护服务。 */
    private fun remember(context: Context, item: Reminders.Item, dismissMs: Long) {
        context.getSharedPreferences(GUARD_PREFS, Context.MODE_PRIVATE).edit()
            .putLong("dismiss", dismissMs)
            .putLong("at", item.atMs)
            .putString("date", item.date)
            .putString("start", item.start)
            .putString("end", item.end)
            .putString("name", item.name)
            .putString("room", item.room)
            .putBoolean("changed", item.changed)
            .putString("was", item.was)
            .apply()
    }

    /** 打开 App 时调用：正在展示流体云就补上前台守护，防止清理后台时进程被杀。 */
    internal fun ensureGuard(context: Context) {
        val prefs = context.getSharedPreferences(GUARD_PREFS, Context.MODE_PRIVATE)
        val dismissMs = prefs.getLong("dismiss", 0L)
        if (dismissMs <= System.currentTimeMillis()) {
            prefs.edit().clear().apply()
            return
        }
        val item = Reminders.Item(
            atMs = prefs.getLong("at", 0L),
            title = "",
            text = "",
            date = prefs.getString("date", "").orEmpty(),
            start = prefs.getString("start", "").orEmpty(),
            end = prefs.getString("end", "").orEmpty(),
            name = prefs.getString("name", "").orEmpty(),
            room = prefs.getString("room", "").orEmpty(),
            changed = prefs.getBoolean("changed", false),
            was = prefs.getString("was", null),
        )
        if (item.date.isBlank() || item.start.isBlank() || item.end.isBlank()) return
        startGuard(context, item)
    }

    private fun startGuard(context: Context, item: Reminders.Item) {
        if (Build.VERSION.SDK_INT < 36) return
        val intent = Intent(context, LiveUpdateGuardService::class.java).also { putExtras(it, item) }
        runCatching { ContextCompat.startForegroundService(context, intent) }
            .onFailure { Diag.w("live update guard start failed: ${it.javaClass.simpleName}") }
    }

    private fun stopGuard(context: Context) {
        runCatching { context.stopService(Intent(context, LiveUpdateGuardService::class.java)) }
    }

    internal fun putExtras(intent: Intent, item: Reminders.Item) {
        intent.putExtra("at", item.atMs)
            .putExtra("title", item.title)
            .putExtra("text", item.text)
            .putExtra("date", item.date)
            .putExtra("start", item.start)
            .putExtra("end", item.end)
            .putExtra("name", item.name)
            .putExtra("room", item.room)
            .putExtra("changed", item.changed)
            .putExtra("was", item.was)
    }
}
