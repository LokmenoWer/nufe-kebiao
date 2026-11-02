package wiki.nufe.kebiao.preview

import android.Manifest
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
import wiki.nufe.kebiao.remind.LiveUpdates
import wiki.nufe.kebiao.ui.MainActivity

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
                        Text("流体云 / Live Updates 测试", fontSize = 24.sp, fontWeight = FontWeight.Bold)
                        Text(
                            "Android 16 的 Live Updates，ColorOS 16 的流体云已接入这套标准 API。",
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
                                "是否为 true，再到系统设置 → 通知与控制中心 → 流体云 / 实时通知里允许本应用。",
                            fontSize = 12.sp,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }
        }
    }

    private fun describe(): String {
        val canPromote = if (Build.VERSION.SDK_INT >= 36) {
            manager.canPostPromotedNotifications()
        } else {
            false
        }
        return buildString {
            appendLine("Android ${Build.VERSION.RELEASE} · API ${Build.VERSION.SDK_INT}")
            appendLine("通知总开关：${manager.areNotificationsEnabled()}")
            appendLine("canPostPromotedNotifications：$canPromote")
            if (Build.VERSION.SDK_INT < 36) {
                append("需要 Android 16 / ColorOS 16（API 36+）才能测试 Live Updates。")
            } else if (!canPromote) {
                append("系统未允许 promoted notifications，流体云可能不会出现；请去通知设置里打开。")
            }
        }
    }

    private fun begin() {
        if (Build.VERSION.SDK_INT < 36) {
            status = describe()
            Toast.makeText(this, "需要 Android 16 / ColorOS 16", Toast.LENGTH_LONG).show()
            return
        }
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
        if (Build.VERSION.SDK_INT < 36) return
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
        if (Build.VERSION.SDK_INT < 36) return
        val style = Notification.ProgressStyle()
            .setProgress(progress)
            .setProgressSegments(
                listOf(
                    Notification.ProgressStyle.Segment(50).setColor(0xFF4B63E0.toInt()),
                    Notification.ProgressStyle.Segment(50).setColor(0xFFB9C3FF.toInt()),
                ),
            )
            .setProgressTrackerIcon(Icon.createWithResource(this, R.drawable.ic_notify))
            .setStyledByProgress(true)

        val notification = Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_notify)
            .setContentTitle("流体云测试")
            .setContentText("正在上课 · 算法设计与分析")
            .setSubText("博远楼JC406 · 10:00–11:25")
            .setContentIntent(openAppIntent())
            .setOngoing(true)
            .setRequestPromotedOngoing(true)
            .setShortCriticalText("剩 ${formatRemaining(remainingMs)}")
            .setCategory(Notification.CATEGORY_PROGRESS)
            .setOnlyAlertOnce(true)
            .setStyle(style)
            .build()
        manager.notify(NOTIFICATION_ID, notification)
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
        val intent = Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
            .putExtra(Settings.EXTRA_APP_PACKAGE, packageName)
        startActivity(intent)
    }

    private fun formatRemaining(ms: Long): String {
        val seconds = (ms / 1000).coerceAtLeast(0)
        return String.format(Locale.ROOT, "%d:%02d", seconds / 60, seconds % 60)
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
