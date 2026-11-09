package wiki.nufe.kebiao.ui

import android.Manifest
import android.app.AlarmManager
import android.os.Build
import android.widget.Toast
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.spring
import androidx.compose.foundation.clickable
import androidx.compose.foundation.pager.PagerDefaults
import androidx.compose.foundation.pager.PagerSnapDistance
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
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.pager.PageSize
import androidx.compose.foundation.pager.VerticalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.TransformOrigin
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import kotlin.math.abs
import wiki.nufe.kebiao.R
import wiki.nufe.kebiao.data.LiveDismissMode
import wiki.nufe.kebiao.data.Repository
import wiki.nufe.kebiao.remind.LiveUpdates
import wiki.nufe.kebiao.remind.Reminders

@Composable
internal fun ReminderSection(
    repo: Repository,
    minutes: Int,
    alarmEnabled: Boolean,
    onPickTime: () -> Unit,
    onAlarmToggle: (Boolean) -> Unit,
    liveUpdate: Boolean,
    onLiveUpdateChange: (Boolean) -> Unit,
) {
    val context = LocalContext.current
    val view = LocalView.current
    var canNotify by remember { mutableStateOf(Reminders.canNotify(context)) }
    var promotedOk by remember { mutableStateOf(isPromotedPermissionOk(context)) }
    var dismissMode by remember { mutableStateOf(repo.liveDismissMode) }
    var progressEnabled by remember { mutableStateOf(repo.liveProgressEnabled) }
    var chronometerEnabled by remember { mutableStateOf(repo.liveChronometer) }
    var previewActive by remember { mutableStateOf(LiveUpdates.isActive(context)) }
    var showDismissPicker by remember { mutableStateOf(false) }

    val permission = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        canNotify = granted
        Reminders.rescheduleAsync(context)
    }
    val liveName = liveUpdateName()
    var batteryOk by remember { mutableStateOf(ignoringBatteryOptimizations(context)) }
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    DisposableEffect(lifecycle) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) {
                canNotify = Reminders.canNotify(context)
                promotedOk = isPromotedPermissionOk(context)
                batteryOk = ignoringBatteryOptimizations(context)
                previewActive = LiveUpdates.isActive(context)
            }
        }
        lifecycle.addObserver(observer)
        onDispose { lifecycle.removeObserver(observer) }
    }

    SectionCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("上课通知", fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
                Spacer(Modifier.height(2.dp))
                Hint("开启后将在每节课前按设定时间发送上课提醒")
            }
            Switch(
                checked = alarmEnabled,
                onCheckedChange = {
                    view.performHapticFeedback(android.view.HapticFeedbackConstants.VIRTUAL_KEY)
                    onAlarmToggle(it)
                },
            )
        }

        HorizontalDivider(
            modifier = Modifier.padding(vertical = 12.dp),
            color = MaterialTheme.colorScheme.outline.copy(alpha = 0.7f),
        )
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .alpha(if (alarmEnabled) 1f else 0.45f)
                .clickable(enabled = alarmEnabled, onClick = onPickTime),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f)) {
                Text("上课前提示时间", fontSize = 14.sp, fontWeight = FontWeight.Medium)
                Spacer(Modifier.height(2.dp))
                Text(
                    if (minutes > 0) "提前 $minutes 分钟" else "未设置",
                    fontSize = 12.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Icon(
                painter = painterResource(R.drawable.ic_chevron_right),
                contentDescription = null,
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.size(18.dp),
            )
        }

        HorizontalDivider(
            modifier = Modifier.padding(vertical = 12.dp),
            color = MaterialTheme.colorScheme.outline.copy(alpha = 0.7f),
        )
        val fluidAvailable = isLiveUpdateSupported()
        val tier = remember { detectLiveUpdateTier() }
        val rom = remember { detectRomFamily() }
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(
                Modifier
                    .weight(1f)
                    .alpha(if (alarmEnabled && fluidAvailable) 1f else 0.45f),
            ) {
                Text("灵动显示（$liveName）", fontSize = 14.sp, fontWeight = FontWeight.Medium)
                Spacer(Modifier.height(2.dp))
                Hint(
                    when (tier) {
                        LiveUpdateTier.NATIVE_PROMOTED ->
                            "开课前后在状态栏与通知栏显示${liveName}实时卡片，收起时间与样式可在下方自定义。"
                        LiveUpdateTier.FLYME_CAPSULE ->
                            "尝试通过魅族${liveName}扩展在状态栏与通知栏显示实时卡片，收起时间与样式可在下方自定义。"
                        LiveUpdateTier.OEM_EXTRAS ->
                            "实验性：附带${rom.displayName}${liveName}扩展字段（若受系统白名单限制则显示为常驻倒计时通知，不影响正常响铃提醒）。"
                        LiveUpdateTier.UNSUPPORTED ->
                            "需要 Android 16+ 或受支持的定制系统；当前系统将使用普通通知。"
                    },
                )
                if (!alarmEnabled) {
                    Spacer(Modifier.height(2.dp))
                    Hint("开启“上课通知”后生效。")
                }
            }
            Switch(
                checked = liveUpdate,
                onCheckedChange = {
                    view.performHapticFeedback(android.view.HapticFeedbackConstants.VIRTUAL_KEY)
                    if (!it) previewActive = false
                    onLiveUpdateChange(it)
                },
                enabled = alarmEnabled && fluidAvailable,
            )
        }
        if (fluidAvailable && liveUpdate && alarmEnabled && !promotedOk) {
            Spacer(Modifier.height(6.dp))
            Hint("尚未授予${liveName}权限，请前往系统“设置 → 通知与控制中心 → $liveName”中开启。")
            TextButton(
                onClick = { openPromotedNotificationSettings(context) },
                contentPadding = PaddingValues(0.dp),
            ) { Text("去通知设置", fontSize = 12.sp) }
        }
        if (fluidAvailable && liveUpdate && alarmEnabled) {
            HorizontalDivider(
                modifier = Modifier.padding(vertical = 12.dp),
                color = MaterialTheme.colorScheme.outline.copy(alpha = 0.7f),
            )
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .clickable { showDismissPicker = true },
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(Modifier.weight(1f)) {
                    Text("自动收起时机", fontSize = 14.sp, fontWeight = FontWeight.Medium)
                    Spacer(Modifier.height(2.dp))
                    Text(
                        dismissMode.label,
                        fontSize = 12.sp,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                Icon(
                    painter = painterResource(R.drawable.ic_chevron_right),
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.size(18.dp),
                )
            }

            HorizontalDivider(
                modifier = Modifier.padding(vertical = 12.dp),
                color = MaterialTheme.colorScheme.outline.copy(alpha = 0.7f),
            )
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("显示卡片进度条", fontSize = 14.sp, fontWeight = FontWeight.Medium)
                    Spacer(Modifier.height(2.dp))
                    Hint("在实时卡片中显示课前准备或课堂进度条")
                }
                Switch(
                    checked = progressEnabled,
                    onCheckedChange = {
                        view.performHapticFeedback(android.view.HapticFeedbackConstants.VIRTUAL_KEY)
                        progressEnabled = it
                        repo.liveProgressEnabled = it
                        LiveUpdates.refreshActiveIfShown(context)
                    },
                )
            }

            HorizontalDivider(
                modifier = Modifier.padding(vertical = 12.dp),
                color = MaterialTheme.colorScheme.outline.copy(alpha = 0.7f),
            )
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("显示秒级倒计时", fontSize = 14.sp, fontWeight = FontWeight.Medium)
                    Spacer(Modifier.height(2.dp))
                    Hint("在卡片第三行以“X分XX秒后上/下课”形式实时跳秒")
                }
                Switch(
                    checked = chronometerEnabled,
                    onCheckedChange = {
                        view.performHapticFeedback(android.view.HapticFeedbackConstants.VIRTUAL_KEY)
                        chronometerEnabled = it
                        repo.liveChronometer = it
                        LiveUpdates.refreshActiveIfShown(context)
                    },
                )
            }

            HorizontalDivider(
                modifier = Modifier.padding(vertical = 12.dp),
                color = MaterialTheme.colorScheme.outline.copy(alpha = 0.7f),
            )
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .clickable {
                        view.performHapticFeedback(android.view.HapticFeedbackConstants.VIRTUAL_KEY)
                        if (previewActive) {
                            LiveUpdates.cancel(context)
                            previewActive = false
                            Toast.makeText(context, "已收起${liveName}预览", Toast.LENGTH_SHORT).show()
                        } else if (!canNotify && Build.VERSION.SDK_INT >= 33) {
                            permission.launch(Manifest.permission.POST_NOTIFICATIONS)
                        } else {
                            val shown = LiveUpdates.preview(context)
                            previewActive = shown
                            val msg = if (shown) {
                                "已发送${liveName}预览，请查看状态栏或通知栏"
                            } else {
                                "无法发送预览，请先开启通知权限"
                            }
                            Toast.makeText(context, msg, Toast.LENGTH_SHORT).show()
                        }
                    },
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(Modifier.weight(1f)) {
                    Text(
                        if (previewActive) "收起${liveName}预览" else "立即预览${liveName}效果",
                        fontSize = 14.sp,
                        fontWeight = FontWeight.Medium,
                        color = MaterialTheme.colorScheme.primary,
                    )
                    Spacer(Modifier.height(2.dp))
                    Hint(
                        if (previewActive) "当前正在显示实时卡片，点击可立即关闭"
                        else "发送一条测试卡片，立即查看当前系统的展示效果",
                    )
                }
                Icon(
                    painter = painterResource(R.drawable.ic_chevron_right),
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.primary,
                    modifier = Modifier.size(18.dp),
                )
            }
        }
        if (alarmEnabled && minutes > 0 && !canNotify && Build.VERSION.SDK_INT >= 33) {
            TextButton(
                onClick = { permission.launch(Manifest.permission.POST_NOTIFICATIONS) },
                contentPadding = PaddingValues(0.dp),
            ) { Text("允许通知", fontSize = 12.sp) }
        }
        if (alarmEnabled && minutes > 0 && Build.VERSION.SDK_INT >= 31 &&
            context.getSystemService(AlarmManager::class.java).canScheduleExactAlarms() == false
        ) {
            TextButton(
                onClick = { openExactAlarmSettings(context) },
                contentPadding = PaddingValues(0.dp),
            ) { Text("允许准时提醒", fontSize = 12.sp) }
        }
        if (alarmEnabled && (!batteryOk || rom.needsAutoStart)) {
            Spacer(Modifier.height(6.dp))
            Hint(
                if (rom.needsAutoStart) {
                    "${rom.displayName} 的电池白名单不等于后台放行；${rom.batteryToast}。"
                } else {
                    "系统可能会限制应用后台运行，导致课前提醒或小组件刷新被推迟。"
                },
            )
            TextButton(
                onClick = { openBatteryOptimizationSettings(context, preferOemPage = batteryOk) },
                contentPadding = PaddingValues(0.dp),
            ) { Text(if (rom.needsAutoStart && batteryOk) "检查后台设置" else "允许后台运行", fontSize = 12.sp) }
        }
    }

    if (showDismissPicker) {
        OptionPickerDialog(
            title = "自动收起时机",
            subtitle = "选择${liveName}卡片在何时自动关闭",
            options = LiveDismissMode.entries.map { it to (it.label to it.desc) },
            selected = dismissMode,
            onDismiss = { showDismissPicker = false },
            onSelect = { mode ->
                showDismissPicker = false
                dismissMode = mode
                repo.liveDismissMode = mode
                LiveUpdates.refreshActiveIfShown(context)
            },
        )
    }
}

@Composable
private fun <T> OptionPickerDialog(
    title: String,
    subtitle: String,
    options: List<Pair<T, Pair<String, String>>>,
    selected: T,
    onDismiss: () -> Unit,
    onSelect: (T) -> Unit,
) {
    PopupCard(onDismissRequest = onDismiss) {
        Text(title, fontSize = 18.sp, fontWeight = FontWeight.SemiBold)
        Spacer(Modifier.height(2.dp))
        Text(
            subtitle,
            fontSize = 12.sp,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.height(10.dp))
        options.forEachIndexed { index, (value, textPair) ->
            if (index > 0) {
                HorizontalDivider(
                    modifier = Modifier.padding(vertical = 4.dp),
                    color = MaterialTheme.colorScheme.outline.copy(alpha = 0.6f),
                )
            }
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .clickable { onSelect(value) }
                    .padding(vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(Modifier.weight(1f)) {
                    Text(textPair.first, fontSize = 14.sp, fontWeight = FontWeight.Medium)
                    Spacer(Modifier.height(2.dp))
                    Text(
                        textPair.second,
                        fontSize = 12.sp,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                RadioButton(
                    selected = value == selected,
                    onClick = { onSelect(value) },
                )
            }
        }
        Spacer(Modifier.height(4.dp))
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.End,
        ) {
            TextButton(
                onClick = onDismiss,
                colors = ButtonDefaults.textButtonColors(
                    contentColor = MaterialTheme.colorScheme.onSurface,
                ),
            ) { Text("取消") }
        }
    }
}

@Composable
internal fun TimeWheelDialog(
    initial: Int,
    onDismiss: () -> Unit,
    onConfirm: (Int) -> Unit,
) {
    val start = initial.coerceIn(1, 60)
    var selected by remember { mutableIntStateOf(start) }

    PopupCard(onDismissRequest = onDismiss) {
        Text("上课前提示时间", fontSize = 18.sp, fontWeight = FontWeight.SemiBold)
        Spacer(Modifier.height(2.dp))
        Text(
            "提前多久提醒你",
            fontSize = 12.sp,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.height(8.dp))
        MinuteWheel(initial = start, onSelected = { selected = it })
        Spacer(Modifier.height(8.dp))
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp, Alignment.End),
        ) {
            TextButton(
                onClick = onDismiss,
                colors = ButtonDefaults.textButtonColors(
                    contentColor = MaterialTheme.colorScheme.onSurface,
                ),
            ) { Text("取消") }
            Button(
                onClick = { onConfirm(selected) },
                colors = ButtonDefaults.buttonColors(
                    containerColor = MaterialTheme.colorScheme.primary,
                    contentColor = MaterialTheme.colorScheme.onPrimary,
                ),
                shape = RoundedCornerShape(14.dp),
            ) { Text("确定") }
        }
    }
}

/** 数字单独滚动，“提前 / 分钟”固定；上下相邻数字按距离缩小、变淡并透视。 */
@Composable
private fun MinuteWheel(initial: Int, onSelected: (Int) -> Unit) {
    val pager = rememberPagerState(initialPage = initial.coerceIn(1, 60) - 1, pageCount = { 60 })
    val view = LocalView.current
    var lastPage by remember { mutableIntStateOf(pager.currentPage) }
    LaunchedEffect(pager) {
        snapshotFlow { pager.currentPage }.collect { page ->
            onSelected(page + 1)
            if (page != lastPage) {
                lastPage = page
                view.performHapticFeedback(android.view.HapticFeedbackConstants.CLOCK_TICK)
            }
        }
    }

    Row(
        modifier = Modifier.fillMaxWidth().height(220.dp),
        horizontalArrangement = Arrangement.Center,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text("提前", fontSize = 17.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.width(14.dp))
        Box(Modifier.width(84.dp).height(220.dp)) {
            VerticalPager(
                state = pager,
                modifier = Modifier.fillMaxSize(),
                pageSize = PageSize.Fixed(44.dp),
                contentPadding = PaddingValues(vertical = 88.dp),
                pageSpacing = 0.dp,
                flingBehavior = PagerDefaults.flingBehavior(
                    state = pager,
                    pagerSnapDistance = PagerSnapDistance.atMost(3),
                    snapAnimationSpec = spring<Float>(
                        dampingRatio = 0.82f,
                        stiffness = Spring.StiffnessMediumLow,
                    ),
                ),
            ) { page ->
                val current = pager.currentPage + pager.currentPageOffsetFraction
                val distance = abs(page - current)
                Box(
                    modifier = Modifier.fillMaxWidth().height(44.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    Text(
                        text = (page + 1).toString(),
                        fontSize = 30.sp,
                        fontWeight = if (page == pager.currentPage) FontWeight.SemiBold else FontWeight.Normal,
                        color = if (page == pager.currentPage) {
                            MaterialTheme.colorScheme.onSurface
                        } else {
                            MaterialTheme.colorScheme.onSurfaceVariant
                        },
                        textAlign = TextAlign.Center,
                        modifier = Modifier.graphicsLayer {
                            val p = distance.coerceAtMost(3f)
                            alpha = (1f - p * 0.28f).coerceIn(0.15f, 1f)
                            val scale = (1f - p * 0.16f).coerceIn(0.55f, 1f)
                            scaleX = scale
                            scaleY = scale
                            rotationX = (page - current) * 12f
                            transformOrigin = TransformOrigin(0.5f, 0.5f)
                            cameraDistance = 24f * density
                        },
                    )
                }
            }
        }
        Spacer(Modifier.width(14.dp))
        Text("分钟", fontSize = 17.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

/** 页面弹窗统一从下向上展开；慢-快-慢的缓动，和页面内的展开动画一致。 */
