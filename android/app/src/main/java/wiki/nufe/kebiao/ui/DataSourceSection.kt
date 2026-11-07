package wiki.nufe.kebiao.ui

import android.content.Intent
import android.widget.Toast
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
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
import wiki.nufe.kebiao.data.CasSessionManager
import wiki.nufe.kebiao.data.Hosting
import wiki.nufe.kebiao.data.Repository

/**
 * 设备数据源：把这台安卓手机变成 iPhone 上 Scriptable 小组件与日历订阅的数据来源。
 * 手机刷到新课表就推给本站，本站转发给 /widget/… 与 /ics/…；同时开着服务器托管时两边谁新用谁。
 */
@Composable
internal fun DataSourceSection(repo: Repository, onLogin: () -> Unit) {
    val context = LocalContext.current
    val view = LocalView.current
    val clipboard = LocalClipboardManager.current
    val scope = rememberCoroutineScope()
    var version by remember { mutableIntStateOf(0) }
    val state = remember(version) { Hosting.state(context) }
    var busy by remember { mutableStateOf(false) }
    var links by remember { mutableStateOf<JSONObject?>(null) }
    var message by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(state.dataSource, version) {
        links = if (state.dataSource) withContext(Dispatchers.IO) { runCatching { Hosting.links(context) }.getOrNull() } else null
    }

    fun toggle(enabled: Boolean) {
        if (busy) return
        busy = true
        message = null
        scope.launch {
            val result = withContext(Dispatchers.IO) {
                runCatching {
                    if (enabled) {
                        if (Hosting.active(context)) {
                            Hosting.setDataSource(context, true)
                        } else {
                            // 只验明身份、不留登录态：本站凭这次读到的学号认人，之后课表只靠手机推
                            Hosting.enroll(context, CasSessionManager(context).load(), keepSession = false, dataSource = true)
                        }
                    } else {
                        Hosting.setDataSource(context, false)
                        if (!Hosting.state(context).hosted) Hosting.leave(context)
                    }
                }
            }
            if (enabled && result.isSuccess) repo.pushToServer()
            result.exceptionOrNull()?.let {
                message = "开启失败：${it.message ?: "网络错误"}。若本机学校登录已过期，请先重新导入课表。"
            }
            busy = false
            version++
        }
    }

    SectionCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("iOS 小组件与日历数据源", fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
                Spacer(Modifier.height(2.dp))
                Text(
                    when {
                        busy -> "处理中…"
                        !state.dataSource -> "未开启"
                        state.pushedAtMs > 0 -> "已开启 · 最近推送 ${clock(state.pushedAtMs)}"
                        else -> "已开启 · 等待第一次推送"
                    },
                    fontSize = 11.sp,
                    color = if (state.dataSource) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Spacer(Modifier.width(8.dp))
            Switch(
                checked = state.dataSource,
                enabled = !busy,
                onCheckedChange = {
                    view.performHapticFeedback(android.view.HapticFeedbackConstants.VIRTUAL_KEY)
                    toggle(it)
                },
            )
        }
        Spacer(Modifier.height(4.dp))
        Hint(
            "开启后，这台手机每次刷到新课表都会经 nufe课表 服务器转发给你 iPhone 上的 Scriptable 小组件和日历订阅。" +
                if (state.hosted) "你也开着服务器托管，服务器同样会刷新，两边谁新用谁。"
                else "服务器不保存你的学校登录状态，只转发手机推来的课表；手机关机或很久没联网时，小组件和日历会停在最后一次的课表。",
        )
        message?.let {
            Spacer(Modifier.height(6.dp))
            Text(it, fontSize = 12.sp, color = MaterialTheme.colorScheme.error)
            Spacer(Modifier.height(2.dp))
            androidx.compose.material3.TextButton(onClick = onLogin) { Text("重新导入课表") }
        }
        val current = links
        if (state.dataSource && current != null) {
            HorizontalDivider(modifier = Modifier.padding(vertical = 10.dp), color = MaterialTheme.colorScheme.outline.copy(alpha = 0.7f))
            Hint("iPhone：在 Safari 打开小组件脚本链接，把全文复制到 Scriptable 新建脚本并添加小组件；日历：用订阅链接添加到 iOS“日历”的订阅日历。")
            Spacer(Modifier.height(8.dp))
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                fun copy(label: String, key: String) {
                    val url = current.optString(key).takeIf { it.isNotEmpty() } ?: return
                    clipboard.setText(AnnotatedString(url))
                    Toast.makeText(context, "已复制$label", Toast.LENGTH_SHORT).show()
                }
                OutlinedButton(onClick = { copy("小组件脚本链接", "script_url") }) { Text("复制小组件脚本链接", fontSize = 12.sp) }
                OutlinedButton(onClick = { copy("日历订阅链接", "webcal_url") }) { Text("复制日历订阅链接", fontSize = 12.sp) }
                OutlinedButton(onClick = {
                    val text = "nufe课表 iOS 小组件脚本：${current.optString("script_url")}\n日历订阅：${current.optString("webcal_url")}"
                    runCatching {
                        context.startActivity(Intent.createChooser(
                            Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, text), "发送到 iPhone"))
                    }
                }) { Text("分享到 iPhone", fontSize = 12.sp) }
            }
            Spacer(Modifier.height(6.dp))
            Hint("链接相当于只读凭证，只发给自己的设备。")
        }
    }
}

private fun clock(ms: Long): String = SimpleDateFormat("M/d HH:mm", Locale.CHINA).format(Date(ms))
