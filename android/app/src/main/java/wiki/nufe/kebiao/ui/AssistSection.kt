package wiki.nufe.kebiao.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import wiki.nufe.kebiao.assist.AssistWorker
import wiki.nufe.kebiao.assist.Community

/** “我的”里的协助刷新开关。默认关闭；说明写清楚本机经手的是什么、不经手什么。 */
@Composable
internal fun AssistSection() {
    val context = LocalContext.current
    val view = LocalView.current
    var enabled by remember { mutableStateOf(Community.assistEnabled(context)) }
    val available = remember(enabled) { Community.assistAvailable(context) }
    val helped = remember(enabled) { Community.helpedCount(context) }

    SectionCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("协助刷新", fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
                Spacer(Modifier.height(2.dp))
                Text("人人为我，我为人人", fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Switch(
                checked = enabled,
                onCheckedChange = {
                    view.performHapticFeedback(android.view.HapticFeedbackConstants.VIRTUAL_KEY)
                    enabled = it
                    Community.setAssistEnabled(context, it)
                },
            )
        }
        Spacer(Modifier.height(6.dp))
        Hint(
            "开启后，手机在连接 Wi-Fi 且电量充足时，会帮网页版同学把课表请求转发给教务系统，分担项目服务器的压力。" +
                "转发的是端到端加密的数据：本机看不到任何人的账号、登录凭据或课表，也只会连接 jwxt.nufe.edu.cn。" +
                "每半小时最多约 2 分钟，每天流量不超过 30 MB。",
        )
        if (enabled) {
            HorizontalDivider(
                modifier = Modifier.padding(vertical = 10.dp),
                color = MaterialTheme.colorScheme.outline.copy(alpha = 0.7f),
            )
            Hint(
                when {
                    available == false -> "服务器暂未开放协助刷新，开关保持打开也不会产生转发流量。"
                    helped > 0 -> "已帮同学完成 $helped 次课表刷新，谢谢你！"
                    else -> "已加入，等待服务器分配任务。"
                },
            )
        }
    }
}

/** App 启动时调用：排好心跳与（若已开启）协助任务。 */
internal fun scheduleCommunity(context: android.content.Context) {
    wiki.nufe.kebiao.assist.HeartbeatWorker.schedule(context)
    AssistWorker.schedule(context)
}
