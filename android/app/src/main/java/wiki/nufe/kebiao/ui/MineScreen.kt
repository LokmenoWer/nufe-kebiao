package wiki.nufe.kebiao.ui

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import android.widget.Toast
import androidx.compose.foundation.clickable
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
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
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import wiki.nufe.kebiao.R
import wiki.nufe.kebiao.data.Profile
import wiki.nufe.kebiao.data.Repository
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
    needsLogin: Boolean,
    busy: Boolean,
    notificationsOk: Boolean,
    exactOk: Boolean,
    promotedOk: Boolean,
    batteryOk: Boolean,
    onLogin: () -> Unit,
    onRemove: () -> Unit,
    rhythmClock12h: Boolean = true,
    onRhythmClockChange: (Boolean) -> Unit = {},
) {
    val context = LocalContext.current
    var hostingVersion by remember { mutableIntStateOf(0) }
    val hostingState = remember(hostingVersion) { wiki.nufe.kebiao.data.Hosting.state(context) }
    val liveName = liveUpdateName()
    var confirmRemove by remember { mutableStateOf(false) }
    var showFeedback by remember { mutableStateOf(false) }
    var liveUpdate by remember { mutableStateOf(repo.liveUpdateEnabled) }
    var alarmEnabled by remember { mutableStateOf(repo.alarmEnabled) }
    var minutes by remember { mutableIntStateOf(repo.remindMinutes) }
    var showWheel by remember { mutableStateOf(false) }
    val student = profile?.student
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
        Text("管理本机资料、学校登录与系统权限。", fontSize = 15.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
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
                        student?.code?.let { "学号 $it" } ?: "本机暂无学号信息",
                        fontSize = 12.sp,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            Spacer(Modifier.height(14.dp))
            HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.7f))
            Spacer(Modifier.height(4.dp))
            Hint(
                when {
                    hostingState.hosted ->
                        "当前已连接 nufe课表 服务器：学校登录状态与课表会加密同步到服务器，可在「互助与同步」里取消。"
                    hostingState.dataSource ->
                        "当前已连接 nufe课表 服务器：课表会推送到服务器供 iOS 小组件与日历使用，服务器不保存学校登录状态，可在「互助与同步」里取消。"
                    else -> "所有个人信息仅保存在本地"
                },
            )
        }

        Spacer(Modifier.height(14.dp))
        SectionCard {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("本地学校登录", fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
                Spacer(Modifier.weight(1f))
                Tag(
                    if (needsLogin) "需登录" else if (snapshot?.offline == true) "离线可用" else "已保存会话",
                    if (needsLogin || snapshot?.offline == true) WarmOrange else MaterialTheme.colorScheme.primary,
                )
            }
            Spacer(Modifier.height(6.dp))
            Hint(if (needsLogin) "请在本机重新登录学校账号以恢复刷新；离线课表与提醒仍可使用。"
                else "登录会话只保存在本机，后台刷新由手机直接访问学校。")
            ConnectionRow("重新前往教务系统导入课表", onLogin)
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
                enabled = !busy,
                modifier = Modifier.fillMaxWidth().height(48.dp),
                shape = RoundedCornerShape(16.dp),
                colors = ButtonDefaults.buttonColors(
                    containerColor = MaterialTheme.colorScheme.error,
                    contentColor = MaterialTheme.colorScheme.onError,
                ),
            ) { Text("清除本机数据", fontSize = 14.sp) }
        }

        Spacer(Modifier.height(14.dp))
        SectionCard {
            Text("首页显示", fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.height(8.dp))
            Row(modifier = Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("今日节奏表盘", fontSize = 14.sp)
                    Spacer(Modifier.height(2.dp))
                    Text(
                        if (rhythmClock12h) "和钟表一样，显示接下来半天的课" else "一圈是一整天，0 点在正上方",
                        fontSize = 11.sp,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                Spacer(Modifier.width(8.dp))
                SlidingToggle(
                    firstLabel = "12 小时",
                    secondLabel = "24 小时",
                    selectedIndex = if (rhythmClock12h) 0 else 1,
                    onSelect = { index -> onRhythmClockChange(index == 0) },
                )
            }
        }
        Spacer(Modifier.height(14.dp))
        AssistSection(repo = repo, onLogin = onLogin, onHostingChanged = { hostingVersion++ })
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
                    minutes = Repository.DEFAULT_REMIND_MINUTES
                    repo.remindMinutes = Repository.DEFAULT_REMIND_MINUTES
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
        val rom = remember { detectRomFamily() }
        SectionCard {
            Text("权限", fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.height(4.dp))
            PermissionRow(
                title = "通知",
                detail = "课前提醒和系统通知",
                allowed = notificationsOk,
            ) {
                openNotificationSettings(context)
            }
            // Android 12 以下不需要也没有“闹钟和提醒”授权页，启动该 intent 会抛 ActivityNotFoundException。
            if (Build.VERSION.SDK_INT >= 31) {
                HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.7f))
                PermissionRow(
                    title = "准时提醒",
                    detail = "避免系统推迟上课提醒",
                    allowed = exactOk,
                ) {
                    openExactAlarmSettings(context)
                }
            }
            if (isLiveUpdateSupported()) {
                val tier = detectLiveUpdateTier()
                HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.7f))
                PermissionRow(
                    title = "系统实时提醒（$liveName）",
                    detail = "在锁屏或状态栏显示课程倒计时",
                    allowed = promotedOk,
                    statusOverride = if (tier != LiveUpdateTier.NATIVE_PROMOTED) "已就绪" else null,
                ) {
                    openPromotedNotificationSettings(context)
                }
            }
            if (rom.needsAutoStart) {
                HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.7f))
                PermissionRow(
                    title = "自启动",
                    detail = rom.autoStartStep,
                    allowed = false,
                    statusOverride = "请确认",
                ) {
                    openAutoStartSettings(context)
                }
            }
            HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.7f))
            PermissionRow(
                title = "后台运行",
                detail = rom.batteryStep,
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
            Hint("日课表、周课表、小组件与提醒均读取本机缓存。学校不可达或登录过期时仍可使用，但可能不是最新安排。清除本机数据会删除课表、个人资料与登录会话，之后需重新登录导入。")
        }
        Spacer(Modifier.height(110.dp))
    }

    if (confirmRemove) {
        AlertDialog(
            onDismissRequest = { confirmRemove = false },
            title = { Text("清除本机数据？") },
            text = {
                Text(
                    "将删除本机整学期课表及旧缓存、姓名学号和学校登录会话，并停止课程提醒与实时通知。" +
                        "桌面小组件将不再显示课程，离线课表也无法恢复，需重新登录学校账号导入。" +
                        "此操作不会删除学校的课表或账号，也不代表删除此前留在旧服务上的数据。",
                )
            },
            confirmButton = {
                TextButton(enabled = !busy, onClick = {
                    confirmRemove = false
                    onRemove()
                }) { Text("确认清除", color = MaterialTheme.colorScheme.error) }
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
                Hint("导出前请检查日志内容，不要公开学号、密码、登录凭据或个人课表。")
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
    statusOverride: String? = null,
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
            statusOverride ?: if (allowed) "已允许" else "未允许",
            fontSize = 12.sp,
            color = when {
                statusOverride != null -> MaterialTheme.colorScheme.onSurfaceVariant
                allowed -> SuccessGreen
                else -> WarmOrange
            },
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
