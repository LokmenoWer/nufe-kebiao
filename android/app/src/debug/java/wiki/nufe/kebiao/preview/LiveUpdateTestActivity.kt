package wiki.nufe.kebiao.preview

import android.Manifest
import android.app.AlarmManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.drawable.Icon
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import java.util.Locale
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import wiki.nufe.kebiao.R
import wiki.nufe.kebiao.data.Now
import wiki.nufe.kebiao.remind.LiveUpdates
import wiki.nufe.kebiao.remind.OemIslandExtras
import wiki.nufe.kebiao.remind.ReminderReceiver
import wiki.nufe.kebiao.remind.Reminders
import wiki.nufe.kebiao.ui.MainActivity
import wiki.nufe.kebiao.ui.canPostPromotedNotificationsCompat
import wiki.nufe.kebiao.ui.detectLiveUpdateTier
import wiki.nufe.kebiao.ui.detectRomFamily
import wiki.nufe.kebiao.ui.liveUpdateName
import wiki.nufe.kebiao.ui.openPromotedNotificationSettings

/**
 * Android 16 Live Updates / ColorOS 16 流体云测试。
 *
 * 只验证系统展示链路：发一条 promoted ongoing 的 Notification.ProgressStyle 通知，
 * 不涉及 OPPO 插件卡 SDK，也不需要卡片授权码。
 */
class LiveUpdateTestActivity : ComponentActivity() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private var job: Job? = null
    private lateinit var manager: NotificationManager

    private var running by mutableStateOf(false)
    private var status by mutableStateOf("")

    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (granted) startLiveUpdate() else status = "没有通知权限，系统不会显示流体云。"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        manager = getSystemService(NotificationManager::class.java)
        status = describe()

        setContent {
            MaterialTheme {
                Surface(modifier = Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
                    Column(
                        modifier = Modifier
                            .fillMaxSize()
                            .verticalScroll(rememberScrollState())
                            .padding(24.dp),
                        verticalArrangement = Arrangement.spacedBy(12.dp),
                    ) {
                        Text("实时提醒 / Live Updates 测试", fontSize = 24.sp, fontWeight = FontWeight.Bold)
                        Text(
                            "Android 16 Live Updates：ColorOS 显示为流体云，HyperOS 显示为实时动态/焦点通知；" +
                                "超级岛需要小米企业开发者与上架 App ID。",
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        Text(status, fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)

                        Button(
                            enabled = !running,
                            modifier = Modifier.fillMaxWidth(),
                            onClick = { begin() },
                        ) { Text("开始测试") }

                        OutlinedButton(
                            enabled = running,
                            modifier = Modifier.fillMaxWidth(),
                            onClick = { stopLiveUpdate("已停止") },
                        ) { Text("停止并撤销通知") }

                        OutlinedButton(
                            modifier = Modifier.fillMaxWidth(),
                            onClick = {
                                val ok = LiveUpdates.preview(this@LiveUpdateTestActivity)
                                status = if (ok) {
                                    "已用缓存里的下一节课发出真实 Live Update；可以去通知栏/锁屏看。"
                                } else {
                                    "没有可用的课表缓存，或系统尚未允许本应用的流体云提醒。"
                                }
                            },
                        ) { Text("预览下一节课（真实数据）") }

                        OutlinedButton(
                            modifier = Modifier.fillMaxWidth(),
                            onClick = { sendReminderFallback() },
                        ) { Text("测试提醒回退（走真实 ReminderReceiver）") }

                        OutlinedButton(
                            modifier = Modifier.fillMaxWidth(),
                            onClick = { scheduleAlarmTest() },
                        ) { Text("15 秒后闹钟测试（按 Home 后等待）") }

                        OutlinedButton(
                            modifier = Modifier.fillMaxWidth(),
                            onClick = {
                                LiveUpdates.cancel(this@LiveUpdateTestActivity)
                                status = "已停止真实课程提醒。"
                            },
                        ) { Text("停止真实课程提醒") }

                        OutlinedButton(
                            modifier = Modifier.fillMaxWidth(),
                            onClick = { openNotificationSettings() },
                        ) { Text("打开通知设置") }

                        Spacer(Modifier.height(4.dp))
                        Text(
                            "如果开始后状态栏没有胶囊/卡片：先看上面的 canPostPromotedNotifications " +
                                "是否为 true，再到系统设置 → 通知与控制中心 → 流体云 / 实时动态里允许本应用。",
                            fontSize = 12.sp,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }
        }
        if (intent.getBooleanExtra("fallback_test", false)) {
            window.decorView.post { sendReminderFallback() }
        }
        if (intent.getBooleanExtra("alarm_test", false)) {
            window.decorView.post { scheduleAlarmTest() }
        }
        if (intent.getBooleanExtra("near_test", false)) {
            window.decorView.post { sendNearReminder() }
        }
        if (intent.getBooleanExtra("live_test", false)) {
            window.decorView.post { begin() }
        }
    }

    /** 2 分钟后开课，用来验证 Live Update 是否会自动切到“正在上课”并收起。 */
    private fun sendNearReminder() {
        val zone = java.time.ZoneId.of("Asia/Shanghai")
        val now = java.time.ZonedDateTime.now(zone)
        val start = now.plusMinutes(2)
        val end = now.plusMinutes(10)
        val fmt = java.time.format.DateTimeFormatter.ofPattern("HH:mm")
        sendReminder(
            title = "近端测试课程",
            date = now.toLocalDate().toString(),
            start = start.format(fmt),
            end = end.format(fmt),
        )
        status = "已安排 2 分钟后开课的测试；观察流体云是否在开课时切为“正在上课”，并在 5 分钟后收起。"
    }

    /** 走真实 AlarmManager，确认按 Home 退到后台后仍能唤醒进程发通知。 */
    private fun scheduleAlarmTest() {
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            permissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
            return
        }
        val at = System.currentTimeMillis() + 15_000L
        val intent = Intent(this, ReminderReceiver::class.java)
            .setAction(Reminders.ACTION)
            .putExtra("at", at)
            .putExtra("title", "闹钟测试")
            .putExtra("text", "15 秒后唤醒测试")
            .putExtra("date", Now.current().date)
            .putExtra("start", "23:00")
            .putExtra("end", "23:50")
            .putExtra("name", "闹钟测试")
            .putExtra("room", "A101")
        val pending = PendingIntent.getBroadcast(
            this, 990001, intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val show = PendingIntent.getActivity(
            this, 990002, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val alarms = getSystemService(AlarmManager::class.java)
        val scheduled = runCatching {
            alarms.setAlarmClock(AlarmManager.AlarmClockInfo(at, show), pending)
        }.isSuccess
        if (!scheduled) {
            if (Build.VERSION.SDK_INT < 31 || alarms.canScheduleExactAlarms()) {
                alarms.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pending)
            } else {
                alarms.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pending)
            }
        }
        status = "已安排 15 秒后的真实闹钟，请按 Home 退到后台等待通知。"
    }

    /** 走真实的 ReminderReceiver：流体云不可用时应落到普通通知。 */
    private fun sendReminderFallback() {
        sendReminder(
            title = "回退测试课程",
            date = Now.current().date,
            start = "23:00",
            end = "23:50",
        )
        status = "已通过 ReminderReceiver 发送提醒：流体云可用时显示实时卡片，不可用时应为普通通知。"
    }

    private fun sendReminder(title: String, date: String, start: String, end: String) {
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            permissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
            return
        }
        val at = System.currentTimeMillis()
        val intent = Intent(this, ReminderReceiver::class.java)
            .setAction(Reminders.ACTION)
            .putExtra("at", at)
            .putExtra("title", title)
            .putExtra("text", "测试提醒")
            .putExtra("date", date)
            .putExtra("start", start)
            .putExtra("end", end)
            .putExtra("name", title)
            .putExtra("room", "A101")
        sendBroadcast(intent)
    }

    private fun describe(): String {
        val canPromote = canPostPromotedNotificationsCompat(manager)
        val tier = detectLiveUpdateTier()
        val rom = detectRomFamily()
        return buildString {
            appendLine("Android ${Build.VERSION.RELEASE} · API ${Build.VERSION.SDK_INT} · ${rom.displayName}")
            appendLine("灵动名称：${liveUpdateName(rom)} · 支撑层级：${tier.name}")
            appendLine("通知总开关：${manager.areNotificationsEnabled()}")
            appendLine("canPostPromotedNotifications：$canPromote")
            if (Build.VERSION.SDK_INT >= 36 && !canPromote) {
                append("系统未允许 promoted notifications，请去通知设置里打开。")
            }
        }
    }

    private fun begin() {
        if (!manager.areNotificationsEnabled()) {
            status = "通知总开关被关闭了，请先打开通知。"
            openNotificationSettings()
            return
        }
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            permissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
            return
        }
        startLiveUpdate()
    }

    private fun startLiveUpdate() {
        job?.cancel()
        ensureChannel()
        running = true
        status = describe() + "\n\n已发送 Live Update 通知，观察状态栏、通知栏和锁屏。"
        job = scope.launch {
            val totalMs = 3 * 60_000L
            val startedAt = System.currentTimeMillis()
            while (isActive) {
                val elapsed = System.currentTimeMillis() - startedAt
                val remaining = (totalMs - elapsed).coerceAtLeast(0L)
                val progress = ((elapsed.toDouble() / totalMs) * 100).toInt().coerceIn(0, 100)
                postLiveUpdate(progress, remaining)
                if (remaining == 0L) break
                delay(1_000L)
            }
            running = false
            status = "测试结束；通知已保留在通知栏，可以点“停止并撤销通知”。"
        }
    }

    private fun postLiveUpdate(progress: Int, remainingMs: Long) {
        val shortText = "剩 ${formatRemaining(remainingMs)}"
        val titleLine = "10:00-11:25 博远楼JC406"
        val course = "算法设计与分析（双语）"
        val statusText = OemIslandExtras.formatCountdownStatus(
            isBeforeClass = false,
            remainMs = remainingMs,
            chronometer = true,
        )
        val builder = Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_notify_calendar)
            .setContentTitle(titleLine)
            .setContentText(course)
            .setSubText(statusText)
            .setContentIntent(openAppIntent())
            .setOngoing(true)
            .setCategory(Notification.CATEGORY_PROGRESS)
            .setOnlyAlertOnce(true)
            .setShowWhen(false)
            .setUsesChronometer(false)

        if (Build.VERSION.SDK_INT >= 36) {
            runCatching {
                val style = Notification.ProgressStyle()
                    .setProgress(progress)
                    .setProgressSegments(
                        listOf(
                            Notification.ProgressStyle.Segment(50).setColor(0xFF4B63E0.toInt()),
                            Notification.ProgressStyle.Segment(50).setColor(0xFFB9C3FF.toInt()),
                        ),
                    )
                    .setProgressTrackerIcon(Icon.createWithResource(this, R.drawable.ic_notify_calendar))
                    .setStyledByProgress(true)
                builder.setStyle(style)
            }
        }
        applyPromotedFlags(builder, shortText)
        val testItem = Reminders.Item(
            atMs = System.currentTimeMillis(),
            title = "${liveUpdateName()}测试",
            text = "10:00–11:25 · 仙林校区 博远楼 JC406",
            date = Now.current().date,
            start = "10:00",
            end = "11:25",
            name = course,
            room = "仙林校区 博远楼 JC406",
        )
        OemIslandExtras.applyOemExtras(
            context = this,
            builder = builder,
            item = testItem,
            titleLine = titleLine,
            course = course,
            status = statusText,
            shortText = shortText,
            progress = progress,
            showProgress = true,
            isUpdate = true,
            openIntent = openAppIntent(),
        )
        manager.notify(NOTIFICATION_ID, builder.build())
    }

    private fun stopLiveUpdate(message: String) {
        job?.cancel()
        job = null
        if (::manager.isInitialized) manager.cancel(NOTIFICATION_ID)
        running = false
        status = message
    }

    private fun ensureChannel() {
        val channel = NotificationChannel(
            CHANNEL_ID,
            "流体云测试",
            NotificationManager.IMPORTANCE_LOW,
        ).apply {
            description = "Android 16 Live Updates / ColorOS 流体云测试"
        }
        manager.createNotificationChannel(channel)
    }

    private fun openAppIntent(): PendingIntent {
        val intent = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
        }
        return PendingIntent.getActivity(
            this,
            0,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    }

    private fun openNotificationSettings() {
        openPromotedNotificationSettings(this)
    }

    private fun formatRemaining(ms: Long): String {
        val seconds = (ms / 1000).coerceAtLeast(0)
        return String.format(Locale.ROOT, "%d:%02d", seconds / 60, seconds % 60)
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

    override fun onDestroy() {
        stopLiveUpdate("已退出测试")
        scope.cancel()
        super.onDestroy()
    }

    companion object {
        private const val CHANNEL_ID = "live_update_test_v1"
        private const val NOTIFICATION_ID = 9001
    }
}
