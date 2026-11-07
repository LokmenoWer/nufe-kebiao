package wiki.nufe.kebiao.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
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
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import wiki.nufe.kebiao.assist.AssistWorker
import wiki.nufe.kebiao.assist.Community
import wiki.nufe.kebiao.data.Hosting

/**
 * “人人为我，我为人人”：
 * - 人人为我 = 服务器托管：手机刷新失败时，服务器和同学们的手机帮你继续更新（登录时勾选）；
 * - 我为人人 = 协助刷新：这台手机在 Wi-Fi 下帮网页版同学转发加密的课表请求。
 */
@Composable
internal fun AssistSection(onLogin: () -> Unit) {
    val context = LocalContext.current
    val view = LocalView.current
    val scope = rememberCoroutineScope()
    var enabled by remember { mutableStateOf(Community.assistEnabled(context)) }
    var hostingVersion by remember { mutableIntStateOf(0) }
    val hosting = remember(hostingVersion) { Hosting.state(context) }
    var confirmLeave by remember { mutableStateOf(false) }
    var leaving by remember { mutableStateOf(false) }
    val available = remember(enabled) { Community.assistAvailable(context) }
    val helped = remember(enabled) { Community.helpedCount(context) }
    val divider = MaterialTheme.colorScheme.outline.copy(alpha = 0.7f)

    SectionCard {
        Text("人人为我，我为人人", fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
        Spacer(Modifier.height(10.dp))

        // ---- 人人为我：服务器托管 ----
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("服务器托管（双重保障）", fontSize = 14.sp)
                Spacer(Modifier.height(2.dp))
                Text(
                    when {
                        !hosting.active -> "未开启"
                        hosting.status == "ok" -> "已开启 · 服务器那份正常更新"
                        else -> "已开启 · 服务器那份暂时无法刷新，本机刷新成功后会自动补交"
                    },
                    fontSize = 11.sp,
                    color = if (hosting.active && hosting.status == "ok") MaterialTheme.colorScheme.primary
                    else MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            if (hosting.active) {
                TextButton(onClick = { confirmLeave = true }, enabled = !leaving) { Text(if (leaving) "取消中…" else "取消托管") }
            } else {
                TextButton(onClick = onLogin) { Text("去开启") }
            }
        }
        Spacer(Modifier.height(4.dp))
        Hint(
            if (hosting.active) "手机直连学校失败时，App 会改用服务器托管的课表；服务器的登录态失效时，本机下次刷新成功会自动再交一次。"
            else hosting.error ?: "重新前往教务系统导入课表时，勾选“同时托管到 nufe课表 服务器”即可开启：本机刷新失败时由服务器和同学们的手机帮你继续更新。",
        )

        HorizontalDivider(modifier = Modifier.padding(vertical = 12.dp), color = divider)

        // ---- 我为人人：协助刷新 ----
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("协助刷新", fontSize = 14.sp)
                Spacer(Modifier.height(2.dp))
                Text(
                    when {
                        !enabled -> "未开启"
                        available == false -> "已开启 · 服务器暂未开放，不产生流量"
                        helped > 0 -> "已开启 · 已帮同学刷新 $helped 次，谢谢你！"
                        else -> "已开启 · 等待服务器分配任务"
                    },
                    fontSize = 11.sp,
                    color = if (enabled) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant,
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
        Spacer(Modifier.height(4.dp))
        Hint(
            "开启后，手机在连接 Wi-Fi 且电量充足时，帮网页版同学把课表请求转发给教务系统。" +
                "转发的是端到端加密的数据：本机看不到任何人的账号、登录凭据或课表，也只会连接 jwxt.nufe.edu.cn。" +
                "每半小时最多约 2 分钟，每天流量不超过 30 MB。",
        )
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
                    leaving = true
                    scope.launch {
                        withContext(Dispatchers.IO) { Hosting.leave(context) }
                        leaving = false
                        hostingVersion++
                    }
                }) { Text("取消托管") }
            },
            dismissButton = { TextButton(onClick = { confirmLeave = false }) { Text("保留") } },
        )
    }
}

/** App 启动时调用：排好心跳与（若已开启）协助任务。 */
internal fun scheduleCommunity(context: android.content.Context) {
    wiki.nufe.kebiao.assist.HeartbeatWorker.schedule(context)
    AssistWorker.schedule(context)
}
