package wiki.nufe.kebiao.diag

import android.app.AlarmManager
import android.app.NotificationManager
import android.appwidget.AppWidgetManager
import android.content.ClipData
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.PowerManager
import android.os.Process
import android.util.Log
import android.widget.Toast
import androidx.core.content.FileProvider
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import wiki.nufe.kebiao.data.Repository
import wiki.nufe.kebiao.data.Snapshot
import wiki.nufe.kebiao.remind.Reminders
import wiki.nufe.kebiao.widget.TodayReceiver
import wiki.nufe.kebiao.widget.WeekReceiver

/** 轻量日志：正式包里也只走 logcat，导出时读取本进程最近的日志。 */
object Diag {
    const val TAG = "nufe-kebiao"

    fun i(message: String) = Log.i(TAG, message)
    fun w(message: String) = Log.w(TAG, message)
}

object Diagnostics {
    private const val MAX_LOG_CHARS = 200_000

    /** 生成诊断文件；只包含链接域名和状态，不写入 token、密码或邮箱。 */
    fun export(context: Context, repo: Repository, snapshot: Snapshot?): File {
        val dir = File(context.cacheDir, "diagnostics").apply { mkdirs() }
        dir.listFiles()
            ?.sortedByDescending { it.lastModified() }
            ?.drop(5)
            ?.forEach { it.delete() }
        val stamp = SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US).format(Date())
        val file = File(dir, "nufe-kebiao-$stamp.txt")
        file.writeText(redact(report(context, repo, snapshot)))
        return file
    }

    fun share(context: Context, file: File) {
        val uri = FileProvider.getUriForFile(
            context,
            "${context.packageName}.fileprovider",
            file,
        )
        val send = Intent(Intent.ACTION_SEND).apply {
            type = "text/plain"
            putExtra(Intent.EXTRA_STREAM, uri)
            putExtra(Intent.EXTRA_SUBJECT, "nufe课表诊断日志")
            clipData = ClipData.newUri(context.contentResolver, "diagnostics", uri)
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
        runCatching { context.startActivity(Intent.createChooser(send, "导出诊断日志")) }
            .onFailure { Toast.makeText(context, "没有可用的分享应用", Toast.LENGTH_SHORT).show() }
    }

    private fun report(context: Context, repo: Repository, snapshot: Snapshot?): String {
        val pkg = context.packageManager.getPackageInfo(context.packageName, 0)
        val version = pkg.versionName.orEmpty()
        val code = if (Build.VERSION.SDK_INT >= 28) {
            pkg.longVersionCode.toString()
        } else {
            @Suppress("DEPRECATION")
            pkg.versionCode.toString()
        }
        val notifications = context.getSystemService(NotificationManager::class.java)
        val alarms = context.getSystemService(AlarmManager::class.java)
        val widgets = AppWidgetManager.getInstance(context)
        val host = repo.widgetUrl?.let { runCatching { Uri.parse(it).host }.getOrNull() }
        val todayIds = widgets.getAppWidgetIds(ComponentName(context, TodayReceiver::class.java)).size
        val weekIds = widgets.getAppWidgetIds(ComponentName(context, WeekReceiver::class.java)).size
        val (alarmCount, nextAlarm) = Reminders.scheduledInfo(context)
        val payload = snapshot?.payload

        return buildString {
            appendLine("nufe课表 诊断日志")
            appendLine("生成时间：${time(System.currentTimeMillis())}")
            appendLine("版本：$version ($code)")
            appendLine("Android：${Build.VERSION.RELEASE} (SDK ${Build.VERSION.SDK_INT})")
            appendLine("设备：${Build.MANUFACTURER} ${Build.MODEL} (${Build.DEVICE})")
            appendLine("时区：${TimeZone.getDefault().id}，语言：${Locale.getDefault()}")
            appendLine("链接：${if (host == null) "未连接" else "已连接，host=$host"}")
            appendLine("缓存：${snapshot?.fetchedAtMs?.let(::time) ?: "无"}")
            appendLine("离线：${snapshot?.offline == true}")
            appendLine("服务状态：${payload?.status ?: "无"}")
            appendLine("设备已绑定：${repo.deviceId != null}")
            appendLine("提醒：提前 ${repo.remindMinutes} 分钟，上课通知 ${repo.alarmEnabled}，流体云 ${repo.liveUpdateEnabled}")
            appendLine("提醒队列（上次排定，未到点）：$alarmCount 个${nextAlarm?.let { "，下次 ${time(it)}" } ?: ""}")
            appendLine("通知权限：${Reminders.canNotify(context)}")
            appendLine(
                "后台运行：${
                    context.getSystemService(PowerManager::class.java)
                        ?.isIgnoringBatteryOptimizations(context.packageName) == true
                }",
            )
            appendLine("准时提醒：${Build.VERSION.SDK_INT < 31 || alarms?.canScheduleExactAlarms() == true}")
            appendLine(
                "系统实时提醒：${
                    Build.VERSION.SDK_INT < 36 ||
                        notifications?.canPostPromotedNotifications() == true
                }",
            )
            appendLine("小组件：今天 $todayIds 个，本周 $weekIds 个")
            appendLine("小组件准时切换：${repo.widgetPunctual}，ROM 需要闹钟时钟：${Reminders.needsAlarmClock()}")
            appendLine()
            appendLine("--- 最近日志 ---")
            appendLine(logcat().takeLast(MAX_LOG_CHARS))
        }
    }

    private fun logcat(): String = runCatching {
        val process = ProcessBuilder(
            "logcat",
            "-d",
            "-v",
            "threadtime",
            "-t",
            "4000",
            "--pid=${Process.myPid()}",
        ).redirectErrorStream(true).start()
        val text = process.inputStream.bufferedReader().use { it.readText() }
        process.waitFor()
        text.ifBlank { "（没有读到日志）" }
    }.getOrElse { "读取日志失败：${it.javaClass.simpleName}" }

    private fun redact(text: String): String = text
        .replace(Regex("""/(mobile|widget)/[A-Za-z0-9_-]{16,}(\.json)?"""), "/$1/<redacted>")
        .replace(Regex("""nufekebiao://setup\?url=[^\s]+"""), "nufekebiao://setup?url=<redacted>")
        .replace(Regex("""[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"""), "<email>")

    private fun time(ms: Long): String =
        SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).format(Date(ms))
}
