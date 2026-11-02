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
import wiki.nufe.kebiao.R
import wiki.nufe.kebiao.data.Now
import wiki.nufe.kebiao.data.Repository
import wiki.nufe.kebiao.data.lessonMs
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

    private const val CHANNEL = "class_live_update"
    private const val NOTIFICATION_ID = 9201
    private const val REQ_START = 9201
    private const val REQ_END = 9202

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
        if (Build.VERSION.SDK_INT < 36) return false
        val manager = context.getSystemService(NotificationManager::class.java) ?: return false
        return manager.canPostPromotedNotifications()
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

    /** 课前闹钟到点：发 Live Update，并安排上课/下课两个更新点。 */
    fun post(context: Context, item: Reminders.Item): Boolean {
        if (!available(context) || item.date.isBlank() || item.start.isBlank() || item.end.isBlank()) return false
        val startMs = lessonMs(item.date, item.start)
        val endMs = lessonMs(item.date, item.end)
        if (endMs <= System.currentTimeMillis()) return false
        ensureChannel(context)
        val phase = if (startMs > System.currentTimeMillis()) Phase.BEFORE else Phase.DURING
        notify(context, item, phase, startMs, endMs)
        schedule(context, item, ACTION_START, startMs, REQ_START)
        schedule(context, item, ACTION_END, endMs, REQ_END)
        return true
    }

    /** 上课时刻：把“即将上课”切换成“正在上课”。 */
    fun startClass(context: Context, intent: Intent) {
        if (!available(context)) {
            cancel(context)
            return
        }
        val item = itemFrom(intent)
        val startMs = lessonMs(item.date, item.start)
        val endMs = lessonMs(item.date, item.end)
        if (endMs <= System.currentTimeMillis()) {
            cancel(context)
            return
        }
        ensureChannel(context)
        notify(context, item, Phase.DURING, startMs, endMs)
        schedule(context, item, ACTION_END, endMs, REQ_END)
    }

    fun cancel(context: Context) {
        context.getSystemService(NotificationManager::class.java)?.cancel(NOTIFICATION_ID)
        cancelPhase(context, ACTION_START, REQ_START)
        cancelPhase(context, ACTION_END, REQ_END)
    }

    private enum class Phase { BEFORE, DURING }

    private fun notify(context: Context, item: Reminders.Item, phase: Phase, startMs: Long, endMs: Long) {
        if (Build.VERSION.SDK_INT < 36) return
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

        val title = if (phase == Phase.BEFORE) {
            "即将上课 · ${item.name.ifBlank { "下一节课" }}"
        } else {
            "正在上课 · ${item.name.ifBlank { "当前课程" }}"
        }
        val room = item.room.ifBlank { "地点待定" }
        val changed = if (item.changed) " · 已调课${item.was?.let { "（$it）" } ?: ""}" else ""
        val open = PendingIntent.getActivity(
            context,
            0,
            Intent(context, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val builder = Notification.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_notify)
            .setContentTitle(title)
            .setContentText("${item.start}–${item.end} · $room$changed")
            .setSubText("南财课表 · 流体云提醒")
            .setContentIntent(open)
            .setOngoing(true)
            .setCategory(Notification.CATEGORY_PROGRESS)
            .setOnlyAlertOnce(true)
            .setStyle(progressStyle)
            .setWhen(if (phase == Phase.BEFORE) startMs else endMs)
            .setUsesChronometer(true)
            .setChronometerCountDown(true)
        applyPromotedFlags(builder, if (phase == Phase.BEFORE) "距上课" else "上课中")
        context.getSystemService(NotificationManager::class.java).notify(NOTIFICATION_ID, builder.build())
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

    private fun ensureChannel(context: Context) {
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
        if (Build.VERSION.SDK_INT < 31 || alarms.canScheduleExactAlarms()) {
            alarms.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, atMs, pending)
        } else {
            alarms.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, atMs, pending)
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

    private fun putExtras(intent: Intent, item: Reminders.Item) {
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
