package wiki.nufe.kebiao.ui

import android.content.Intent
import android.widget.Toast
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import wiki.nufe.kebiao.assist.AssistWorker
import wiki.nufe.kebiao.assist.Community
import wiki.nufe.kebiao.data.CasSessionManager
import wiki.nufe.kebiao.data.Hosting
import wiki.nufe.kebiao.data.Repository

/**
 * 互助与同步：
 * - 服务器托管（人人为我）：手机刷新失败时，服务器和同学们的手机帮你继续更新。
 * - 协助刷新（我为人人）：这台手机在 Wi-Fi 下帮网页版同学转发加密的课表请求。
 * - iOS 数据源：手机刷到课表就推给本站，转发给 Scriptable 小组件与日历订阅。
 */
@Composable
internal fun AssistSection(repo: Repository, onLogin: () -> Unit, onHostingChanged: () -> Unit = {}) {
    val context = LocalContext.current
    val view = LocalView.current
    val clipboard = LocalClipboardManager.current
    val scope = rememberCoroutineScope()
    var version by remember { mutableIntStateOf(0) }
    val hosting = remember(version) { Hosting.state(context) }
    var enabled by remember(version) { mutableStateOf(Community.assistEnabled(context)) }
    val available = remember(enabled) { Community.assistAvailable(context) }
    val helped = remember(enabled) { Community.helpedCount(context) }
    var confirmLeave by remember { mutableStateOf(false) }
    // 三个开关的联网操作互斥；busyOn 记下正在处理哪一项，只在那一项显示“处理中”。
    var busyOn by remember { mutableStateOf<String?>(null) }
    val leaving = busyOn != null
    var links by remember { mutableStateOf<JSONObject?>(null) }
    var dataMessage by remember { mutableStateOf<String?>(null) }
    var hostMessage by remember { mutableStateOf<String?>(null) }
    val divider = MaterialTheme.colorScheme.outline.copy(alpha = 0.7f)

    LaunchedEffect(hosting.dataSource, version) {
        links = if (hosting.dataSource) withContext(Dispatchers.IO) {
            runCatching { Hosting.links(context) }.getOrNull()
        } else null
    }

    fun enrollHosting() {
        if (leaving) return
        hostMessage = null
        busyOn = "host"
        scope.launch {
            val result = withContext(Dispatchers.IO) {
                runCatching {
                    Hosting.enroll(context, CasSessionManager(context).load(), keepSession = true)
                }
            }
            busyOn = null
            version++
            onHostingChanged()
            result.exceptionOrNull()?.let {
                hostMessage = "开启失败：${it.message ?: "网络错误"}。如本机学校登录已失效，请先在「本地学校登录」重新导入。"
            }
        }
    }

    fun toggleDataSource(on: Boolean) {
        if (leaving) return
        busyOn = "data"
        dataMessage = null
        scope.launch {
            val result = withContext(Dispatchers.IO) {
                runCatching {
                    if (on) {
                        if (Hosting.active(context)) {
                            Hosting.setDataSource(context, true)
                        } else {
                            Hosting.enroll(context, CasSessionManager(context).load(), keepSession = false, dataSource = true)
                        }
                    } else {
                        Hosting.setDataSource(context, false)
                        if (!Hosting.state(context).hosted) Hosting.leave(context)
                    }
                }
            }
            if (on && result.isSuccess) repo.pushToServer()
            result.exceptionOrNull()?.let {
                dataMessage = "开启失败：${it.message ?: "网络错误"}。若本机学校登录已过期，请先重新导入课表。"
            }
            busyOn = null
            version++
            onHostingChanged()
        }
    }

    SectionCard {
        Text("互助与同步", fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
        Spacer(Modifier.height(4.dp))
        Hint("人人为我，我为人人：全部可选，随时可关；开启后才会联网协作，不影响本机课表使用。")
        Spacer(Modifier.height(12.dp))

        // ---- 服务器托管（人人为我）----
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("服务器托管", fontSize = 14.sp, fontWeight = FontWeight.Medium)
                    Spacer(Modifier.width(6.dp))
                    Tag(
                        when {
                            busyOn == "host" -> "处理中"
                            !hosting.hosted -> "未开启"
                            hosting.status == "ok" -> "已同步"
                            else -> "已开启"
                        },
                        when {
                            busyOn == "host" || !hosting.hosted -> MaterialTheme.colorScheme.onSurfaceVariant
                            hosting.status == "ok" -> SuccessGreen
                            else -> WarmOrange
                        },
                    )
                }
                Spacer(Modifier.height(2.dp))
                Hint(
                    when {
                        !hosting.hosted -> "本机刷新失败时，由服务器和同学们的手机帮你继续更新。"
                        hosting.status == "ok" -> "服务器那份正常更新，手机直连失败时自动兜底。"
                        else -> hosting.error ?: "服务器那份暂时无法刷新，本机刷新成功后会自动补交。"
                    },
                )
            }
            Spacer(Modifier.width(8.dp))
            Switch(
                checked = hosting.hosted,
                enabled = !leaving,
                onCheckedChange = {
                    view.performHapticFeedback(android.view.HapticFeedbackConstants.VIRTUAL_KEY)
                    if (it) enrollHosting() else confirmLeave = true
                },
            )
        }
        hostMessage?.let {
            Spacer(Modifier.height(4.dp))
            Text(it, fontSize = 12.sp, color = MaterialTheme.colorScheme.error)
        }

        HorizontalDivider(modifier = Modifier.padding(vertical = 12.dp), color = divider)

        // ---- 协助刷新（我为人人）----
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("协助刷新", fontSize = 14.sp, fontWeight = FontWeight.Medium)
                    Spacer(Modifier.width(6.dp))
                    Tag(
                        if (enabled) "已开启" else "未开启",
                        if (enabled) SuccessGreen else MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                Spacer(Modifier.height(2.dp))
                Hint(
                    when {
                        !enabled -> "在 Wi-Fi 且电量充足时，帮网页版同学转发加密的课表请求。"
                        available == false -> "服务器暂未开放，当前不产生任何流量。"
                        helped > 0 -> "已帮同学刷新 $helped 次，谢谢你！只会连接 jwxt.nufe.edu.cn。"
                        else -> "等待服务器分配任务；约每半小时 2 分钟，每天流量 < 30 MB。"
                    },
                )
            }
            Spacer(Modifier.width(8.dp))
            Switch(
                checked = enabled,
                onCheckedChange = {
                    view.performHapticFeedback(android.view.HapticFeedbackConstants.VIRTUAL_KEY)
                    enabled = it
                    Community.setAssistEnabled(context, it)
                },
            )
        }

        HorizontalDivider(modifier = Modifier.padding(vertical = 12.dp), color = divider)

        // ---- iOS 小组件与日历数据源 ----
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("iOS 小组件与日历", fontSize = 14.sp, fontWeight = FontWeight.Medium)
                    Spacer(Modifier.width(6.dp))
                    Tag(
                        when {
                            busyOn == "data" -> "处理中"
                            !hosting.dataSource -> "未开启"
                            hosting.pushedAtMs > 0 -> "已同步"
                            else -> "等待推送"
                        },
                        when {
                            !hosting.dataSource -> MaterialTheme.colorScheme.onSurfaceVariant
                            hosting.pushedAtMs > 0 -> SuccessGreen
                            else -> WarmOrange
                        },
                    )
                }
                Spacer(Modifier.height(2.dp))
                Hint(
                    when {
                        !hosting.dataSource -> "没有另一台 iOS 设备请忽略本条。有的话，可把课表推给 iPhone 的 Scriptable 小组件与日历订阅。"
                        hosting.pushedAtMs > 0 -> "最近推送 ${clock(hosting.pushedAtMs)}；手机刷到新课表即转发。"
                        else -> "已开启，等待第一次推送。"
                    },
                )
            }
            Spacer(Modifier.width(8.dp))
            Switch(
                checked = hosting.dataSource,
                enabled = !leaving,
                onCheckedChange = {
                    view.performHapticFeedback(android.view.HapticFeedbackConstants.VIRTUAL_KEY)
                    toggleDataSource(it)
                },
            )
        }
        dataMessage?.let {
            Spacer(Modifier.height(4.dp))
            Text(it, fontSize = 12.sp, color = MaterialTheme.colorScheme.error)
            TextButton(onClick = onLogin) { Text("重新导入课表") }
        }
        val current = links
        if (hosting.dataSource && current != null) {
            Spacer(Modifier.height(6.dp))
            fun copy(label: String, key: String) {
                val url = current.optString(key).takeIf { it.isNotEmpty() } ?: return
                clipboard.setText(AnnotatedString(url))
                Toast.makeText(context, "已复制$label", Toast.LENGTH_SHORT).show()
            }
            LinkRow("小组件脚本链接", "复制") { copy("小组件脚本链接", "script_url") }
            LinkRow("日历订阅链接", "复制") { copy("日历订阅链接", "webcal_url") }
            LinkRow("分享到 iPhone", "分享") {
                val text = "nufe课表 iOS 小组件脚本：${current.optString("script_url")}\n日历订阅：${current.optString("webcal_url")}"
                runCatching {
                    context.startActivity(
                        Intent.createChooser(
                            Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, text),
                            "发送到 iPhone",
                        ),
                    )
                }
            }
            Spacer(Modifier.height(2.dp))
            Hint("链接相当于只读凭证，只发给自己的设备。")
        }
    }

    if (confirmLeave) {
        AlertDialog(
            onDismissRequest = { confirmLeave = false },
            title = { Text("取消服务器托管？") },
            text = {
                Text("服务器将删除为你保存的学校登录状态并撤销这台设备，之后只靠本机刷新。本机课表不受影响。", fontSize = 13.sp)
            },
            confirmButton = {
                TextButton(onClick = {
                    confirmLeave = false
                    busyOn = "host"
                    scope.launch {
                        withContext(Dispatchers.IO) {
                            // 还在给 iOS 小组件 / 日历供数：只删本站的登录态，设备与链接保留
                            if (Hosting.dataSource(context)) runCatching { Hosting.forgetSession(context) } else Hosting.leave(context)
                        }
                        busyOn = null
                        version++
                        onHostingChanged()
                    }
                }) { Text("取消托管") }
            },
            dismissButton = { TextButton(onClick = { confirmLeave = false }) { Text("保留") } },
        )
    }
}

/** 一行可复制的链接：左标题、右操作。 */
@Composable
private fun LinkRow(title: String, action: String, onClick: () -> Unit) {
    Row(modifier = Modifier.fillMaxWidth().height(40.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(title, fontSize = 13.sp, modifier = Modifier.weight(1f))
        TextButton(onClick = onClick) { Text(action, fontSize = 13.sp) }
    }
}

private fun clock(ms: Long): String = SimpleDateFormat("M/d HH:mm", Locale.CHINA).format(Date(ms))

/** App 启动时调用：排好心跳与（若已开启）协助任务。 */
internal fun scheduleCommunity(context: android.content.Context) {
    wiki.nufe.kebiao.assist.HeartbeatWorker.schedule(context)
    AssistWorker.schedule(context)
}
