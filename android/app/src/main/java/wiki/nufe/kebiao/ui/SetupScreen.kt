package wiki.nufe.kebiao.ui

import android.os.Build
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

@Composable
internal fun PermissionSetupDialog(
    liveName: String,
    notificationsOk: Boolean,
    exactOk: Boolean,
    promotedOk: Boolean,
    batteryOk: Boolean,
    onAllowNotifications: () -> Unit,
    onOpenExactAlarm: () -> Unit,
    onOpenPromoted: () -> Unit,
    onOpenBattery: () -> Unit,
    onDismiss: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("先开启提醒权限") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(
                    "开启以下权限后，课前提醒、桌面小组件与${liveName}才能准时生效。点击对应项将直接跳转至系统授权页。",
                    fontSize = 13.sp,
                    lineHeight = 20.sp,
                )
                if (Build.VERSION.SDK_INT >= 33) {
                    PermissionDialogRow(
                        title = "通知",
                        detail = "发送课前提醒与实时上课状态",
                        allowed = notificationsOk,
                        action = "允许",
                        onClick = onAllowNotifications,
                    )
                }
                if (Build.VERSION.SDK_INT >= 31) {
                    PermissionDialogRow(
                        title = "准时提醒",
                        detail = "使用精确闹钟，避免提醒延后",
                        allowed = exactOk,
                        action = "去设置",
                        onClick = onOpenExactAlarm,
                    )
                }
                if (Build.VERSION.SDK_INT >= 36) {
                    PermissionDialogRow(
                        title = "${liveName}提醒",
                        detail = "在状态栏与通知栏展示课前倒计时卡片",
                        allowed = promotedOk,
                        action = "去设置",
                        onClick = onOpenPromoted,
                    )
                }
                PermissionDialogRow(
                    title = "后台运行",
                    detail = "避免应用在后台被系统冻结或推迟提醒",
                    allowed = batteryOk,
                    action = "去设置",
                    note = if (isColorOsLike()) "电池白名单不够，还要允许自启动/后台运行" else null,
                    onClick = onOpenBattery,
                )
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("知道了") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("稍后") } },
    )
}

@Composable
internal fun PermissionDialogRow(
    title: String,
    detail: String,
    allowed: Boolean,
    action: String,
    note: String? = null,
    onClick: () -> Unit,
) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(title, fontSize = 14.sp, fontWeight = FontWeight.Medium)
            Text(detail, fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            if (note != null) {
                Text(note, fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        if (allowed) {
            Text("已允许", fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        } else {
            TextButton(onClick = onClick, contentPadding = PaddingValues(horizontal = 6.dp)) {
                Text(action, fontSize = 12.sp)
            }
        }
    }
}

@Composable
internal fun SetupScreen(
    busy: Boolean,
    message: String?,
    onLogin: () -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .safeDrawingPadding()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 24.dp, vertical = 28.dp),
    ) {
        HeaderBlock()
        Spacer(Modifier.height(36.dp))
        SectionCard {
            Text("登录学校，导入课表", fontSize = 18.sp, fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.height(6.dp))
            Hint("在 App 内打开南京财经大学统一身份认证，由你完成登录和验证，随后直接从教务系统导入本学期课表。")
            Spacer(Modifier.height(14.dp))
            Text("凭据只留在本机", fontSize = 14.sp, fontWeight = FontWeight.Medium)
            Spacer(Modifier.height(4.dp))
            Hint("学号、密码和登录凭据不发送给项目服务器。App 不保存密码；登录会话与姓名、学号加密保存在本机。课表导入后可离线查看。")
            Spacer(Modifier.height(14.dp))
            Text("推荐账号密码 + 记住我", fontSize = 14.sp, fontWeight = FontWeight.Medium)
            Spacer(Modifier.height(4.dp))
            Hint("可在学校登录页勾选“记住我”，获得较持久的登录会话（由学校决定，通常约半年）。这不等于让 App 保存密码。")
            Spacer(Modifier.height(10.dp))
            Hint("扫码登录通常只能维持数小时，适合临时导入；会话失效后需在 App 内重新登录，已保存的课表不会消失。")
            Spacer(Modifier.height(18.dp))
            Button(
                onClick = onLogin,
                enabled = !busy,
                modifier = Modifier.fillMaxWidth().height(52.dp),
                shape = RoundedCornerShape(16.dp),
            ) { Text(if (busy) "正在登录…" else "登录学校账号", fontSize = 14.sp) }
            if (message != null) {
                Spacer(Modifier.height(10.dp))
                Text(message, color = MaterialTheme.colorScheme.error, fontSize = 12.sp, lineHeight = 18.sp)
            }
        }
        Spacer(Modifier.height(20.dp))
        Hint("课前提醒、桌面小组件与实时通知均读取本机课表。联网刷新由手机直接访问学校，学校暂时不可达时继续使用缓存。")
        Spacer(Modifier.height(28.dp))
        Text(
            "非官方工具，与南京财经大学无隶属关系。",
            fontSize = 11.sp,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}
