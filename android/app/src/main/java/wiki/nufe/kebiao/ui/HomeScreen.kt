package wiki.nufe.kebiao.ui

import android.content.Intent
import android.net.Uri
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.animation.togetherWith
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.delay
import wiki.nufe.kebiao.R
import wiki.nufe.kebiao.data.DayPlan
import wiki.nufe.kebiao.data.Note
import wiki.nufe.kebiao.data.Now
import wiki.nufe.kebiao.data.AppUpdate
import wiki.nufe.kebiao.data.Repository
import wiki.nufe.kebiao.data.needsLogin
import wiki.nufe.kebiao.data.Slot
import wiki.nufe.kebiao.data.Snapshot
import wiki.nufe.kebiao.data.State
import wiki.nufe.kebiao.data.Tone
import wiki.nufe.kebiao.data.WEEKDAY
import wiki.nufe.kebiao.data.annotate
import wiki.nufe.kebiao.data.buildTodayPlan
import wiki.nufe.kebiao.data.headerNote
import wiki.nufe.kebiao.data.lessonMs
import wiki.nufe.kebiao.data.shortDate
import wiki.nufe.kebiao.diag.Diag
import wiki.nufe.kebiao.remind.LiveUpdates
import wiki.nufe.kebiao.remind.Reminders
import wiki.nufe.kebiao.widget.parseColor

@Composable
internal fun UpdateBanner(update: AppUpdate, onDismiss: () -> Unit) {
    val context = LocalContext.current
    SectionCard {
        Text("发现新版本 ${update.version}", fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
        Spacer(Modifier.height(4.dp))
        Hint("下载安装包后覆盖安装即可，课表链接和设置都会保留。")
        Spacer(Modifier.height(6.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            TextButton(
                onClick = { context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(update.url))) },
                contentPadding = PaddingValues(horizontal = 4.dp),
            ) { Text("查看", fontSize = 13.sp) }
            TextButton(onClick = onDismiss, contentPadding = PaddingValues(horizontal = 4.dp)) {
                Text("先不更新", fontSize = 13.sp)
            }
        }
    }
}

/** 自动刷新停了：小字标题栏容易看漏，这里写清楚怎么恢复。 */
@Composable
internal fun LoginBanner(captcha: Boolean, busy: Boolean, onRefresh: () -> Unit, onOpenSite: () -> Unit) {
    SectionCard {
        Text(
            if (captcha) "需要重新验证" else "登录已失效",
            fontSize = 16.sp,
            fontWeight = FontWeight.SemiBold,
            color = MaterialTheme.colorScheme.error,
        )
        Spacer(Modifier.height(4.dp))
        Hint("课表已暂停更新，现在显示的可能不是最新的。到网站重新登录一次，就会恢复自动更新。")
        Spacer(Modifier.height(6.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            TextButton(onClick = onOpenSite, contentPadding = PaddingValues(horizontal = 4.dp)) {
                Text("去网站登录", fontSize = 13.sp)
            }
            TextButton(onClick = onRefresh, enabled = !busy, contentPadding = PaddingValues(horizontal = 4.dp)) {
                Text("已登录，刷新", fontSize = 13.sp)
            }
        }
    }
}

@Composable
internal fun HomeScreen(
    repo: Repository,
    snapshot: Snapshot?,
    busy: Boolean,
    message: String?,
    onRefresh: () -> Unit,
    onOpenSite: () -> Unit,
    update: AppUpdate?,
    onDismissUpdate: () -> Unit,
) {
    val context = LocalContext.current
    var now by remember { mutableStateOf(Now.current()) }
    var liveUpdate by remember { mutableStateOf(repo.liveUpdateEnabled) }
    var alarmEnabled by remember { mutableStateOf(repo.alarmEnabled) }
    var minutes by remember { mutableIntStateOf(repo.remindMinutes) }
    var showWheel by remember { mutableStateOf(false) }

    LaunchedEffect(Unit) {
        while (true) {
            delay(30_000)
            now = Now.current()
        }
    }

    val plan = snapshot?.let { buildTodayPlan(it.payload, now) }
    val changed = plan?.slots?.any { it.lesson.changed } == true || plan?.cancelled?.isNotEmpty() == true
    val note = snapshot?.let { headerNote(it, plan?.day, now, compact = false, changed = changed) }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .safeDrawingPadding()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 24.dp, vertical = 24.dp),
    ) {
        HeaderBlock()
        Spacer(Modifier.height(18.dp))
        StatusPill(plan, now)
        if (plan?.slots?.isNotEmpty() == true) Spacer(Modifier.height(18.dp))
        if (message != null) {
            Text(message, color = MaterialTheme.colorScheme.error, fontSize = 12.sp)
            Spacer(Modifier.height(10.dp))
        }
        update?.let {
            UpdateBanner(it, onDismissUpdate)
            Spacer(Modifier.height(14.dp))
        }
        snapshot?.payload?.takeIf { it.needsLogin }?.let { payload ->
            LoginBanner(payload.status == "captcha", busy, onRefresh, onOpenSite)
            Spacer(Modifier.height(14.dp))
        }
        TodayCardView(snapshot, plan, note, now, busy, onRefresh, onOpenSite)
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
                Reminders.reschedule(context)
            },
            liveUpdate = liveUpdate,
            onLiveUpdateChange = { checked ->
                Diag.i("live update enabled=$checked")
                liveUpdate = checked
                repo.liveUpdateEnabled = checked
                if (!checked) LiveUpdates.cancel(context)
                Reminders.reschedule(context)
            },
        )
        Spacer(Modifier.height(110.dp))
    }

    if (showWheel) {
        TimeWheelDialog(
            initial = minutes,
            onDismiss = { showWheel = false },
            onConfirm = { selected ->
                showWheel = false
                minutes = selected
                repo.remindMinutes = selected
                Reminders.reschedule(context)
            },
        )
    }
}

@Composable
internal fun HeaderBlock() {
    Text(
        "nufe课表",
        fontSize = 34.sp,
        fontWeight = FontWeight.Bold,
        letterSpacing = (-0.5).sp,
        color = MaterialTheme.colorScheme.onBackground,
    )
    Spacer(Modifier.height(4.dp))
    Text(
        "把课程放在桌面。",
        fontSize = 15.sp,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

@Composable
private fun StatusPill(plan: DayPlan?, now: Now) {
    val slot = plan?.slots?.firstOrNull() ?: return
    val date = plan.day?.date ?: return
    val target = when (slot.state) {
        State.LIVE -> lessonMs(date, slot.lesson.end)
        State.NEXT -> lessonMs(date, slot.lesson.start)
        else -> return
    }
    val remain = target - now.ms
    if (remain <= 0) return
    val text = when (slot.state) {
        State.LIVE -> "正在上课 · 剩 ${remainText(remain)}"
        State.NEXT -> "准备上课 · ${remainText(remain)}后"
        else -> return
    }
    val dark = isSystemInDarkTheme()
    Surface(
        color = if (dark) Color(0xFF2B2B30) else MaterialTheme.colorScheme.onBackground,
        contentColor = if (dark) Color(0xFFDADAE0) else MaterialTheme.colorScheme.background,
        shape = RoundedCornerShape(50),
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 16.dp, vertical = 9.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(Modifier.size(6.dp).clip(CircleShape).background(Color(0xFF7BD88F)))
            Spacer(Modifier.width(8.dp))
            Text(text, fontSize = 13.sp, fontWeight = FontWeight.Medium)
        }
    }
}

@Composable
private fun TodayCardView(
    snapshot: Snapshot?,
    plan: DayPlan?,
    note: Note?,
    now: Now,
    busy: Boolean,
    onRefresh: () -> Unit,
    onOpenSite: () -> Unit,
) {
    val tomorrow = plan?.title == "明天"
    var showAll by remember(plan?.day?.date) { mutableStateOf(false) }
    val fullSlots = plan?.day
        ?.let { if (tomorrow) annotate(it.lessons, -1, false) else annotate(it.lessons, now.minutes) }
        .orEmpty()
    val hiddenPast = fullSlots.count { it.state == State.PAST }

    SectionCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(plan?.title ?: "今天", fontSize = 18.sp, fontWeight = FontWeight.SemiBold)
            plan?.day?.let {
                Spacer(Modifier.width(8.dp))
                Text(
                    "周${WEEKDAY[it.weekday]} ${shortDate(it.date)}",
                    fontSize = 12.sp,
                    maxLines = 1,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Spacer(Modifier.weight(1f))
            note?.let {
                Text(it.text, fontSize = 11.sp, color = noteColor(it.tone))
                Spacer(Modifier.width(8.dp))
            }
            TextButton(
                onClick = onRefresh,
                enabled = !busy,
                contentPadding = PaddingValues(horizontal = 4.dp),
            ) { Text(if (busy) "刷新中" else "刷新", fontSize = 12.sp) }
        }
        if (plan != null && plan.slots.isNotEmpty()) {
            Spacer(Modifier.height(4.dp))
            Text(
                if (tomorrow) "明天还有 ${plan.slots.size} 门课" else "还有 ${plan.slots.size} 门课要上",
                fontSize = 12.sp,
                fontWeight = FontWeight.Medium,
                color = MaterialTheme.colorScheme.primary,
            )
        }
        Spacer(Modifier.height(12.dp))
        if (snapshot == null || plan == null) {
            Text("正在获取课表…", fontSize = 14.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            return@SectionCard
        }
        AnimatedContent(
            targetState = showAll,
            transitionSpec = {
                (expandVertically(
                    expandFrom = Alignment.Bottom,
                    animationSpec = tween(320, easing = FastOutSlowInEasing),
                ) + fadeIn(tween(200))) togetherWith
                    (shrinkVertically(
                        shrinkTowards = Alignment.Bottom,
                        animationSpec = tween(240, easing = FastOutSlowInEasing),
                    ) + fadeOut(tween(120)))
            },
            label = "today-expand",
        ) { expanded ->
            val shown = if (expanded && fullSlots.isNotEmpty()) fullSlots else plan.slots
            if (shown.isEmpty()) {
                Text(plan.empty, fontSize = 14.sp)
                val preview = plan.preview
                if (preview != null) {
                    Spacer(Modifier.height(12.dp))
                    Text(preview.title, fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Spacer(Modifier.height(8.dp))
                    preview.slots.firstOrNull()?.let { LessonRow(it) }
                }
            } else {
                Column {
                    shown.forEachIndexed { index, slot ->
                        LessonRow(slot)
                        if (index != shown.lastIndex) Spacer(Modifier.height(12.dp))
                    }
                }
            }
        }
        if (plan.cancelled.isNotEmpty()) {
            Spacer(Modifier.height(10.dp))
            Text(
                "停课：" + plan.cancelled.joinToString("、") { "${it.name} ${it.start}" },
                fontSize = 11.sp,
                color = WarmOrange,
            )
        }
        HorizontalDivider(
            modifier = Modifier.padding(top = 14.dp),
            color = MaterialTheme.colorScheme.outline.copy(alpha = 0.7f),
        )
        Row(verticalAlignment = Alignment.CenterVertically) {
            if (hiddenPast > 0) {
                TextButton(
                    onClick = { showAll = !showAll },
                    contentPadding = PaddingValues(0.dp),
                ) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(if (showAll) "收起" else "查看全天", fontSize = 12.sp)
                        Spacer(Modifier.width(2.dp))
                        Icon(
                            painter = painterResource(R.drawable.ic_chevron_down),
                            contentDescription = null,
                            tint = MaterialTheme.colorScheme.primary,
                            modifier = Modifier
                                .size(14.dp)
                                .rotate(if (showAll) 180f else 0f),
                        )
                    }
                }
            }
            Spacer(Modifier.weight(1f))
            TextButton(onClick = onOpenSite, contentPadding = PaddingValues(0.dp)) {
                Text("前往网站查看完整课表", fontSize = 12.sp)
                Spacer(Modifier.width(2.dp))
                Icon(
                    painter = painterResource(R.drawable.ic_chevron_right),
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.size(14.dp),
                )
            }
        }
    }
}

@Composable
private fun LessonRow(slot: Slot) {
    val lesson = slot.lesson
    Row(
        modifier = Modifier.alpha(if (slot.state == State.PAST) 0.45f else 1f),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            Modifier
                .width(3.dp)
                .height(38.dp)
                .clip(RoundedCornerShape(2.dp))
                .background(parseColor(lesson.color)),
        )
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    lesson.name,
                    fontSize = 14.sp,
                    fontWeight = FontWeight.Medium,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f, fill = false),
                )
                if (lesson.changed) {
                    Spacer(Modifier.width(6.dp))
                    Tag("调课", WarmOrange)
                }
            }
            Spacer(Modifier.height(2.dp))
            Text(
                "${lesson.start}–${lesson.end} · ${lesson.room.ifEmpty { "地点待定" }}",
                fontSize = 12.sp,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
        when (slot.state) {
            State.LIVE -> Tag("上课中", LiveRed)
            State.NEXT -> Tag("下一节", MaterialTheme.colorScheme.primary)
            State.PAST -> Tag("已结束", MaterialTheme.colorScheme.onSurfaceVariant)
            else -> Unit
        }
    }
}

@Composable
internal fun Tag(text: String, color: Color) {
    Surface(
        color = color.copy(alpha = 0.12f),
        contentColor = color,
        shape = RoundedCornerShape(50),
    ) {
        Text(
            text,
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 2.dp),
            fontSize = 10.sp,
            fontWeight = FontWeight.Medium,
        )
    }
}

@Composable
private fun noteColor(tone: Tone): Color = when (tone) {
    Tone.WARN -> MaterialTheme.colorScheme.error
    Tone.ACCENT -> MaterialTheme.colorScheme.primary
    Tone.FAINT -> MaterialTheme.colorScheme.onSurfaceVariant
}

private fun remainText(ms: Long): String {
    val total = (ms / 1000).coerceAtLeast(0)
    val hours = total / 3600
    val minutes = total % 3600 / 60
    return when {
        hours >= 1 && minutes >= 1 -> "$hours 小时 $minutes 分钟"
        hours >= 1 -> "$hours 小时"
        minutes >= 1 -> "$minutes 分钟"
        else -> "$total 秒"
    }
}
