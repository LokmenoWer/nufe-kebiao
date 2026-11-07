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
import androidx.compose.material3.Checkbox
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
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
    onOpenAutoStart: () -> Unit,
    onOpenBattery: () -> Unit,
    onDismiss: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("先开启提醒权限") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(
                    "开启以下权限后，课前提醒、桌面小组件与${liveName}才能准时生效。点击对应项将直接跳转至系统授权页。" +
                        "部分系统（如 ColorOS）还需允许“自启动”，否则 App 被清理后提醒无法唤醒。",
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
                if (isColorOsLike()) {
                    PermissionDialogRow(
                        title = "自启动",
                        detail = "打开系统设置 → 应用 → 自启动，勾选本 app",
                        allowed = false,
                        action = "去设置",
                        onClick = onOpenAutoStart,
                    )
                }
                PermissionDialogRow(
                    title = "后台运行",
                    detail = "在耗电管理或后台行为中，设置为完全允许后台行为",
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
    var agreed by remember { mutableStateOf(false) }
    var showDisclaimer by remember { mutableStateOf(false) }
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
            fontSize = 44.sp,
            fontWeight = FontWeight.Bold,
            letterSpacing = (-1).sp,
            color = MaterialTheme.colorScheme.onBackground,
        )
        Spacer(Modifier.height(10.dp))
        Text("你的课程，一眼掌握", fontSize = 15.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.height(40.dp))
        Button(
            onClick = onLogin,
            enabled = agreed && !busy,
            modifier = Modifier.height(52.dp),
            shape = RoundedCornerShape(16.dp),
        ) { Text(if (busy) "正在登录…" else "前往学校官网导入课表", fontSize = 14.sp) }
        Spacer(Modifier.height(18.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Checkbox(checked = agreed, onCheckedChange = { agreed = it })
            Text("我已阅读并同意", fontSize = 13.sp)
            TextButton(onClick = { showDisclaimer = true }) { Text("《免责声明》", fontSize = 13.sp) }
        }
        if (message != null) {
            Spacer(Modifier.height(12.dp))
            Text(message, color = MaterialTheme.colorScheme.error, fontSize = 12.sp, lineHeight = 18.sp)
        }
    }

    if (showDisclaimer) {
        AlertDialog(
            onDismissRequest = { showDisclaimer = false },
            title = { Text("免责声明") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("本应用（nufe课表）是个人开发的非官方工具，与南京财经大学不存在任何隶属、授权或合作关系。", fontSize = 13.sp, lineHeight = 20.sp)
                    Text("默认情况下，课表、姓名、学号只从学校教务系统读取并仅保存在本机；登录凭据加密保存在本机，App 不保存你的密码。", fontSize = 13.sp, lineHeight = 20.sp)
                    Text("若你在登录时勾选“同时托管到 nufe课表 服务器”（或在「我的」开启“服务器托管”）：学校登录会话（Cookie）与课表会加密上传并保存在 nufe课表 服务器，服务器据此代为刷新课表、或在手机刷新失败时兜底——也就是说服务器会拿到你的学号、姓名等课表信息以及加密的学校登录状态。取消托管后服务器会删除你的登录状态。", fontSize = 13.sp, lineHeight = 20.sp)
                    Text("若只开启“iOS 小组件与日历”：开启时会用本机学校会话向服务器验明身份一次，但服务器不保存该会话；之后手机刷到的课表（含学号、姓名）会推送并保存在服务器，供 iOS 小组件与日历订阅读取。两项都关闭后服务器会撤销该设备。", fontSize = 13.sp, lineHeight = 20.sp)
                    Text("本应用按“现状”提供，不保证数据准确、完整或及时，课程安排以学校教务系统为准；因使用本应用造成的任何影响，开发者不承担责任。", fontSize = 13.sp, lineHeight = 20.sp)
                    Text("请勿用于违反校规、法律或侵犯他人权益的用途。", fontSize = 13.sp, lineHeight = 20.sp)
                }
            },
            confirmButton = { TextButton(onClick = { agreed = true; showDisclaimer = false }) { Text("同意并继续") } },
            dismissButton = { TextButton(onClick = { showDisclaimer = false }) { Text("关闭") } },
        )
    }
}
