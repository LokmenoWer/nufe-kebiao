package wiki.nufe.kebiao.sync

import android.content.Context
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
import wiki.nufe.kebiao.data.LinkInvalidException
import wiki.nufe.kebiao.data.Now
import wiki.nufe.kebiao.data.Payload
import wiki.nufe.kebiao.data.Repository
import wiki.nufe.kebiao.data.lessonMs
import wiki.nufe.kebiao.data.resolveDays
import wiki.nufe.kebiao.remind.Reminders
import wiki.nufe.kebiao.widget.TodayWidget
import wiki.nufe.kebiao.widget.WeekWidget
import wiki.nufe.kebiao.widget.WidgetVersion

/**
 * 什么时候拉数据、什么时候重画：
 * - 每 30 分钟一次的周期任务兜底（系统可能推迟，国产 ROM 需要允许后台运行）；
 * - 另排一次“下一个时刻”：最近的上下课、课前 25 分钟（服务器刚在课前半小时确认完课表）、北京时间零点后，
 *   服务器正在向学校拉课表时改为大约 2 分钟后，这样“正在上课 / 下一节”能按时切换；
 * - 绑定过的设备替服务器拉课表：再加课前 37 分钟（自己去学校确认这节课）和服务器给的 poll_after。
 */
object Sync {
    private const val PERIODIC = "sync-periodic"
    private const val NEXT = "sync-next"
    private const val NOW = "sync-now"
    private const val AFTER_CHECK_MINUTES = 25
    private const val DELEGATE_CHECK_MINUTES = 37
    private const val PENDING_REFRESH_MS = 2 * 60_000L
    private const val MAX_REDRAW_MS = 30 * 60_000L
    private const val MIN_REDRAW_MS = 5_000L

    fun schedule(context: Context) {
        val request = PeriodicWorkRequestBuilder<SyncWorker>(30, TimeUnit.MINUTES)
            .setConstraints(Constraints(requiredNetworkType = NetworkType.CONNECTED))
            .build()
        WorkManager.getInstance(context).enqueueUniquePeriodicWork(PERIODIC, ExistingPeriodicWorkPolicy.KEEP, request)
    }

    /** 马上同步一次；refresh = true 时请服务器插队向学校拉课表。 */
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
            val marks = mutableListOf(lesson.startMinutes - AFTER_CHECK_MINUTES, lesson.startMinutes, lesson.endMinutes)
            if (payload.delegate) marks += lesson.startMinutes - DELEGATE_CHECK_MINUTES
            for (t in marks) {
                val targetMs = dayStartMs + t * 60_000L
                if (targetMs > now.ms) candidates += targetMs
            }
        }
        if (payload.delegate) payload.pollAfterSeconds?.takeIf { it > 0 }?.let { candidates += now.ms + it * 1000 }
        if (payload.refresh.pending || payload.refresh.due) candidates += now.ms + PENDING_REFRESH_MS
        return maxOf(now.ms + MIN_REDRAW_MS, candidates.min())
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
        WidgetVersion.bump()
        TodayWidget().updateAll(context)
        WeekWidget().updateAll(context)
    }
}

class SyncWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val repo = Repository(applicationContext)
        if (repo.widgetUrl == null) return Result.success()
        val snapshot = try {
            repo.fetch(refresh = inputData.getBoolean(REFRESH, false))
        } catch (_: LinkInvalidException) {
            LinkState.markInvalid(applicationContext, true)
            Sync.redraw(applicationContext)
            return Result.success()
        }
        LinkState.markInvalid(applicationContext, false)
        snapshot?.takeUnless { it.offline }?.let { LoginAlert.update(applicationContext, it.payload) }
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

/** 服务器说链接不存在（被重新生成 / 停用 / 删号）：小组件和 App 提示重新粘贴。 */
object LinkState {
    fun isInvalid(context: Context) =
        context.getSharedPreferences("kebiao", Context.MODE_PRIVATE).getBoolean("link_invalid", false)

    fun markInvalid(context: Context, invalid: Boolean) {
        context.getSharedPreferences("kebiao", Context.MODE_PRIVATE).edit().putBoolean("link_invalid", invalid).apply()
    }
}
