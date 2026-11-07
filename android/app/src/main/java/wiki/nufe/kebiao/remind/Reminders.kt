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
import android.graphics.Bitmap
import android.graphics.Canvas
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import wiki.nufe.kebiao.R
import wiki.nufe.kebiao.data.Day
import wiki.nufe.kebiao.data.Now
import wiki.nufe.kebiao.data.Payload
import wiki.nufe.kebiao.data.Repository
import wiki.nufe.kebiao.data.daysBetween
import wiki.nufe.kebiao.data.lessonMs
import wiki.nufe.kebiao.data.resolveDays
import wiki.nufe.kebiao.diag.Diag
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
    private const val PREFS = "reminders"
    private const val SHOW_REQUEST_OFFSET = 100_000
    private val ioScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    /** 供主线程 UI 开关、滚轮与权限回调调用，避免在主线程读盘与批量排闹钟造成卡顿。 */
    fun rescheduleAsync(context: Context) {
        val app = context.applicationContext
        ioScope.launch { reschedule(app) }
    }

    // 这些 ROM 会把普通精确闹钟放进弹性窗口；只有闹钟时钟能保证课前唤醒。
    private val ALARM_CLOCK_OEMS = listOf(
        "oppo", "oneplus", "realme", "xiaomi", "redmi", "poco", "vivo", "iqoo", "huawei", "honor",
    )

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
                // 每次打开 App 都会重排，快到点的也要留着，否则同号闹钟被覆盖后这节课就不响了
                if (at <= now.ms) continue
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

    /** 按缓存里的课表整批重排。启动恢复和后台同步可能同时进来，串行化避免互相抵消。 */
    @Synchronized
    fun reschedule(context: Context) {
        val repo = Repository(context)
        val snapshot = repo.cached()
        val items = if (snapshot == null || !repo.hasTimetable || !repo.alarmEnabled) emptyList()
        else plan(snapshot.payload, Now.current(), repo.remindMinutes)
        val alarms = context.getSystemService(AlarmManager::class.java)
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val previous = prefs.getInt("count", MAX)
        for (i in items.size until maxOf(previous, items.size)) {
            pending(context, i, null)?.let { alarms.cancel(it); it.cancel() }
        }
        items.forEachIndexed { i, item ->
            schedule(context, item.atMs, i, pending(context, i, item)!!)
        }
        prefs.edit()
            .putInt("count", items.size)
            .putString("times", items.joinToString(",") { it.atMs.toString() })
            .apply()
        Diag.i("reminders scheduled=${items.size} alarmClock=${needsAlarmClock()}")
    }

    /**
     * 上次重排记下的提醒里还没到点的数量和最近一次时间，写进诊断日志。
     * 这是 App 自己的记录，系统不提供查询，不能说明闹钟此刻还在 AlarmManager 队列里。
     */
    fun scheduledInfo(context: Context): Pair<Int, Long?> {
        val now = System.currentTimeMillis()
        val times = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .getString("times", null).orEmpty()
            .split(',')
            .mapNotNull { it.toLongOrNull() }
            .filter { it > now }
        return times.size to times.minOrNull()
    }

    /**
     * 课前提醒也必须能唤醒进程：国产 ROM 上普通 setExact 会被延后，
     * 用用户可见的闹钟时钟排，失败时再退回精确 / 非精确闹钟。
     */
    internal fun schedule(
        context: Context,
        atMs: Long,
        requestCode: Int,
        pending: PendingIntent,
        useAlarmClock: Boolean = needsAlarmClock(),
    ) {
        val alarms = context.getSystemService(AlarmManager::class.java)
        if (useAlarmClock) {
            val show = PendingIntent.getActivity(
                context,
                requestCode + SHOW_REQUEST_OFFSET,
                Intent(context, MainActivity::class.java),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
            val scheduled = runCatching {
                alarms.setAlarmClock(AlarmManager.AlarmClockInfo(atMs, show), pending)
            }.isSuccess
            if (scheduled) return
        }
        if (Build.VERSION.SDK_INT < 31 || alarms.canScheduleExactAlarms()) {
            val scheduled = runCatching {
                alarms.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, atMs, pending)
            }.isSuccess
            if (scheduled) return
        }
        alarms.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, atMs, pending)
    }

    internal fun needsAlarmClock(): Boolean {
        val id = "${Build.MANUFACTURER} ${Build.BRAND}".lowercase()
        return ALARM_CLOCK_OEMS.any { it in id }
    }

    @Volatile private var appIconCache: Bitmap? = null

    /**
     * 彩色应用图标（64dp）：通知右侧大图标与首启弹窗共用；状态栏小图标仍用单色。
     * 流体云每分钟重发一次，缓存起来避免每次都重新画一张位图。
     */
    internal fun appIconBitmap(context: Context): Bitmap {
        val size = (64 * context.resources.displayMetrics.density).toInt().coerceAtLeast(64)
        appIconCache?.takeIf { it.width == size }?.let { return it }
        val drawable = context.packageManager.getApplicationIcon(context.packageName)
        val bitmap = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888)
        drawable.setBounds(0, 0, size, size)
        drawable.draw(Canvas(bitmap))
        appIconCache = bitmap
        return bitmap
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
        val app = context.applicationContext
        fun redrawWidgets() {
            val pending = goAsync()
            CoroutineScope(Dispatchers.IO).launch {
                try {
                    Sync.redraw(app)
                } finally {
                    pending.finish()
                }
            }
        }
        when (intent.action) {
            LiveUpdates.ACTION_START -> {
                Diag.i("live update start alarm fired")
                if (Reminders.canNotify(context)) LiveUpdates.startClass(context, intent)
                redrawWidgets()
                return
            }
            LiveUpdates.ACTION_END -> {
                Diag.i("live update end alarm fired")
                LiveUpdates.cancel(context)
                redrawWidgets()
                return
            }
            Reminders.ACTION -> redrawWidgets()
            else -> return
        }
        Diag.i("reminder alarm fired")
        if (!Reminders.canNotify(context)) {
            Diag.w("reminder skipped: notification permission")
            return
        }
        val at = intent.getLongExtra("at", 0L)
        val repo = Repository(context)
        if (!repo.alarmEnabled) {
            Diag.i("reminder skipped: disabled")
            return
        }
        // 灵动显示是普通提醒之上的叠加，不是二选一：额外发一张流体云卡片，普通提醒照常发；
        // 流体云已发出时普通提醒改为静默，避免同一节课响两次。
        val liveShown = repo.liveUpdateEnabled &&
            runCatching { LiveUpdates.post(context, LiveUpdates.itemFrom(intent)) }.getOrDefault(false)
        if (liveShown) Diag.i("live update posted")
        // 手机关机 / 休眠错过太久（超过 5 分钟）就不补发，免得课都上了才响
        if (at > 0 && System.currentTimeMillis() - at > 5 * 60_000L) {
            Diag.i("reminder skipped: stale")
            return
        }
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
            .setSmallIcon(R.drawable.ic_notify_calendar)
            .setLargeIcon(Reminders.appIconBitmap(context))
            .setContentTitle(intent.getStringExtra("title"))
            .setContentText(intent.getStringExtra("text"))
            .setCategory(NotificationCompat.CATEGORY_REMINDER)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setContentIntent(open)
            .setAutoCancel(true)
            .setTimeoutAfter(timeout)
            .setSilent(liveShown)
            .build()
        try {
            NotificationManagerCompat.from(context).notify((at / 60_000L).toInt(), notification)
            Diag.i("reminder notification posted")
        } catch (_: SecurityException) {
            Diag.w("reminder notification failed: security")
        }
    }
}

/** 开机、更新 App、改时区后闹钟会丢：重排提醒并刷新小组件。 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val app = context.applicationContext
        val pending = goAsync()
        CoroutineScope(Dispatchers.IO).launch {
            try {
                Reminders.reschedule(app)
                Sync.armBoundary(app)
                Sync.schedule(app)
                Sync.now(app)
            } finally {
                pending.finish()
            }
        }
    }
}
