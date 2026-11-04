package wiki.nufe.kebiao.ui

import android.Manifest
import android.app.AlarmManager
import android.app.NotificationManager
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
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
import kotlinx.coroutines.launch
import kotlin.math.abs
import wiki.nufe.kebiao.R
import wiki.nufe.kebiao.data.Repository
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
    val manager = context.getSystemService(NotificationManager::class.java)
    var canNotify by remember { mutableStateOf(Reminders.canNotify(context)) }
    val permission = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        canNotify = granted
        Reminders.reschedule(context)
    }
    val liveName = liveUpdateName()
    var batteryOk by remember { mutableStateOf(ignoringBatteryOptimizations(context)) }
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    DisposableEffect(lifecycle) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) {
                batteryOk = ignoringBatteryOptimizations(context)
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
                Hint("开启后会在设定的时间发送通知提醒上课")
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
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(
                Modifier
                    .weight(1f)
                    .alpha(if (alarmEnabled) 1f else 0.45f),
            ) {
                Text("灵动显示", fontSize = 14.sp, fontWeight = FontWeight.Medium)
                Spacer(Modifier.height(2.dp))
                Hint(
                    if (Build.VERSION.SDK_INT >= 36) {
                        "仅 ColorOS 16+ 使用流体云，其他平台回退普通通知；开课 5 分钟后收起。"
                    } else {
                        "需要 Android 16 / ColorOS 16 或 HyperOS 3。"
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
                    onLiveUpdateChange(it)
                },
                enabled = alarmEnabled && Build.VERSION.SDK_INT >= 36,
            )
        }
        if (Build.VERSION.SDK_INT >= 36 && liveUpdate && alarmEnabled &&
            manager?.canPostPromotedNotifications() != true
        ) {
            Spacer(Modifier.height(6.dp))
            Hint("系统还没允许本应用的${liveName}提醒；到系统设置 → 通知与控制中心 → $liveName 里允许。")
            TextButton(
                onClick = {
                    context.startActivity(
                        Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                            .putExtra(Settings.EXTRA_APP_PACKAGE, context.packageName),
                    )
                },
                contentPadding = PaddingValues(0.dp),
            ) { Text("去通知设置", fontSize = 12.sp) }
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
                onClick = {
                    context.startActivity(
                        Intent(
                            Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM,
                            Uri.parse("package:${context.packageName}"),
                        ),
                    )
                },
                contentPadding = PaddingValues(0.dp),
            ) { Text("允许准时提醒", fontSize = 12.sp) }
        }
        if (alarmEnabled && !batteryOk) {
            Spacer(Modifier.height(6.dp))
            Hint("系统可能在后台冻结 App，导致提醒被推迟。")
            TextButton(
                onClick = { openBatteryOptimizationSettings(context) },
                contentPadding = PaddingValues(0.dp),
            ) { Text("允许后台运行", fontSize = 12.sp) }
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
