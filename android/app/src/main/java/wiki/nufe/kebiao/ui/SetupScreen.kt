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
import androidx.compose.foundation.shape.RoundedCornerShape
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
            .padding(horizontal = 24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Text(
            "nufe课表",
            fontSize = 34.sp,
            fontWeight = FontWeight.Bold,
            letterSpacing = (-0.5).sp,
            color = MaterialTheme.colorScheme.onBackground,
        )
        Spacer(Modifier.height(8.dp))
        Text("你的课程，一眼掌握", fontSize = 15.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.height(36.dp))
        Button(
            onClick = onLogin,
            enabled = !busy,
            modifier = Modifier.fillMaxWidth().height(52.dp),
            shape = RoundedCornerShape(16.dp),
        ) { Text(if (busy) "正在登录…" else "前往学校官网导入课表", fontSize = 14.sp) }
        if (message != null) {
            Spacer(Modifier.height(12.dp))
            Text(message, color = MaterialTheme.colorScheme.error, fontSize = 12.sp, lineHeight = 18.sp)
        }
    }
}
