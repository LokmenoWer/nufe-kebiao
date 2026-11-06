package wiki.nufe.kebiao.ui

import android.content.Intent
import android.net.Uri
import android.text.format.DateFormat
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInHorizontally
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutHorizontally
import androidx.compose.animation.slideOutVertically
import androidx.compose.animation.togetherWith
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.Easing
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.layout.positionInParent
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import wiki.nufe.kebiao.R
import wiki.nufe.kebiao.data.Cancelled
import wiki.nufe.kebiao.data.Day
import wiki.nufe.kebiao.data.DayColumn
import wiki.nufe.kebiao.data.DayPlan
import wiki.nufe.kebiao.data.Lesson
import wiki.nufe.kebiao.data.Note
import wiki.nufe.kebiao.data.Now
import wiki.nufe.kebiao.data.AppUpdate
import wiki.nufe.kebiao.data.needsLogin
import wiki.nufe.kebiao.data.Slot
import wiki.nufe.kebiao.data.Snapshot
import wiki.nufe.kebiao.data.State
import wiki.nufe.kebiao.data.Tone
import wiki.nufe.kebiao.data.WEEKDAY
import wiki.nufe.kebiao.data.annotate
import wiki.nufe.kebiao.data.buildTodayPlan
import wiki.nufe.kebiao.data.isRestMessage
import wiki.nufe.kebiao.data.headerNote
import wiki.nufe.kebiao.data.lessonMs
import wiki.nufe.kebiao.data.shortDate
import wiki.nufe.kebiao.data.weekColumns
import wiki.nufe.kebiao.data.weekToShow
import wiki.nufe.kebiao.widget.CHIP_GAP
import wiki.nufe.kebiao.widget.CHIP_PAD_H
import wiki.nufe.kebiao.widget.CHIP_PAD_V
import wiki.nufe.kebiao.widget.Cell
import wiki.nufe.kebiao.widget.GridLayout
import wiki.nufe.kebiao.widget.Kind
import wiki.nufe.kebiao.widget.TextMeasure
import wiki.nufe.kebiao.widget.bestPieces
import wiki.nufe.kebiao.widget.cellsOf
import wiki.nufe.kebiao.widget.layoutGrid
import wiki.nufe.kebiao.widget.mix
import wiki.nufe.kebiao.widget.parseColor
import wiki.nufe.kebiao.widget.pieceBold
import wiki.nufe.kebiao.widget.roomText
import wiki.nufe.kebiao.widget.size

@Composable
internal fun UpdateBanner(update: AppUpdate, flow: UpdateFlow, onDismiss: () -> Unit) {
    val step = flow.step
    SectionCard {
        Text("发现新版本 ${update.version}", fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
        Spacer(Modifier.height(4.dp))
        when (step) {
            UpdateStep.Idle -> Hint("点击“立即更新”将自动下载安装包并校验签名，原有课表连接与提醒设置均会保留。")
            is UpdateStep.Downloading -> {
                Hint(if (step.percent != null) "正在下载 ${step.percent}%" else "正在下载…")
                Spacer(Modifier.height(6.dp))
                if (step.percent != null) {
                    LinearProgressIndicator(progress = { step.percent / 100f }, modifier = Modifier.fillMaxWidth())
                } else {
                    LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
                }
            }
            UpdateStep.NeedPermission -> Hint("首次应用内更新需授予“安装未知应用”权限，开启后返回即可继续安装。")
            is UpdateStep.Failed -> Text(step.message, fontSize = 12.sp, color = MaterialTheme.colorScheme.error)
        }
        Spacer(Modifier.height(6.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            val pad = PaddingValues(horizontal = 4.dp)
            when (step) {
                UpdateStep.Idle -> TextButton(onClick = { flow.start(update) }, contentPadding = pad) {
                    Text("立即更新", fontSize = 13.sp)
                }
                is UpdateStep.Downloading -> TextButton(onClick = flow::cancel, contentPadding = pad) {
                    Text("取消", fontSize = 13.sp)
                }
                UpdateStep.NeedPermission -> TextButton(onClick = flow::openPermissionSettings, contentPadding = pad) {
                    Text("去允许", fontSize = 13.sp)
                }
                is UpdateStep.Failed -> {
                    TextButton(onClick = { flow.start(update) }, contentPadding = pad) {
                        Text("重试", fontSize = 13.sp)
                    }
                    TextButton(onClick = { flow.openInBrowser(update) }, contentPadding = pad) {
                        Text("用浏览器下载", fontSize = 13.sp)
                    }
                }
            }
            if (step !is UpdateStep.Downloading) {
                TextButton(onClick = onDismiss, contentPadding = pad) { Text("先不更新", fontSize = 13.sp) }
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
        Hint("后台自动同步已暂停，当前展示的可能不是最新课表。请前往网站重新登录一次以恢复自动更新。")
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
    snapshot: Snapshot?,
    busy: Boolean,
    message: String?,
    onRefresh: () -> Unit,
    onOpenSite: () -> Unit,
    update: AppUpdate?,
    updateFlow: UpdateFlow,
    onDismissUpdate: () -> Unit,
    initialWeek: Boolean = false,
    viewPulse: Int = 0,
    onViewConsumed: () -> Unit = {},
    rhythmClock12h: Boolean = true,
) {
    var now by remember { mutableStateOf(Now.current()) }

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
        Spacer(Modifier.height(12.dp))
        EnterUp { StatusPill(plan, now) }
        if (plan?.slots?.isNotEmpty() == true) Spacer(Modifier.height(18.dp))
        if (message != null) {
            val isSuccess = message == "已绑定这台设备。"
            Text(
                message,
                color = if (isSuccess) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.error,
                fontSize = 12.sp,
            )
            Spacer(Modifier.height(10.dp))
        }
        update?.let {
            UpdateBanner(it, updateFlow, onDismissUpdate)
            Spacer(Modifier.height(14.dp))
        }
        snapshot?.payload?.takeIf { it.needsLogin }?.let { payload ->
            LoginBanner(payload.status == "captcha", busy, onRefresh, onOpenSite)
            Spacer(Modifier.height(14.dp))
        }
        EnterUp(delayMillis = 70) {
            TodayCardView(snapshot, plan, note, now, busy, onRefresh, initialWeek, viewPulse, onViewConsumed)
        }
        Spacer(Modifier.height(14.dp))
        val week = snapshot?.payload?.week?.week
        Row(
            modifier = Modifier.fillMaxWidth().height(IntrinsicSize.Max),
            horizontalArrangement = Arrangement.spacedBy(12.dp),
            verticalAlignment = Alignment.Top,
        ) {
            Box(Modifier.weight(1f)) {
                TodayRhythmCard(plan, now, clock12h = rhythmClock12h)
            }
            if (week != null && week > 0) {
                SemesterProgressCard(
                    week = week,
                    modifier = Modifier.width(72.dp).fillMaxHeight(),
                )
            }
        }
        Spacer(Modifier.height(110.dp))
    }
}

@Composable
internal fun HeaderBlock() {
    Text(
        "nufe课表",
        fontSize = 28.sp,
        fontWeight = FontWeight.Bold,
        letterSpacing = (-0.5).sp,
        color = MaterialTheme.colorScheme.onBackground,
    )
    Spacer(Modifier.height(2.dp))
    Text(
        stringResource(R.string.app_tagline),
        fontSize = 14.sp,
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

private fun syncTime(ms: Long): String = DateFormat.format("HH:mm", ms).toString()

private enum class CardMode { DAY, WEEK }

/** 二次函数缓动：切换周视图时先把高度变化做得更明显，再自然收住。 */
private val QuadraticEaseInOut = Easing { fraction ->
    if (fraction < 0.5f) {
        2f * fraction * fraction
    } else {
        1f - (-2f * fraction + 2f).let { it * it } / 2f
    }
}

private data class SelectedCourse(
    val day: Day,
    val slots: List<Slot>,
    val cancelled: Cancelled? = null,
)

@Composable
private fun TodayCardView(
    snapshot: Snapshot?,
    plan: DayPlan?,
    note: Note?,
    now: Now,
    busy: Boolean,
    onRefresh: () -> Unit,
    initialWeek: Boolean,
    viewPulse: Int,
    onViewConsumed: () -> Unit,
) {
    var mode by remember { mutableStateOf(CardMode.DAY) }
    val view = LocalView.current
    LaunchedEffect(viewPulse, initialWeek) {
        if (viewPulse > 0) {
            mode = if (initialWeek) CardMode.WEEK else CardMode.DAY
            onViewConsumed()
        }
    }
    var preferNextWeek by remember { mutableStateOf<Boolean?>(null) }
    var selectedCourse by remember { mutableStateOf<SelectedCourse?>(null) }
    val tomorrow = plan?.title == "明天"
    var showAll by remember(plan?.day?.date) { mutableStateOf(false) }
    val fullSlots = plan?.day
        ?.let { if (tomorrow) annotate(it.lessons, -1, false) else annotate(it.lessons, now.minutes) }
        .orEmpty()
    val hiddenPast = fullSlots.count { it.state == State.PAST }

    val payload = snapshot?.payload
    val defaultWeek = payload?.let { weekToShow(it, now) }
    val showNext = preferNextWeek ?: (defaultWeek?.first == "下周")
    val weekTitle = if (showNext && payload?.nextWeek != null) "下周" else "本周"
    val baseWeekBlock = payload?.week
    val nextWeekBlock = payload?.nextWeek
    val baseWeekCols = remember(baseWeekBlock, now.date, now.minutes) { weekColumns(baseWeekBlock, now) }
    val nextWeekCols = remember(nextWeekBlock, now.date, now.minutes) { weekColumns(nextWeekBlock, now) }
    val weekBlock = if (showNext && nextWeekBlock != null) nextWeekBlock else baseWeekBlock
    val weekCols = if (showNext && nextWeekBlock != null) nextWeekCols else baseWeekCols
    val weekRange = if (weekCols.isNotEmpty()) {
        val first = shortDate(weekCols.first().day.date)
        val last = shortDate(weekCols.last().day.date)
        if (first == last) first else "$first–$last"
    } else ""
    val weekFirstDay = weekCols.firstOrNull()?.day
    val weekChanged = weekCols.any { c -> c.cancelled.isNotEmpty() || c.slots.any { it.lesson.changed } }
    val weekNote = snapshot?.let { headerNote(it, weekFirstDay, now, compact = false, changed = weekChanged) }
    val activeNote = if (mode == CardMode.WEEK) {
        val warning = weekNote?.takeIf { !it.text.startsWith("第 ") }
        val text = warning?.text ?: weekBlock?.week?.let { "第 $it 周" } ?: weekNote?.text ?: "本周"
        val label = if (!showNext && warning == null && weekBlock?.week != null && weekCols.isNotEmpty()) {
            "$text · ${weekCols.size} 天"
        } else {
            text
        }
        Note(label, warning?.tone ?: Tone.FAINT)
    } else {
        note
    }

    SectionCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                if (mode == CardMode.WEEK) weekTitle else (plan?.title ?: "今天"),
                fontSize = 18.sp,
                fontWeight = FontWeight.SemiBold,
            )
            val subtitle = if (mode == CardMode.WEEK) {
                weekRange
            } else {
                plan?.day?.let { "周${WEEKDAY[it.weekday]} ${shortDate(it.date)}" }.orEmpty()
            }
            if (subtitle.isNotEmpty()) {
                Spacer(Modifier.width(8.dp))
                Text(
                    subtitle,
                    fontSize = 12.sp,
                    maxLines = 1,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Spacer(Modifier.weight(1f))
            activeNote?.let {
                Text(it.text, fontSize = 11.sp, color = noteColor(it.tone))
                Spacer(Modifier.width(6.dp))
            }
            TextButton(
                onClick = {
                    view.performHapticFeedback(android.view.HapticFeedbackConstants.VIRTUAL_KEY)
                    onRefresh()
                },
                enabled = !busy,
                contentPadding = PaddingValues(horizontal = 4.dp),
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    if (busy) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(14.dp),
                            strokeWidth = 1.5.dp,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    } else {
                        Icon(
                            painter = painterResource(R.drawable.ic_refresh),
                            contentDescription = null,
                            tint = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.size(14.dp),
                        )
                    }
                    Spacer(Modifier.width(3.dp))
                    Text(if (busy) "刷新中" else "刷新", fontSize = 12.sp)
                }
            }
        }
        val syncText = snapshot?.let {
            if (it.offline) "离线 · 缓存于 ${syncTime(it.fetchedAtMs)}"
            else "已同步 ${syncTime(it.fetchedAtMs)}"
        }
        Spacer(Modifier.height(6.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            if (mode == CardMode.DAY && plan != null && plan.slots.isNotEmpty()) {
                Text(
                    "还有 ${plan.slots.size} 门课",
                    fontSize = 12.sp,
                    fontWeight = FontWeight.Medium,
                    color = MaterialTheme.colorScheme.primary,
                )
            } else if (mode == CardMode.WEEK) {
                Text(
                    "共有 ${weekCols.sumOf { it.slots.size }} 门课",
                    fontSize = 12.sp,
                    fontWeight = FontWeight.Medium,
                    color = MaterialTheme.colorScheme.primary,
                )
            }
            Spacer(Modifier.weight(1f))
            if (mode == CardMode.WEEK && payload?.week != null && payload.nextWeek != null) {
                TextButton(
                    onClick = {
                        view.performHapticFeedback(android.view.HapticFeedbackConstants.VIRTUAL_KEY)
                        preferNextWeek = !showNext
                    },
                    contentPadding = PaddingValues(horizontal = 6.dp, vertical = 0.dp),
                    modifier = Modifier.height(28.dp),
                ) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(if (showNext) "看本周" else "看下周", fontSize = 11.sp)
                        Spacer(Modifier.width(2.dp))
                        Icon(
                            painter = painterResource(R.drawable.ic_chevron_right),
                            contentDescription = null,
                            tint = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier
                                .size(14.dp)
                                .rotate(if (showNext) 180f else 0f),
                        )
                    }
                }
                Spacer(Modifier.width(8.dp))
            }
            ModeToggle(
                mode = mode,
                dayLabel = plan?.title ?: "今天",
                weekLabel = weekTitle,
                onSelect = { mode = it },
            )
        }
        Spacer(Modifier.height(12.dp))
        if (snapshot == null || plan == null) {
            Text("正在获取课表…", fontSize = 14.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            return@SectionCard
        }
        AnimatedContent(
            targetState = mode,
            transitionSpec = {
                val toWeek = targetState == CardMode.WEEK
                (slideInVertically(
                    animationSpec = tween(420, easing = QuadraticEaseInOut),
                    initialOffsetY = { if (toWeek) -it / 4 else it / 4 },
                ) + fadeIn(tween(180))) togetherWith
                    (slideOutVertically(
                        animationSpec = tween(320, easing = QuadraticEaseInOut),
                        targetOffsetY = { if (toWeek) it / 4 else -it / 4 },
                    ) + fadeOut(tween(160)))
            },
            contentAlignment = Alignment.TopCenter,
            label = "card-mode",
        ) { targetMode ->
            if (targetMode == CardMode.WEEK) {
                val canSwitchWeek = payload?.week != null && nextWeekBlock != null
                val swipeThreshold = with(LocalDensity.current) { 56.dp.toPx() }
                AnimatedContent(
                    targetState = showNext,
                    modifier = Modifier.pointerInput(canSwitchWeek, showNext) {
                        if (!canSwitchWeek) return@pointerInput
                        var dragged = 0f
                        detectHorizontalDragGestures(
                            onDragStart = { dragged = 0f },
                            onHorizontalDrag = { change, amount ->
                                dragged += amount
                                change.consume()
                            },
                            onDragEnd = {
                                // 左滑看下周、右滑回本周；已经在目标周时不重复切换。
                                val target = when {
                                    dragged <= -swipeThreshold -> true
                                    dragged >= swipeThreshold -> false
                                    else -> null
                                }
                                if (target != null && target != showNext) {
                                    view.performHapticFeedback(android.view.HapticFeedbackConstants.VIRTUAL_KEY)
                                    preferNextWeek = target
                                }
                            },
                        )
                    },
                    transitionSpec = {
                        val direction = if (targetState) 1 else -1
                        (slideInHorizontally(
                            animationSpec = tween(360, easing = QuadraticEaseInOut),
                            initialOffsetX = { it * direction },
                        ) + fadeIn(tween(160))) togetherWith
                            (slideOutHorizontally(
                                animationSpec = tween(300, easing = QuadraticEaseInOut),
                                targetOffsetX = { -it * direction },
                            ) + fadeOut(tween(140)))
                    },
                    contentAlignment = Alignment.Center,
                    label = "week-swipe",
                ) { next ->
                    val block = if (next && nextWeekBlock != null) nextWeekBlock else baseWeekBlock
                    val columns = if (next && nextWeekBlock != null) nextWeekCols else baseWeekCols
                    WeekGridView(
                        columns = columns,
                        isHoliday = block?.week == null,
                        onSelectCourse = { selectedCourse = it },
                    )
                }
            } else {
                AnimatedContent(
                    targetState = showAll,
                    transitionSpec = {
                        val collapsing = !targetState
                        (slideInVertically(
                            animationSpec = tween(320, easing = QuadraticEaseInOut),
                            initialOffsetY = { if (collapsing) it / 3 else -it / 3 },
                        ) + fadeIn(tween(180))) togetherWith
                            (slideOutVertically(
                                animationSpec = tween(240, easing = QuadraticEaseInOut),
                                targetOffsetY = { if (collapsing) -it / 3 else it / 3 },
                            ) + fadeOut(tween(120)))
                    },
                    label = "today-expand",
                ) { expanded ->
                    val shown = if (expanded && fullSlots.isNotEmpty()) fullSlots else plan.slots
                    if (shown.isEmpty()) {
                        Column {
                            Text(
                                "⭐️ ${plan.empty}",
                                fontSize = 14.sp,
                                fontWeight = FontWeight.SemiBold,
                                color = if (isRestMessage(plan.empty)) SuccessGreen else MaterialTheme.colorScheme.onSurface,
                            )
                            plan.tomorrowHint?.let { hint ->
                                Spacer(Modifier.height(4.dp))
                                Text(
                                    hint,
                                    fontSize = 13.sp,
                                    color = if (isRestMessage(hint)) SuccessGreen else MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                            val preview = plan.preview
                            if (preview != null) {
                                Spacer(Modifier.height(12.dp))
                                HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.6f))
                                Spacer(Modifier.height(12.dp))
                                if (plan.showPreviewTitle) {
                                    Row(verticalAlignment = Alignment.CenterVertically) {
                                        Text(
                                            preview.title, fontSize = 12.sp, fontWeight = FontWeight.SemiBold,
                                            color = MaterialTheme.colorScheme.primary,
                                        )
                                        preview.slots.firstOrNull()?.lesson?.let { first ->
                                            Spacer(Modifier.width(8.dp))
                                            Text(
                                                first.start, fontSize = 18.sp, fontWeight = FontWeight.SemiBold,
                                                color = MaterialTheme.colorScheme.primary,
                                            )
                                        }
                                    }
                                    Spacer(Modifier.height(8.dp))
                                }
                                preview.slots.forEachIndexed { index, slot ->
                                    LessonRow(slot) {
                                        selectedCourse = SelectedCourse(preview.day, listOf(slot))
                                    }
                                    if (index != preview.slots.lastIndex) Spacer(Modifier.height(12.dp))
                                }
                                if (preview.cancelled.isNotEmpty()) {
                                    Spacer(Modifier.height(8.dp))
                                    Text(
                                        "停课：" + preview.cancelled.joinToString("、") { "${it.name} ${it.start}" },
                                        fontSize = 11.sp,
                                        color = WarmOrange,
                                    )
                                }
                            }
                        }
                    } else {
                        Column {
                            shown.forEachIndexed { index, slot ->
                                LessonRow(slot) {
                                    plan.day?.let { d -> selectedCourse = SelectedCourse(d, listOf(slot)) }
                                }
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
            }
        }
        if (syncText != null || (mode == CardMode.DAY && hiddenPast > 0)) {
            HorizontalDivider(
                modifier = Modifier.padding(top = 14.dp),
                color = MaterialTheme.colorScheme.outline.copy(alpha = 0.7f),
            )
            Row(verticalAlignment = Alignment.CenterVertically) {
                if (syncText != null) {
                    Text(
                        syncText,
                        fontSize = 11.sp,
                        color = if (snapshot?.offline == true) WarmOrange
                        else MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                Spacer(Modifier.weight(1f))
                if (mode == CardMode.DAY && hiddenPast > 0) {
                    TextButton(
                        onClick = {
                            view.performHapticFeedback(android.view.HapticFeedbackConstants.VIRTUAL_KEY)
                            showAll = !showAll
                        },
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
            }
        }
    }

    selectedCourse?.let { detail ->
        CourseDetailDialog(detail = detail, onDismiss = { selectedCourse = null })
    }
}

@Composable
private fun ModeToggle(
    mode: CardMode,
    dayLabel: String,
    weekLabel: String,
    onSelect: (CardMode) -> Unit,
) {
    SlidingToggle(
        firstLabel = dayLabel,
        secondLabel = weekLabel,
        selectedIndex = if (mode == CardMode.DAY) 0 else 1,
        onSelect = { index -> onSelect(if (index == 0) CardMode.DAY else CardMode.WEEK) },
    )
}

/** 两段式滑动开关：选中白色胶囊在两端之间滑动，今天/本周和网页/App 共用。 */
@Composable
internal fun SlidingToggle(
    firstLabel: String,
    secondLabel: String,
    selectedIndex: Int,
    onSelect: (Int) -> Unit,
) {
    val density = LocalDensity.current
    var firstX by remember { mutableStateOf(0f) }
    var firstY by remember { mutableStateOf(0f) }
    var firstWidth by remember { mutableStateOf(0f) }
    var firstHeight by remember { mutableStateOf(0f) }
    var secondX by remember { mutableStateOf(0f) }
    var secondY by remember { mutableStateOf(0f) }
    var secondWidth by remember { mutableStateOf(0f) }
    var secondHeight by remember { mutableStateOf(0f) }
    val targetX = if (selectedIndex == 0) firstX else secondX
    val targetY = if (selectedIndex == 0) firstY else secondY
    val targetWidth = if (selectedIndex == 0) firstWidth else secondWidth
    val targetHeight = if (selectedIndex == 0) firstHeight else secondHeight
    val indicatorX = remember { Animatable(0f) }
    val indicatorY = remember { Animatable(0f) }
    val indicatorWidth = remember { Animatable(0f) }
    val indicatorHeight = remember { Animatable(0f) }
    var initialized by remember { mutableStateOf(false) }
    val springSpec = spring<Float>(dampingRatio = 0.72f, stiffness = 500f)
    LaunchedEffect(selectedIndex, targetX, targetY, targetWidth, targetHeight) {
        if (targetWidth <= 0f || targetHeight <= 0f) return@LaunchedEffect
        if (!initialized) {
            // 首次排版直接落到目标位置，避免切换页面时自己播放一次滑入动画。
            indicatorX.snapTo(targetX)
            indicatorY.snapTo(targetY)
            indicatorWidth.snapTo(targetWidth)
            indicatorHeight.snapTo(targetHeight)
            initialized = true
        } else {
            coroutineScope {
                launch { indicatorX.animateTo(targetX, springSpec) }
                launch { indicatorY.animateTo(targetY, springSpec) }
                launch { indicatorWidth.animateTo(targetWidth, springSpec) }
                launch { indicatorHeight.animateTo(targetHeight, springSpec) }
            }
        }
    }
    Surface(
        color = MaterialTheme.colorScheme.surfaceVariant,
        shape = RoundedCornerShape(50),
    ) {
        Box(Modifier.padding(2.dp)) {
            if (initialized) {
                Box(
                    Modifier
                        .offset(
                            x = with(density) { indicatorX.value.toDp() },
                            y = with(density) { indicatorY.value.toDp() },
                        )
                        .width(with(density) { indicatorWidth.value.toDp() })
                        .height(with(density) { indicatorHeight.value.toDp() })
                        .clip(RoundedCornerShape(50))
                        .background(MaterialTheme.colorScheme.surface),
                )
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                ModePill(
                    text = firstLabel,
                    selected = selectedIndex == 0,
                    onClick = { onSelect(0) },
                    onPosition = { x, y, width, height ->
                        firstX = x
                        firstY = y
                        firstWidth = width
                        firstHeight = height
                    },
                )
                ModePill(
                    text = secondLabel,
                    selected = selectedIndex == 1,
                    onClick = { onSelect(1) },
                    onPosition = { x, y, width, height ->
                        secondX = x
                        secondY = y
                        secondWidth = width
                        secondHeight = height
                    },
                )
            }
        }
    }
}

@Composable
internal fun ModePill(
    text: String,
    selected: Boolean,
    onPosition: (Float, Float, Float, Float) -> Unit = { _, _, _, _ -> },
    onClick: () -> Unit,
) {
    val interactionSource = remember { MutableInteractionSource() }
    val view = LocalView.current
    val content by animateColorAsState(
        targetValue = if (selected) MaterialTheme.colorScheme.onSurface
        else MaterialTheme.colorScheme.onSurfaceVariant,
        animationSpec = tween(120),
        label = "mode-pill-fg",
    )
    Box(
        modifier = Modifier
            .onGloballyPositioned { coordinates ->
                val position = coordinates.positionInParent()
                onPosition(
                    position.x,
                    position.y,
                    coordinates.size.width.toFloat(),
                    coordinates.size.height.toFloat(),
                )
            }
            .clip(RoundedCornerShape(50))
            .clickable(
                interactionSource = interactionSource,
                indication = null,
            ) {
                view.performHapticFeedback(android.view.HapticFeedbackConstants.VIRTUAL_KEY)
                onClick()
            }
            .padding(horizontal = 12.dp, vertical = 4.dp),
    ) {
        Text(
            text = text,
            fontSize = 11.sp,
            fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
            color = content,
        )
    }
}

@Composable
private fun WeekGridView(
    columns: List<DayColumn>,
    isHoliday: Boolean,
    onSelectCourse: (SelectedCourse) -> Unit,
) {
    val context = LocalContext.current
    val dark = isSystemInDarkTheme()
    val measure = remember(context) { TextMeasure(context) }
    val cells = remember(columns) { columns.map(::cellsOf) }
    if (cells.all { it.isEmpty() }) {
        Text(if (isHoliday) "假期中" else "这周没有课", fontSize = 14.sp)
        return
    }
    BoxWithConstraints(modifier = Modifier.fillMaxWidth()) {
        val totalW = maxWidth.value
        val axisSp = 9f
        val unitSp = 8f
        val endSp = 8.5f
        val axisW = maxOf(
            measure.width("00:00", axisSp, true),
            measure.width("第10节", unitSp, false),
            measure.width("–00:00", endSp, false),
        ) + 4f
        val labelH = measure.height("00:00", axisSp, true, 100f, 1)
        val endH = measure.height("–00:00", endSp, false, 100f, 1)
        val colGap = 3f
        val usableW = (totalW - axisW).coerceAtLeast(1f)
        val colW = if (columns.isEmpty()) 0f else usableW / columns.size - colGap
        val cellInnerW = (colW - CHIP_PAD_H - 3f).coerceAtLeast(20f)
        val gridH = 380f
        val layout = remember(cells, totalW) {
            layoutGrid(cells, gridH, cellInnerW, measure, labelH, endH)
        }
        if (layout == null) {
            Text(if (isHoliday) "假期中" else "这周没有课", fontSize = 14.sp)
            return@BoxWithConstraints
        }
        Column(modifier = Modifier.fillMaxWidth()) {
            Row(modifier = Modifier.fillMaxWidth().height(34.dp), verticalAlignment = Alignment.CenterVertically) {
                Spacer(Modifier.width(axisW.dp))
                for (c in columns) {
                    Column(
                        modifier = Modifier.weight(1f).padding(start = colGap.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                    ) {
                        Text(
                            text = "周${WEEKDAY[c.day.weekday]}",
                            fontSize = 11.sp,
                            lineHeight = 13.sp,
                            fontWeight = if (c.isToday) FontWeight.Bold else FontWeight.Normal,
                            color = if (c.isToday) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        Text(
                            text = shortDate(c.day.date),
                            fontSize = 9.sp,
                            lineHeight = 11.sp,
                            color = if (c.isToday) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }
            Spacer(Modifier.height(4.dp))
            Row(modifier = Modifier.fillMaxWidth().height(gridH.dp)) {
                WeekAxis(layout, axisW, gridH, labelH, endH, measure, axisSp, unitSp, endSp)
                columns.forEachIndexed { colIndex, column ->
                    val colSpans = layout.spans.filter { it.col == colIndex }
                    Box(
                        modifier = Modifier
                            .weight(1f)
                            .fillMaxHeight()
                            .padding(start = colGap.dp),
                    ) {
                        for (span in colSpans) {
                            val top = layout.bounds[span.a]
                            val h = (layout.bounds[span.b] - top - CHIP_GAP).coerceAtLeast(2f)
                            Box(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .offset(y = top.dp)
                                    .height(h.dp),
                            ) {
                                WeekCellCard(
                                    cell = span.cell,
                                    height = h,
                                    innerW = cellInnerW,
                                    fs = layout.fontScale,
                                    onAxis = span.onAxis,
                                    measure = measure,
                                    dark = dark,
                                    onClick = {
                                        val overlapping = column.slots.filter {
                                            it.lesson.startMinutes < span.cell.end && it.lesson.endMinutes > span.cell.start
                                        }
                                        onSelectCourse(
                                            SelectedCourse(
                                                day = column.day,
                                                slots = overlapping,
                                                cancelled = span.cell.cancelled,
                                            ),
                                        )
                                    },
                                )
                            }
                        }
                    }
                }
            }
        }
    }
}

private data class ComposeAxisItem(
    val top: Float,
    val height: Float,
    val text: String,
    val start: Boolean,
    val unit: Int?,
)

@Composable
private fun WeekAxis(
    layout: GridLayout,
    axisW: Float,
    gridH: Float,
    labelH: Float,
    endH: Float,
    measure: TextMeasure,
    axisSp: Float,
    unitSp: Float,
    endSp: Float,
) {
    val unitH = measure.height("第1节", unitSp, false, 100f, 1)
    val items = (layout.starts.map { (k, l) -> ComposeAxisItem(layout.bounds[k], labelH, l.time, true, l.unit) } +
        layout.ends.map { (k, t) -> ComposeAxisItem(layout.bounds[k] - endH, endH, "–$t", false, null) })
        .sortedWith(compareBy({ it.top }, { it.start }))
    Box(modifier = Modifier.width(axisW.dp).height(gridH.dp)) {
        items.forEachIndexed { i, item ->
            val next = items.getOrNull(i + 1)?.top ?: gridH
            val showUnit = item.start && item.unit != null && next - item.top >= labelH + unitH
            Column(modifier = Modifier.offset(y = item.top.coerceAtLeast(0f).dp)) {
                if (item.start) {
                    Text(
                        text = item.text,
                        fontSize = axisSp.sp,
                        lineHeight = (axisSp + 1f).sp,
                        fontWeight = FontWeight.Bold,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    if (showUnit) {
                        Text(
                            text = "第${item.unit}节",
                            fontSize = unitSp.sp,
                            lineHeight = (unitSp + 1f).sp,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                } else {
                    Text(
                        text = item.text,
                        fontSize = endSp.sp,
                        lineHeight = (endSp + 1f).sp,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }
}

@Composable
private fun WeekCellCard(
    cell: Cell,
    height: Float,
    innerW: Float,
    fs: Float,
    onAxis: Boolean,
    measure: TextMeasure,
    dark: Boolean,
    onClick: () -> Unit,
) {
    val dim = cell.cancelled != null || cell.state == State.PAST
    val live = cell.state == State.LIVE
    val baseColor = parseColor(cell.color)
    val bg = when {
        live -> baseColor
        dim -> MaterialTheme.colorScheme.surfaceVariant
        dark -> mix(baseColor, Color(0xFF1A1A1D), 0.7f)
        else -> mix(baseColor, Color.White, 0.84f)
    }
    val barColor = if (dim) MaterialTheme.colorScheme.outline else baseColor
    // App 周视图不再在卡片里重复写起止时间，时间轴和详情弹窗已经足够。
    val pieces = bestPieces(cell, height, measure, innerW, fs, onAxis)
        .filter { it.kind != Kind.TIME }

    Row(
        modifier = Modifier
            .fillMaxSize()
            .clip(RoundedCornerShape(6.dp))
            .background(bg)
            .clickable(onClick = onClick),
    ) {
        Box(
            modifier = Modifier
                .width(3.dp)
                .fillMaxHeight()
                .background(barColor),
        )
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(horizontal = 4.dp, vertical = 2.dp),
        ) {
            var left = height - CHIP_PAD_V
            for (p in pieces) {
                val sp = p.size(fs)
                val bold = pieceBold(p.kind)
                var lines = p.maxLines
                while (lines > 1 && measure.height(p.text, sp, bold, innerW, lines) > left) lines--
                val h = measure.height(p.text, sp, bold, innerW, lines)
                if (h > left + 0.5f) break
                left -= h
                val textColor = when {
                    live -> Color.White
                    cell.cancelled != null || p.kind == Kind.WAS -> WarmOrange
                    dim -> MaterialTheme.colorScheme.onSurfaceVariant
                    cell.lesson?.changed == true && (p.kind == Kind.NAME || p.kind == Kind.TIME) -> WarmOrange
                    p.kind == Kind.NAME || p.kind == Kind.TIME ->
                        if (dark) mix(baseColor, Color.White, 0.55f) else mix(baseColor, Color.Black, 0.4f)
                    else -> MaterialTheme.colorScheme.onSurfaceVariant
                }
                Text(
                    text = p.text,
                    fontSize = sp.sp,
                    lineHeight = (sp * 1.18f).sp,
                    letterSpacing = if (p.kind == Kind.TIME) (-0.3).sp else 0.sp,
                    fontWeight = if (bold) FontWeight.Bold else FontWeight.Normal,
                    maxLines = lines,
                    overflow = TextOverflow.Ellipsis,
                    color = textColor,
                )
            }
        }
    }
}

@Composable
private fun CourseDetailDialog(detail: SelectedCourse, onDismiss: () -> Unit) {
    PopupCard(onDismissRequest = onDismiss) {
        val dateText = "周${WEEKDAY[detail.day.weekday]} ${shortDate(detail.day.date)}" +
            (detail.day.week?.let { " · 第 $it 周" } ?: "")
        Text(dateText, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.height(8.dp))
        detail.slots.forEachIndexed { idx, slot ->
            val lesson = slot.lesson
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    Modifier
                        .size(10.dp)
                        .clip(CircleShape)
                        .background(parseColor(lesson.color)),
                )
                Spacer(Modifier.width(8.dp))
                Text(
                    lesson.name,
                    fontSize = 17.sp,
                    fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.weight(1f),
                )
                if (lesson.changed) {
                    Spacer(Modifier.width(6.dp))
                    Tag("调课", WarmOrange)
                }
            }
            Spacer(Modifier.height(8.dp))
            DetailLine(
                "时间",
                "${lesson.start}–${lesson.end}" +
                    if (lesson.units.isNotEmpty()) "（第 ${lesson.units} 节）" else "",
            )
            DetailLine("地点", lesson.room.ifEmpty { "地点待定" })
            if (lesson.was != null) {
                DetailLine("原安排", lesson.was, highlight = true)
            }
            if (lesson.teachers.isNotEmpty()) {
                DetailLine("教师", lesson.teachers)
            }
            if (idx != detail.slots.lastIndex) {
                HorizontalDivider(
                    modifier = Modifier.padding(vertical = 10.dp),
                    color = MaterialTheme.colorScheme.outline.copy(alpha = 0.7f),
                )
            }
        }
        detail.cancelled?.let { c ->
            if (detail.slots.isNotEmpty()) {
                HorizontalDivider(
                    modifier = Modifier.padding(vertical = 10.dp),
                    color = MaterialTheme.colorScheme.outline.copy(alpha = 0.7f),
                )
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(c.name, fontSize = 17.sp, fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                Tag("停课", WarmOrange)
            }
            Spacer(Modifier.height(8.dp))
            DetailLine("时间", "${c.start}–${c.end}")
            if (c.room.isNotEmpty()) DetailLine("原教室", c.room)
        }
        Spacer(Modifier.height(10.dp))
        Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
            TextButton(
                onClick = onDismiss,
                colors = ButtonDefaults.textButtonColors(contentColor = MaterialTheme.colorScheme.primary),
            ) { Text("知道了") }
        }
    }
}

@Composable
private fun DetailLine(label: String, value: String, highlight: Boolean = false) {
    Row(modifier = Modifier.fillMaxWidth().padding(vertical = 2.dp)) {
        Text(
            label,
            fontSize = 13.sp,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.width(56.dp),
        )
        Text(
            value,
            fontSize = 13.sp,
            color = if (highlight) WarmOrange else MaterialTheme.colorScheme.onSurface,
            modifier = Modifier.weight(1f),
        )
    }
}

@Composable
private fun LessonRow(slot: Slot, onClick: (() -> Unit)? = null) {
    val lesson = slot.lesson
    val timeWithUnits = if (lesson.units.isNotEmpty()) {
        "${lesson.start}–${lesson.end} (${lesson.units}节)"
    } else {
        "${lesson.start}–${lesson.end}"
    }
    val meta = listOfNotNull(
        timeWithUnits,
        roomText(lesson.room, lesson.was),
        lesson.teachers.takeIf { it.isNotEmpty() },
    ).joinToString(" · ")
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .height(IntrinsicSize.Max)
            .clip(RoundedCornerShape(10.dp))
            .let { if (onClick != null) it.clickable(onClick = onClick) else it }
            .alpha(if (slot.state == State.PAST) 0.45f else 1f),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            Modifier
                .width(3.dp)
                .fillMaxHeight()
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
                meta,
                fontSize = 12.sp,
                color = if (lesson.changed) WarmOrange else MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
        }
        Spacer(Modifier.width(8.dp))
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

