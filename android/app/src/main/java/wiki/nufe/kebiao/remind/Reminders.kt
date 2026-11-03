package wiki.nufe.kebiao.remind

import android.Manifest
import android.app.AlarmManager
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import wiki.nufe.kebiao.R
import wiki.nufe.kebiao.data.Day
import wiki.nufe.kebiao.data.Now
import wiki.nufe.kebiao.data.Payload
import wiki.nufe.kebiao.data.Repository
import wiki.nufe.kebiao.data.daysBetween
import wiki.nufe.kebiao.data.lessonMs
import wiki.nufe.kebiao.data.resolveDays
import wiki.nufe.kebiao.sync.Sync
import wiki.nufe.kebiao.ui.MainActivity

/**
 * 上课提醒：本机闹钟到点发通知，不依赖服务器推送。每次拿到新课表都整批重排，
 * 调课后旧时间的提醒自然被替换掉。
 */
object Reminders {
    const val CHANNEL = "class_reminder"
    const val IN_CLASS_VISIBLE_MS = 5 * 60_000L  // 开课后保留 5 分钟，然后静默撤销
    private const val HORIZON_DAYS = 7     // 只排未来这么多天的；更远的等下次同步
    private const val MAX = 40             // 闹钟太多会被部分 ROM 限制
    private const val MIN_GAP_MS = 60_000L // 不足 1 分钟就开始的不再响
    private const val PREFS = "reminders"

    data class Item(
        val atMs: Long,
        val title: String,
        val text: String,
        val date: String = "",
        val start: String = "",
        val end: String = "",
        val name: String = "",
        val room: String = "",
        val changed: Boolean = false,
        val was: String? = null,
    )

    /** 未来几天有课的日子（去重）；今天 / 明天按缓存日期纠正，避免把昨天的“今天”排进提醒。 */
    private fun lessonDays(payload: Payload, now: Now): List<Day> {
        val seen = mutableSetOf<String>()
        val out = mutableListOf<Day>()
        val (today, tomorrow) = resolveDays(payload, now)
        val candidates = listOf(today, tomorrow, payload.upcoming) +
            payload.week?.days.orEmpty() + payload.nextWeek?.days.orEmpty()
        for (day in candidates) {
            if (day == null || day.date.isEmpty() || day.date in seen || day.lessons.isEmpty()) continue
            val delta = daysBetween(now.date, day.date)
            if (delta !in 0..HORIZON_DAYS) continue
            seen += day.date
            out += day
        }
        return out.sortedBy { it.date }
    }

    fun plan(payload: Payload, now: Now, lead: Int): List<Item> {
        if (lead <= 0) return emptyList()
        val out = mutableListOf<Item>()
        for (day in lessonDays(payload, now)) {
            for (lesson in day.lessons) {
                val at = lessonMs(day.date, lesson.start) - lead * 60_000L
                if (at - now.ms < MIN_GAP_MS) continue
                val place = lesson.room.ifEmpty { "地点待定" }
                val changed = if (lesson.changed) " · 已调课${lesson.was?.let { "（$it）" } ?: ""}" else ""
                out += Item(
                    atMs = at,
                    title = "${if (lead % 60 == 0) "${lead / 60} 小时" else "$lead 分钟"}后：${lesson.name}",
                    text = "${lesson.start}–${lesson.end} · $place$changed",
                    date = day.date,
                    start = lesson.start,
                    end = lesson.end,
                    name = lesson.name,
                    room = place,
                    changed = lesson.changed,
                    was = lesson.was,
                )
            }
        }
        return out.sortedBy { it.atMs }.take(MAX)
    }

    fun ensureChannel(context: Context) {
        if (Build.VERSION.SDK_INT < 26) return
        val manager = context.getSystemService(NotificationManager::class.java)
        if (manager.getNotificationChannel(CHANNEL) != null) return
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL, "上课提醒", NotificationManager.IMPORTANCE_HIGH).apply {
                description = "课前按设定的分钟数提醒课程、时间和教室"
            },
        )
    }

    /** 按缓存里的课表整批重排。 */
    fun reschedule(context: Context) {
        val repo = Repository(context)
        val snapshot = repo.cached()
        val items = if (snapshot == null || repo.widgetUrl == null || !repo.alarmEnabled) emptyList()
        else plan(snapshot.payload, Now.current(), repo.remindMinutes)
        val alarms = context.getSystemService(AlarmManager::class.java)
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val previous = prefs.getInt("count", MAX)
        for (i in items.size until maxOf(previous, items.size)) {
            pending(context, i, null)?.let { alarms.cancel(it); it.cancel() }
        }
        val exact = Build.VERSION.SDK_INT < 31 || alarms.canScheduleExactAlarms()
        items.forEachIndexed { i, item ->
            val intent = pending(context, i, item)!!
            if (exact) alarms.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, item.atMs, intent)
            else alarms.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, item.atMs, intent)
        }
        prefs.edit().putInt("count", items.size).apply()
    }

    /** item = null 只用来查找已有的同号闹钟（取消用）。 */
    private fun pending(context: Context, index: Int, item: Item?): PendingIntent? {
        val intent = Intent(context, ReminderReceiver::class.java).setAction(ACTION)
        if (item != null) {
            intent.putExtra("title", item.title)
                .putExtra("text", item.text)
                .putExtra("at", item.atMs)
                .putExtra("date", item.date)
                .putExtra("start", item.start)
                .putExtra("end", item.end)
                .putExtra("name", item.name)
                .putExtra("room", item.room)
                .putExtra("changed", item.changed)
                .putExtra("was", item.was)
        }
        val flags = PendingIntent.FLAG_IMMUTABLE or
            if (item == null) PendingIntent.FLAG_NO_CREATE else PendingIntent.FLAG_UPDATE_CURRENT
        return PendingIntent.getBroadcast(context, index, intent, flags)
    }

    fun canNotify(context: Context) = Build.VERSION.SDK_INT < 33 ||
        ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) ==
        PackageManager.PERMISSION_GRANTED

    const val ACTION = "wiki.nufe.kebiao.REMIND"
}

class ReminderReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        when (intent.action) {
            LiveUpdates.ACTION_START -> {
                if (Reminders.canNotify(context)) LiveUpdates.startClass(context, intent)
                return
            }
            LiveUpdates.ACTION_END -> {
                LiveUpdates.cancel(context)
                return
            }
            Reminders.ACTION -> Unit
            else -> return
        }
        if (!Reminders.canNotify(context)) return
        val at = intent.getLongExtra("at", 0L)
        val repo = Repository(context)
        if (!repo.alarmEnabled) return
        if (repo.liveUpdateEnabled &&
            runCatching { LiveUpdates.post(context, LiveUpdates.itemFrom(intent)) }.getOrDefault(false)
        ) return
        // 手机关机 / 休眠错过太久（超过 5 分钟）就不补发，免得课都上了才响
        if (at > 0 && System.currentTimeMillis() - at > 5 * 60_000L) return
        Reminders.ensureChannel(context)
        val open = PendingIntent.getActivity(
            context, 0, Intent(context, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE,
        )
        val date = intent.getStringExtra("date").orEmpty()
        val start = intent.getStringExtra("start").orEmpty()
        val startMs = if (date.isNotBlank() && start.isNotBlank()) lessonMs(date, start) else null
        val timeout = startMs?.let {
            (it + Reminders.IN_CLASS_VISIBLE_MS - System.currentTimeMillis()).coerceAtLeast(1L)
        } ?: Reminders.IN_CLASS_VISIBLE_MS
        val notification = NotificationCompat.Builder(context, Reminders.CHANNEL)
            .setSmallIcon(R.drawable.ic_notify)
            .setContentTitle(intent.getStringExtra("title"))
            .setContentText(intent.getStringExtra("text"))
            .setCategory(NotificationCompat.CATEGORY_REMINDER)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setContentIntent(open)
            .setAutoCancel(true)
            .setTimeoutAfter(timeout)
            .build()
        try {
            NotificationManagerCompat.from(context).notify((at / 60_000L).toInt(), notification)
        } catch (_: SecurityException) {
            // 通知权限在这之间被关掉了
        }
    }
}

/** 开机、更新 App、改时区后闹钟会丢：重排提醒并刷新小组件。 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        Reminders.reschedule(context)
        Sync.schedule(context)
        Sync.now(context)
    }
}
