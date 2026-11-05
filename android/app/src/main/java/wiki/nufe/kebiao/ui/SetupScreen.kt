package wiki.nufe.kebiao.ui

import android.content.ClipboardManager
import android.net.Uri
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
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import wiki.nufe.kebiao.data.Repository

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
    onClick: () -> Unit,
) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(title, fontSize = 14.sp, fontWeight = FontWeight.Medium)
            Text(detail, fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
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
    invalid: Boolean,
    onConfirm: (String) -> Unit,
    onOpenSite: () -> Unit,
) {
    val context = LocalContext.current
    var text by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }

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
            Text("连接课表", fontSize = 18.sp, fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.height(6.dp))
            Hint("在网站“账号与设置 → 安卓 App”中点击“在安卓 App 中打开”，或复制配对链接粘贴至下方。")
            Spacer(Modifier.height(16.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(
                    value = text,
                    onValueChange = {
                        text = it
                        error = null
                    },
                    modifier = Modifier.weight(1f),
                    placeholder = { Text("https://…/mobile/….json", fontSize = 13.sp) },
                    singleLine = true,
                    shape = RoundedCornerShape(16.dp),
                    keyboardOptions = KeyboardOptions(
                        keyboardType = KeyboardType.Uri,
                        imeAction = ImeAction.Done,
                    ),
                    keyboardActions = KeyboardActions(
                        onDone = {
                            if (!busy && text.isNotBlank()) {
                                error = null
                                onConfirm(text)
                            }
                        },
                    ),
                )
                Spacer(Modifier.width(10.dp))
                Button(
                    onClick = {
                        error = null
                        onConfirm(text)
                    },
                    enabled = !busy && text.isNotBlank(),
                    modifier = Modifier.height(56.dp),
                    shape = RoundedCornerShape(16.dp),
                    contentPadding = PaddingValues(horizontal = 18.dp),
                ) { Text(if (busy) "校验中" else "确认", fontSize = 14.sp) }
            }
            if (error != null || message != null) {
                Spacer(Modifier.height(10.dp))
                Text(
                    error ?: message.orEmpty(),
                    color = MaterialTheme.colorScheme.error,
                    fontSize = 12.sp,
                    lineHeight = 18.sp,
                )
            } else if (invalid) {
                Spacer(Modifier.height(10.dp))
                Text("链接已失效，请重新生成。", color = MaterialTheme.colorScheme.error, fontSize = 12.sp)
            }
            Spacer(Modifier.height(12.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                TextButton(onClick = onOpenSite, contentPadding = PaddingValues(0.dp)) {
                    Text("打开网站生成链接", fontSize = 13.sp)
                }
                Spacer(Modifier.width(12.dp))
                TextButton(
                    onClick = {
                        val clip = context.getSystemService(ClipboardManager::class.java)
                            ?.primaryClip?.takeIf { it.itemCount > 0 }?.getItemAt(0)
                            ?.coerceToText(context)?.toString()?.trim()
                        val extracted = Repository.extractLink(clip)
                        if (clip != null && extracted != null) {
                            text = extracted
                            error = null
                            if (!busy) onConfirm(clip)
                        } else if (!clip.isNullOrBlank()) {
                            text = clip
                            error = "剪贴板里的内容不是有效的课表链接。"
                        } else {
                            error = "剪贴板为空，请先去网站复制链接。"
                        }
                    },
                    enabled = !busy,
                    contentPadding = PaddingValues(0.dp),
                ) {
                    Text("粘贴剪贴板", fontSize = 13.sp)
                }
                Spacer(Modifier.weight(1f))
                Text("链接是只读凭证", fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        Spacer(Modifier.height(20.dp))
        Hint("课前提醒、桌面小组件与实时通知均通过此链接同步课表。链接本身为访问凭证，请勿转发给他人。")
        Spacer(Modifier.height(28.dp))
        Text(
            "非官方工具，与南京财经大学无隶属关系。",
            fontSize = 11.sp,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}
