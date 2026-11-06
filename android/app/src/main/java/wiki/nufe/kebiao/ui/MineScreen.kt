package wiki.nufe.kebiao.ui

import android.content.ClipboardManager
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import android.widget.Toast
import androidx.compose.foundation.clickable
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import wiki.nufe.kebiao.R
import wiki.nufe.kebiao.data.Profile
import wiki.nufe.kebiao.data.Repository
import wiki.nufe.kebiao.data.needsLogin
import wiki.nufe.kebiao.data.Snapshot
import wiki.nufe.kebiao.diag.Diag
import wiki.nufe.kebiao.diag.Diagnostics
import wiki.nufe.kebiao.remind.LiveUpdates
import wiki.nufe.kebiao.remind.Reminders

@Composable
internal fun MineScreen(
    repo: Repository,
    snapshot: Snapshot?,
    profile: Profile?,
    bound: Boolean,
    notificationsOk: Boolean,
    exactOk: Boolean,
    promotedOk: Boolean,
    batteryOk: Boolean,
    onOpenSite: () -> Unit,
    onPair: (String) -> Unit,
    onRemove: () -> Unit,
) {
    val context = LocalContext.current
    val liveName = liveUpdateName()
    var confirmRemove by remember { mutableStateOf(false) }
    var showFeedback by remember { mutableStateOf(false) }
    var liveUpdate by remember { mutableStateOf(repo.liveUpdateEnabled) }
    var alarmEnabled by remember { mutableStateOf(repo.alarmEnabled) }
    var minutes by remember { mutableIntStateOf(repo.remindMinutes) }
    var showWheel by remember { mutableStateOf(false) }
    val student = profile?.student
    val mail = profile?.mail
    val scope = rememberCoroutineScope()
    var exporting by remember { mutableStateOf(false) }

    fun openExternal(intent: Intent) {
        runCatching { context.startActivity(intent) }
            .onFailure { Toast.makeText(context, "没有可用的应用", Toast.LENGTH_SHORT).show() }
    }

    fun exportDiagnostics() {
        if (exporting) return
        exporting = true
        scope.launch {
            val file = withContext(Dispatchers.IO) {
                runCatching { Diagnostics.export(context, repo, snapshot) }.getOrNull()
            }
            exporting = false
            if (file == null) {
                Toast.makeText(context, "导出失败，请稍后重试", Toast.LENGTH_SHORT).show()
                return@launch
            }
            val shared = runCatching { Diagnostics.share(context, file) }.isSuccess
            if (!shared) Toast.makeText(context, "没有可用的分享应用", Toast.LENGTH_SHORT).show()
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .safeDrawingPadding()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 24.dp, vertical = 24.dp),
    ) {
        Text(
            "我的",
            fontSize = 34.sp,
            fontWeight = FontWeight.Bold,
            letterSpacing = (-0.5).sp,
            color = MaterialTheme.colorScheme.onBackground,
        )
        Spacer(Modifier.height(4.dp))
        Text("管理个人资料、账号连接与系统权限。", fontSize = 15.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.height(20.dp))

        SectionCard {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Surface(
                    color = if (isSystemInDarkTheme()) Color(0xFF2B2B30) else MaterialTheme.colorScheme.onBackground,
                    contentColor = if (isSystemInDarkTheme()) Color(0xFFDADAE0) else MaterialTheme.colorScheme.background,
                    shape = CircleShape,
                    modifier = Modifier.size(48.dp),
                ) {
                    Box(contentAlignment = Alignment.Center) {
                        Text(
                            student?.name?.take(1) ?: "南",
                            fontSize = 18.sp,
                            fontWeight = FontWeight.SemiBold,
                        )
                    }
                }
                Spacer(Modifier.width(14.dp))
                Column(Modifier.weight(1f)) {
                    Text(
                        student?.name ?: "个人信息",
                        fontSize = 18.sp,
                        fontWeight = FontWeight.SemiBold,
                    )
                    Spacer(Modifier.height(3.dp))
                    Text(
                        student?.code?.let { "学号 $it" } ?: if (bound) "正在同步…" else "这台设备尚未绑定",
                        fontSize = 12.sp,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            Spacer(Modifier.height(14.dp))
            HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.7f))
            Spacer(Modifier.height(4.dp))
            if (!bound) {
                Spacer(Modifier.height(6.dp))
                Hint("姓名、学号等个人信息不包含在基础课表链接中，仅向已绑定设备提供。请前往网站“账号与设置 → 安卓 App”点击“绑定新设备”，再返回此处完成配对。")
                Spacer(Modifier.height(6.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    TextButton(onClick = onOpenSite, contentPadding = PaddingValues(horizontal = 4.dp)) {
                        Text("打开网站", fontSize = 12.sp)
                    }
                    TextButton(
                        onClick = {
                            val text = context.getSystemService(ClipboardManager::class.java)
                                ?.primaryClip?.takeIf { it.itemCount > 0 }?.getItemAt(0)
                                ?.coerceToText(context)?.toString()
                            if (Repository.extractPair(text) != null && text != null) {
                                onPair(text)
                            } else {
                                Toast.makeText(context, "剪贴板里没有配对链接", Toast.LENGTH_SHORT).show()
                            }
                        },
                        contentPadding = PaddingValues(horizontal = 4.dp),
                    ) { Text("粘贴配对链接", fontSize = 12.sp) }
                }
            }
            if (profile != null) Row(
                modifier = Modifier.fillMaxWidth().padding(vertical = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    "提醒邮箱",
                    fontSize = 13.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.width(72.dp),
                )
                Text(
                    if (mail?.bound == true) mail.email ?: "已绑定" else "未绑定",
                    fontSize = 13.sp,
                    modifier = Modifier.weight(1f),
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                if (mail?.bound != true) {
                    TextButton(onClick = onOpenSite, contentPadding = PaddingValues(horizontal = 4.dp)) {
                        Text("去绑定", fontSize = 12.sp)
                    }
                }
            }
            if (profile != null) Hint(
                if (mail?.bound == true) "此处仅展示脱敏邮箱，完整地址请前往网站设置页查看。"
                else "绑定后，自动刷新暂停或连续失败时会发邮件提醒。",
            )
        }

        Spacer(Modifier.height(14.dp))
        SectionCard {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("账号连接", fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
                Spacer(Modifier.weight(1f))
                val needsLogin = snapshot?.payload?.needsLogin == true
                Tag(
                    if (needsLogin) "需重新登录" else if (snapshot?.offline == true) "离线" else "已连接",
                    if (needsLogin || snapshot?.offline == true) WarmOrange else MaterialTheme.colorScheme.primary,
                )
            }
            Spacer(Modifier.height(6.dp))
            ConnectionRow("打开网站", onOpenSite)
            HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.7f))
            ConnectionRow(
                "应用设置",
                {
                    context.startActivity(
                        Intent(
                            Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                            Uri.parse("package:${context.packageName}"),
                        ),
                    )
                },
            )
            Spacer(Modifier.height(8.dp))
            Button(
                onClick = { confirmRemove = true },
                modifier = Modifier.fillMaxWidth().height(48.dp),
                shape = RoundedCornerShape(16.dp),
                colors = ButtonDefaults.buttonColors(
                    containerColor = MaterialTheme.colorScheme.error,
                    contentColor = MaterialTheme.colorScheme.onError,
                ),
            ) { Text("断开连接", fontSize = 14.sp) }
        }

        Spacer(Modifier.height(14.dp))
        WidgetSection()
        Spacer(Modifier.height(14.dp))
        ReminderSection(
            repo = repo,
            minutes = minutes,
            alarmEnabled = alarmEnabled,
            onPickTime = { showWheel = true },
            onAlarmToggle = { enabled ->
                Diag.i("alarm enabled=$enabled")
                alarmEnabled = enabled
                repo.alarmEnabled = enabled
                if (enabled && minutes == 0) {
                    minutes = 10
                    repo.remindMinutes = 10
                }
                if (!enabled) LiveUpdates.cancel(context)
                Reminders.rescheduleAsync(context)
            },
            liveUpdate = liveUpdate,
            onLiveUpdateChange = { checked ->
                Diag.i("live update enabled=$checked")
                liveUpdate = checked
                repo.liveUpdateEnabled = checked
                if (!checked) LiveUpdates.cancel(context)
                Reminders.rescheduleAsync(context)
            },
        )

        Spacer(Modifier.height(14.dp))
        SectionCard {
            Text("权限", fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.height(4.dp))
            PermissionRow(
                title = "通知",
                detail = "课前提醒和系统通知",
                allowed = notificationsOk,
            ) {
                context.startActivity(
                    Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                        .putExtra(Settings.EXTRA_APP_PACKAGE, context.packageName),
                )
            }
            // Android 12 以下不需要也没有“闹钟和提醒”授权页，启动该 intent 会抛 ActivityNotFoundException。
            if (Build.VERSION.SDK_INT >= 31) {
                HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.7f))
                PermissionRow(
                    title = "准时提醒",
                    detail = "避免系统推迟上课提醒",
                    allowed = exactOk,
                ) {
                    context.startActivity(
                        Intent(
                            Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM,
                            Uri.parse("package:${context.packageName}"),
                        ),
                    )
                }
            }
            if (Build.VERSION.SDK_INT >= 36) {
                HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.7f))
                PermissionRow(
                    title = "系统实时提醒（$liveName）",
                    detail = "在锁屏或状态栏显示课程倒计时",
                    allowed = promotedOk,
                ) {
                    context.startActivity(
                        Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                            .putExtra(Settings.EXTRA_APP_PACKAGE, context.packageName),
                    )
                }
            }
            HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.7f))
            PermissionRow(
                title = "后台运行",
                detail = "保持提醒和桌面组件在后台更新",
                allowed = batteryOk,
            ) {
                openBatteryOptimizationSettings(context)
            }
        }

        Spacer(Modifier.height(14.dp))
        SectionCard {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .clickable { showFeedback = true },
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(Modifier.weight(1f)) {
                    Text("提交反馈", fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
                    Spacer(Modifier.height(2.dp))
                    Hint("GitHub Issue、联系开发者，或先导出诊断日志")
                }
                Icon(
                    painter = painterResource(R.drawable.ic_chevron_right),
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.size(18.dp),
                )
            }
        }

        Spacer(Modifier.height(14.dp))
        SectionCard {
            Text("数据", fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.height(4.dp))
            Hint("课表视图、桌面小组件与课前提醒均通过当前链接同步数据。若链接不慎外泄，请前往网站设置页重新生成，或在此处断开连接。")
        }
        Spacer(Modifier.height(110.dp))
    }

    if (confirmRemove) {
        AlertDialog(
            onDismissRequest = { confirmRemove = false },
            title = { Text("断开连接？") },
            text = {
                Text(
                    "断开后本机课表、课前提醒与桌面小组件将停止更新，需重新粘贴或配对链接才可恢复。" +
                        "网站账号及已登记的设备记录不受影响；如需撤销设备授权，请前往网站“账号与设置 → 安卓 App”操作。",
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    confirmRemove = false
                    onRemove()
                }) { Text("断开连接", color = MaterialTheme.colorScheme.error) }
            },
            dismissButton = { TextButton(onClick = { confirmRemove = false }) { Text("取消") } },
        )
    }

    if (showFeedback) {
        FeedbackDialog(
            exporting = exporting,
            onGitHub = {
                showFeedback = false
                openExternal(Intent(Intent.ACTION_VIEW, Uri.parse("$REPO_URL/issues/new")))
            },
            onContact = {
                showFeedback = false
                openExternal(
                    Intent(Intent.ACTION_SENDTO, Uri.parse("mailto:$FEEDBACK_EMAIL")).apply {
                        putExtra(Intent.EXTRA_SUBJECT, "nufe课表 App 反馈")
                    },
                )
            },
            onExport = {
                showFeedback = false
                exportDiagnostics()
            },
            onDismiss = { showFeedback = false },
        )
    }

    if (showWheel) {
        TimeWheelDialog(
            initial = minutes,
            onDismiss = { showWheel = false },
            onConfirm = { selected ->
                showWheel = false
                minutes = selected
                repo.remindMinutes = selected
                Reminders.rescheduleAsync(context)
            },
        )
    }
}

@Composable
private fun FeedbackDialog(
    exporting: Boolean,
    onGitHub: () -> Unit,
    onContact: () -> Unit,
    onExport: () -> Unit,
    onDismiss: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("提交反馈") },
        text = {
            Column {
                Text(
                    "遇到问题或有建议，可以先导出诊断日志，再选择反馈方式。",
                    fontSize = 13.sp,
                    lineHeight = 20.sp,
                )
                Spacer(Modifier.height(4.dp))
                Hint("诊断日志会自动对课表链接、密码与邮箱地址进行脱敏处理。")
                Spacer(Modifier.height(10.dp))
                FeedbackOption("1. GitHub Issue", "功能建议、可以公开讨论的问题", onClick = onGitHub)
                HorizontalDivider(
                    modifier = Modifier.padding(vertical = 2.dp),
                    color = MaterialTheme.colorScheme.outline.copy(alpha = 0.7f),
                )
                FeedbackOption("2. 直接联系开发者", FEEDBACK_EMAIL, onClick = onContact)
                HorizontalDivider(
                    modifier = Modifier.padding(vertical = 2.dp),
                    color = MaterialTheme.colorScheme.outline.copy(alpha = 0.7f),
                )
                FeedbackOption(
                    "3. 导出诊断日志",
                    if (exporting) "正在导出…" else "可选，附在反馈里更有帮助",
                    enabled = !exporting,
                    onClick = onExport,
                )
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("取消") } },
    )
}

@Composable
private fun FeedbackOption(
    title: String,
    description: String,
    enabled: Boolean = true,
    onClick: () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(enabled = enabled, onClick = onClick)
            .padding(vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(title, fontSize = 14.sp, fontWeight = FontWeight.Medium)
            Spacer(Modifier.height(2.dp))
            Text(description, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Icon(
            painter = painterResource(R.drawable.ic_chevron_right),
            contentDescription = null,
            tint = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.size(16.dp),
        )
    }
}

@Composable
private fun ConnectionRow(title: String, onClick: () -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable { launchSafely(title, onClick) }
            .padding(vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(title, fontSize = 14.sp, modifier = Modifier.weight(1f))
        Icon(
            painter = painterResource(R.drawable.ic_chevron_right),
            contentDescription = null,
            tint = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.size(18.dp),
        )
    }
}

@Composable
private fun PermissionRow(
    title: String,
    detail: String,
    allowed: Boolean,
    onClick: () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable { launchSafely(title, onClick) }
            .padding(vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(title, fontSize = 14.sp)
            Spacer(Modifier.height(2.dp))
            Text(detail, fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Text(
            if (allowed) "已允许" else "未允许",
            fontSize = 12.sp,
            color = if (allowed) SuccessGreen else WarmOrange,
        )
        Spacer(Modifier.width(4.dp))
        Icon(
            painter = painterResource(R.drawable.ic_chevron_right),
            contentDescription = null,
            tint = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.size(16.dp),
        )
    }
}

/** 系统设置页在部分 ROM 上可能不存在；跳转失败时只记日志，不让整行点击把 App 带崩。 */
private fun launchSafely(title: String, open: () -> Unit) {
    runCatching(open).onFailure { Diag.w("open settings failed ($title): ${it.javaClass.simpleName}") }
}
