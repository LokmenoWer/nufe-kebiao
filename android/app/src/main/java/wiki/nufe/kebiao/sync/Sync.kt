package wiki.nufe.kebiao.sync

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.glance.appwidget.updateAll
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.OutOfQuotaPolicy
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import wiki.nufe.kebiao.data.Now
import wiki.nufe.kebiao.data.Payload
import wiki.nufe.kebiao.data.Repository
import wiki.nufe.kebiao.data.lessonMs
import wiki.nufe.kebiao.data.resolveDays
import wiki.nufe.kebiao.diag.Diag
import wiki.nufe.kebiao.remind.Reminders
import wiki.nufe.kebiao.widget.TodayWidget
import wiki.nufe.kebiao.widget.WeekWidget
import wiki.nufe.kebiao.widget.WidgetVersion

/** 学校刷新交给 WorkManager；上下课和零点的精确闹钟只重画本地课表。 */
object Sync {
    private const val PERIODIC = "sync-periodic"
    private const val NEXT = "sync-next"
    private const val NOW = "sync-now"
    private const val AFTER_CHECK_MINUTES = 25
    private const val MAX_REDRAW_MS = 30 * 60_000L
    private const val MIN_REDRAW_MS = 5_000L
    private const val BOUNDARY_REQUEST = 1000

    fun schedule(context: Context) {
        val request = PeriodicWorkRequestBuilder<SyncWorker>(30, TimeUnit.MINUTES)
            .setConstraints(Constraints(requiredNetworkType = NetworkType.CONNECTED))
            .build()
        WorkManager.getInstance(context).enqueueUniquePeriodicWork(PERIODIC, ExistingPeriodicWorkPolicy.KEEP, request)
    }

    /** 马上从学校刷新；refresh = true 跳过短时间内的缓存复用。 */
    fun now(context: Context, refresh: Boolean = false) {
        val request = OneTimeWorkRequestBuilder<SyncWorker>()
            .setInputData(workDataOf(SyncWorker.REFRESH to refresh))
            .setExpedited(OutOfQuotaPolicy.RUN_AS_NON_EXPEDITED_WORK_REQUEST)
            .build()
        WorkManager.getInstance(context).enqueueUniqueWork(NOW, ExistingWorkPolicy.REPLACE, request)
    }

    fun cancelAll(context: Context) {
        WorkManager.getInstance(context).apply {
            cancelUniqueWork(PERIODIC)
            cancelUniqueWork(NEXT)
            cancelUniqueWork(NOW)
        }
    }

    fun nextRedrawMs(payload: Payload, now: Now): Long {
        val dayStartMs = lessonMs(now.date, "00:00")
        val candidates = mutableListOf(now.ms + MAX_REDRAW_MS, dayStartMs + (24 * 60 + 1) * 60_000L)
        val (today, _) = resolveDays(payload, now)
        for (lesson in today?.lessons.orEmpty()) {
            val marks = listOf(lesson.startMinutes - AFTER_CHECK_MINUTES, lesson.startMinutes, lesson.endMinutes)
            for (t in marks) {
                val targetMs = dayStartMs + t * 60_000L
                if (targetMs > now.ms) candidates += targetMs
            }
        }
        return maxOf(now.ms + MIN_REDRAW_MS, candidates.min())
    }

    /** 下一个小组件内容会变的时刻：今天最近的上下课（含停课的结束时间），没有了就是零点后。 */
    fun nextBoundaryMs(payload: Payload, now: Now): Long {
        val dayStartMs = lessonMs(now.date, "00:00")
        val (today, _) = resolveDays(payload, now)
        val marks = today?.lessons.orEmpty().flatMap { listOf(it.startMinutes, it.endMinutes) } +
            today?.cancelled.orEmpty().map { it.endMinutes }
        return marks.map { dayStartMs + it * 60_000L }.filter { it > now.ms }.minOrNull()
            ?: (dayStartMs + (24 * 60 + 1) * 60_000L)
    }

    /**
     * 按缓存排下一次上下课重画。同一个 PendingIntent 重复排只会替换；到点后 [redraw] 接着排下一次。
     * 闹钟时钟可能在状态栏显示图标，只在需要它的 ROM 上、用户打开“小组件准时切换”时才用，和上课通知开关无关。
     */
    fun armBoundary(context: Context) {
        val app = context.applicationContext
        val repo = Repository(app)
        val pending = PendingIntent.getBroadcast(
            app, BOUNDARY_REQUEST, Intent(app, WidgetBoundaryReceiver::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val payload = repo.cached()?.payload?.takeIf { repo.hasTimetable }
        if (payload == null) {
            app.getSystemService(AlarmManager::class.java).cancel(pending)
            return
        }
        // 调试预览会拨时钟（Now.offsetMs），闹钟要换算回真实时间，否则排到过去会立刻响、反复重画
        val atMs = nextBoundaryMs(payload, Now.current()) - Now.offsetMs
        Reminders.schedule(app, atMs, BOUNDARY_REQUEST, pending, Reminders.needsAlarmClock() && repo.widgetPunctual)
    }

    internal fun scheduleNext(context: Context, atMs: Long) {
        val delay = (atMs - System.currentTimeMillis()).coerceAtLeast(MIN_REDRAW_MS)
        val request = OneTimeWorkRequestBuilder<SyncWorker>()
            .setInitialDelay(delay, TimeUnit.MILLISECONDS)
            .setBackoffCriteria(BackoffPolicy.LINEAR, 5, TimeUnit.MINUTES)
            .build()
        WorkManager.getInstance(context).enqueueUniqueWork(NEXT, ExistingWorkPolicy.REPLACE, request)
    }

    /** 只按缓存重画所有小组件（不联网）。 */
    suspend fun redraw(context: Context) {
        withContext(Dispatchers.IO) { armBoundary(context) }
        WidgetVersion.bump()
        TodayWidget().updateAll(context)
        WeekWidget().updateAll(context)
    }
}

class SyncWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val repo = Repository(applicationContext)
        if (!repo.hasTimetable) return Result.success()
        val snapshot = repo.fetch(refresh = inputData.getBoolean(REFRESH, false))
        snapshot?.let { LoginAlert.update(applicationContext, it.payload) }
        Reminders.reschedule(applicationContext)
        Sync.redraw(applicationContext)
        val payload = snapshot?.payload
        val next = if (payload != null) Sync.nextRedrawMs(payload, Now.current())
        else System.currentTimeMillis() + 30 * 60_000L
        // 离线时也按时重画（课的状态要按时切换），但不要比 5 分钟更勤
        Sync.scheduleNext(applicationContext, if (snapshot?.offline == true) maxOf(next, System.currentTimeMillis() + 5 * 60_000L) else next)
        return Result.success()
    }

    companion object {
        const val REFRESH = "refresh"
    }
}

/** 上下课闹钟到点：只按缓存重画，[Sync.redraw] 会排好下一次。 */
class WidgetBoundaryReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val app = context.applicationContext
        val pending = goAsync()
        CoroutineScope(Dispatchers.IO).launch {
            try {
                Diag.i("widget boundary alarm fired")
                Reminders.reschedule(app)
                Sync.redraw(app)
            } finally {
                pending.finish()
            }
        }
    }
}

