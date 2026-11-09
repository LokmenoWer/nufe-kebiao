package wiki.nufe.kebiao.assist

import android.content.Context
import android.net.ConnectivityManager
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import java.time.LocalDate
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import wiki.nufe.kebiao.data.SHANGHAI
import wiki.nufe.kebiao.diag.Diag

/** 每天一次匿名心跳；只要求联网。 */
class HeartbeatWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        if (Community.heartbeat(applicationContext)) Result.success() else Result.retry()
    }

    companion object {
        private const val PERIODIC = "community-heartbeat"
        private const val NOW = "community-heartbeat-now"
        private val network = Constraints(requiredNetworkType = NetworkType.CONNECTED)

        fun schedule(context: Context) {
            val request = PeriodicWorkRequestBuilder<HeartbeatWorker>(1, TimeUnit.DAYS).setConstraints(network).build()
            WorkManager.getInstance(context).enqueueUniquePeriodicWork(PERIODIC, ExistingPeriodicWorkPolicy.KEEP, request)
        }

        fun now(context: Context) {
            val request = OneTimeWorkRequestBuilder<HeartbeatWorker>().setConstraints(network).build()
            WorkManager.getInstance(context).enqueueUniqueWork(NOW, ExistingWorkPolicy.REPLACE, request)
        }
    }
}

/**
 * 协助刷新（人人为我，我为人人）：用户打开后，每半小时在 Wi-Fi、电量不低时问一次服务器有没有活。
 * 一次最多帮 [MAX_TASKS] 个人、[RUN_MS] 毫秒；每天流量封顶 [DAILY_BYTES]。没活时只是一个很小的请求。
 */
class AssistWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        val app = applicationContext
        if (!Community.assistEnabled(app)) return@withContext Result.success()
        // WorkManager 已要求不计流量的网络；再看一眼，系统偶尔会把热点当成 Wi-Fi
        if (app.getSystemService(ConnectivityManager::class.java)?.isActiveNetworkMetered != false) {
            return@withContext Result.success()
        }
        if (Community.heartbeatDue(app, 12 * 3600_000L)) Community.heartbeat(app)
        if (Community.assistAvailable(app) == false) return@withContext Result.success()
        val budget = DataBudget(app)
        val deadline = System.currentTimeMillis() + RUN_MS
        val site = Community.site(app)
        repeat(MAX_TASKS) {
            if (isStopped || System.currentTimeMillis() > deadline - 20_000) return@withContext Result.success()
            val left = budget.remaining()
            if (left <= 0) return@withContext Result.success()
            val task = claim(site, Community.installId(app)) ?: return@withContext Result.success()
            val relay = AssistRelay(Community.http, site, task.getString("session"), task.getString("token"))
            val outcome = relay.run(deadline, minOf(left, PER_TASK_BYTES))
            budget.spend(relay.bytes)
            Diag.i("assist task outcome=$outcome bytes=${relay.bytes}")
            if (outcome == "ok") Community.recordHelped(app)
            if (outcome == null) return@withContext Result.success()  // 半路断了：这一轮到此为止
        }
        Result.success()
    }

    private fun claim(site: String, installId: String): JSONObject? {
        val request = Request.Builder().url("$site/api/assist/claim")
            .header("User-Agent", "nufe-kebiao-android/2")
            .post(JSONObject().put("install_id", installId).toString().toRequestBody(Community.JSON)).build()
        return runCatching {
            Community.http.newCall(request).execute().use { response ->
                if (!response.isSuccessful) null
                else JSONObject(response.body?.string().orEmpty()).optJSONObject("task")
            }
        }.getOrNull()
    }

    /** 当天已用流量，跨天清零。 */
    private class DataBudget(context: Context) {
        private val prefs = context.getSharedPreferences("community", Context.MODE_PRIVATE)
        private val today = LocalDate.now(SHANGHAI).toString()

        fun remaining(): Long {
            val used = if (prefs.getString("bytes_day", null) == today) prefs.getLong("bytes_used", 0L) else 0L
            return DAILY_BYTES - used
        }

        fun spend(n: Long) {
            val used = DAILY_BYTES - remaining() + n
            prefs.edit().putString("bytes_day", today).putLong("bytes_used", used).apply()
        }
    }

    companion object {
        private const val PERIODIC = "community-assist"
        const val MAX_TASKS = 3
        const val RUN_MS = 120_000L
        const val PER_TASK_BYTES = 4L * 1024 * 1024
        const val DAILY_BYTES = 30L * 1024 * 1024

        /** 开着就排周期任务，关了就撤掉。 */
        fun schedule(context: Context) {
            val wm = WorkManager.getInstance(context)
            if (!Community.assistEnabled(context)) {
                wm.cancelUniqueWork(PERIODIC)
                return
            }
            val constraints = Constraints.Builder()
                .setRequiredNetworkType(NetworkType.UNMETERED)
                .setRequiresBatteryNotLow(true)
                .build()
            val request = PeriodicWorkRequestBuilder<AssistWorker>(30, TimeUnit.MINUTES)
                .setConstraints(constraints)
                .build()
            wm.enqueueUniquePeriodicWork(PERIODIC, ExistingPeriodicWorkPolicy.KEEP, request)
        }

        /** 调试用：立刻跑一次（仍然受网络和电量条件约束）。 */
        fun runOnce(context: Context) {
            val constraints = Constraints.Builder().setRequiredNetworkType(NetworkType.UNMETERED).build()
            WorkManager.getInstance(context).enqueueUniqueWork(
                "community-assist-now", ExistingWorkPolicy.REPLACE,
                OneTimeWorkRequestBuilder<AssistWorker>().setConstraints(constraints).build(),
            )
        }
    }
}
