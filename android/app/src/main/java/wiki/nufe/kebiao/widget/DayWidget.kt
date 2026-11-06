package wiki.nufe.kebiao.widget

import android.content.Context
import androidx.compose.runtime.Composable
import androidx.compose.ui.unit.DpSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.glance.GlanceId
import androidx.glance.GlanceModifier
import androidx.glance.GlanceTheme
import androidx.glance.LocalSize
import androidx.glance.action.clickable
import androidx.glance.appwidget.GlanceAppWidget
import androidx.glance.appwidget.GlanceAppWidgetReceiver
import androidx.glance.appwidget.SizeMode
import androidx.glance.appwidget.provideContent
import androidx.glance.layout.Alignment
import androidx.glance.layout.Column
import androidx.glance.layout.ColumnScope
import androidx.glance.layout.Row
import androidx.glance.layout.Spacer
import androidx.glance.layout.fillMaxSize
import androidx.glance.layout.fillMaxWidth
import androidx.glance.layout.height
import androidx.glance.layout.width
import wiki.nufe.kebiao.data.Cancelled
import wiki.nufe.kebiao.data.DayPlan
import wiki.nufe.kebiao.data.Now
import wiki.nufe.kebiao.data.Payload
import wiki.nufe.kebiao.data.Slot
import wiki.nufe.kebiao.data.State
import wiki.nufe.kebiao.data.WEEKDAY
import wiki.nufe.kebiao.data.buildTodayPlan
import wiki.nufe.kebiao.data.headerNote
import wiki.nufe.kebiao.data.isRestMessage
import wiki.nufe.kebiao.data.lessonMs
import wiki.nufe.kebiao.data.shortDate

/** 今天：正在上的课倒计时到下课，下一节 1 小时内倒计时到上课；今天全部上完后直接显示明天的完整课表。 */
class TodayWidget : DayWidget()

class TodayReceiver : GlanceAppWidgetReceiver() {
    override val glanceAppWidget: GlanceAppWidget = TodayWidget()
}

private const val ROW_H = 44      // 列表一行（课名 + 时间教室）的高度，含间距；汉字行高约 1.4 倍字号
private const val HEADER_H = 26

open class DayWidget : GlanceAppWidget() {
    override val sizeMode = SizeMode.Exact
    override val previewSizeMode = SizeMode.Responsive(
        setOf(DpSize(140.dp, 140.dp), DpSize(250.dp, 140.dp), DpSize(250.dp, 250.dp)),
    )

    override suspend fun provideGlance(context: Context, id: GlanceId) {
        provideContent {
            val data = rememberWidgetData(context)
            GlanceTheme(colors = WidgetColors) {
                val message = placeholderMessage(data)
                if (message != null) Placeholder(context, message) else DayContent(context, data)
            }
        }
    }

    override suspend fun providePreview(context: Context, widgetCategory: Int) {
        provideContent { GlanceTheme(colors = WidgetColors) { DayPreview(context) } }
    }
}

/** Android 15+ 小组件选择器里的生成预览：不读账号数据，画一张稳定的示意卡片。 */
@Composable
private fun DayPreview(context: Context) {
    val small = LocalSize.current.height < 180.dp
    WidgetRoot(onClick = openApp(context)) {
        Column(GlanceModifier.fillMaxSize()) {
            Header("今天", "周一 11/2", null, compact = true)
            Spacer(GlanceModifier.height(4.dp))
            PreviewLesson("算法设计与分析", "13:30–14:55 · 博观楼 JB404", "#9c36b5")
            Spacer(GlanceModifier.height(5.dp))
            PreviewLesson("企业营销道德", "18:30–20:50 · 德济楼 Z2-112", "#e8590c")
            if (!small) {
                Spacer(GlanceModifier.height(5.dp))
                PreviewLesson("大学英语", "10:00–11:35 · 德济楼 305", "#2f9e44")
            }
        }
    }
}

@Composable
private fun PreviewLesson(name: String, meta: String, color: String) {
    Chip(color, height = 40.dp) {
        Column(GlanceModifier.fillMaxWidth()) {
            Label(name, size = 12.sp, bold = true, color = chipText(color))
            Label(meta, size = 10.sp, color = GlanceTheme.colors.onSurfaceVariant)
        }
    }
}

@Composable
private fun DayContent(context: Context, data: WidgetData) {
    val snapshot = data.snapshot!!
    val now = Now.current()
    val plan = buildTodayPlan(snapshot.payload, now)
    val size = LocalSize.current
    val tablet = context.resources.configuration.smallestScreenWidthDp >= 600
    // 平板只把 1×1 当小号；1×2 / 2×2 用列表。手机沿用原来的宽度判断，避免改坏手机上正常的版面。
    val small = if (tablet) size.width < 220.dp && size.height < 220.dp else size.width < 200.dp
    val changed = plan.slots.any { it.lesson.changed } || plan.cancelled.isNotEmpty()
    val note = headerNote(snapshot, plan.day, now, compact = small, changed = changed)
    val dateText = plan.day?.let { "周${WEEKDAY[it.weekday]} ${shortDate(it.date)}" }.orEmpty()
    val count = plan.slots.size
    val subtitle = when {
        small && count > 0 -> "还有 $count 门"
        small -> ""
        count > 0 && dateText.isNotEmpty() -> "$dateText · 还有 $count 门"
        count > 0 -> "还有 $count 门"
        else -> dateText
    }
    WidgetRoot(onClick = openDay(context, data, plan.day)) {
        Column(modifier = GlanceModifier.fillMaxSize()) {
            Header(plan.title, subtitle, if (small) null else note, compact = small)
            Spacer(GlanceModifier.height(4.dp))
            when {
                plan.slots.isEmpty() -> EmptyState(context, data, plan, small)
                small -> Focus(context, plan)
                else -> LessonList(context, plan.slots, plan.cancelled, plan.day?.date, size.height.value - PADDING.value * 2 - HEADER_H)
            }
        }
    }
}

private const val MORE_H = 16f  // “还有 N 节”一行

/**
 * 小号（和 iOS 的小号一样）：只画最要紧的一节——正在上的，或下一节。
 * 从上到下：“上课中 / 下一节”（调过的课加“· 调课”）→ 大字起止时间 → 课名（课程色）→ 教室 → 倒计时 → “之后还有 N 节”或停课。
 */
@Composable
private fun ColumnScope.Focus(context: Context, plan: DayPlan) {
    val slot = plan.slots.first()
    val lesson = slot.lesson
    val date = plan.day?.date
    val height = LocalSize.current.height.value
    val tag = when (slot.state) {
        State.LIVE -> "上课中"
        State.NEXT -> "下一节"
        else -> ""
    }
    Row(verticalAlignment = Alignment.CenterVertically) {
        if (tag.isNotEmpty()) Label(tag, size = 11.sp, bold = true, color = if (slot.state == State.LIVE) solid(LIVE) else GlanceTheme.colors.primary)
        if (lesson.changed) Label(if (tag.isNotEmpty()) " · 调课" else "调课", size = 11.sp, bold = true, color = solid(WARN))
    }
    Label("${lesson.start}–${lesson.end}", size = 18.sp, bold = true)
    // 2×2 一般只有 140dp 上下：课名矮的时候只写一行
    Label(lesson.name, size = 14.sp, bold = true, lines = if (height >= 170) 2 else 1, color = chipText(lesson.color))
    Label(roomText(lesson.room, lesson.was), size = 11.sp,
        color = if (lesson.changed) solid(WARN) else GlanceTheme.colors.onSurfaceVariant)
    if (date != null) StateLine(context, slot, date)
    Spacer(GlanceModifier.defaultWeight())
    val more = plan.slots.size - 1
    when {
        plan.cancelled.isNotEmpty() -> Label(cancelledText(plan.cancelled), size = 10.sp, color = solid(WARN))
        more > 0 -> Label("之后还有 $more 节", size = 10.sp, color = GlanceTheme.colors.onSurfaceVariant)
    }
}

/** 倒计时（系统每秒自己走）：上课中“还剩 mm:ss”，下一节“距上课 h:mm:ss”——不论多久以后都显示。 */
@Composable
private fun StateLine(context: Context, slot: Slot, date: String) {
    val lesson = slot.lesson
    Row(verticalAlignment = Alignment.CenterVertically) {
        when (slot.state) {
            State.LIVE -> {
                Label("还剩 ", size = 11.sp, color = GlanceTheme.colors.onSurfaceVariant)
                Countdown(context, lessonMs(date, lesson.end), LIVE)
            }
            State.NEXT -> {
                Label("距上课 ", size = 11.sp, color = GlanceTheme.colors.onSurfaceVariant)
                Countdown(context, lessonMs(date, lesson.start), ACCENT)
            }
            else -> Unit
        }
    }
}

fun roomText(room: String, was: String? = null): String {
    val place = room.ifEmpty { "地点待定" }
    return if (was != null) "$place（$was）" else place
}

fun cancelledText(list: List<Cancelled>) = "停课：" + list.joinToString("、") { "${it.name} ${it.start}" }

/**
 * 中 / 大号：按高度能放几行就放几行，放不下的合成“还有 N 节”；有停课时停课比“还有 N 节”更要紧，挤掉最后一节。
 * 大号（拉高拉宽）再写上教师。
 */
@Composable
private fun ColumnScope.LessonList(context: Context, slots: List<Slot>, cancelled: List<Cancelled>, date: String?, height: Float) {
    val size = LocalSize.current
    val showTeacher = size.width.value >= 250 && size.height.value >= 250
    val fitAll = ((height + 4) / ROW_H).toInt()
    val reserve = (if (cancelled.isNotEmpty()) MORE_H else 0f)
    val shown = if (slots.size <= ((height - reserve + 4) / ROW_H).toInt()) slots
    else slots.take(maxOf(1, ((height - reserve - MORE_H + 4) / ROW_H).toInt()))
    for (slot in shown) {
        LessonRow(context, slot, date, showTeacher)
        Spacer(GlanceModifier.height(4.dp))
    }
    if (cancelled.isNotEmpty() && fitAll > 0) Label(cancelledText(cancelled), size = 11.sp, color = solid(WARN))
    val hidden = slots.size - shown.size
    if (hidden > 0) {
        val last = slots.last().lesson
        Label("还有 $hidden 节 · 到 ${last.end}", size = 11.sp, color = GlanceTheme.colors.onSurfaceVariant)
    }
}

/** 一行课：课名（调过的加“调”）、“起止 · 教室 · 教师”（调过的标橙色），右边“上课中 / 下一节”和倒计时。 */
@Composable
private fun LessonRow(context: Context, slot: Slot, date: String?, showTeacher: Boolean = false) {
    val lesson = slot.lesson
    val live = slot.state == State.LIVE
    Chip(lesson.color, height = 40.dp) {
        Row(modifier = GlanceModifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Column(modifier = GlanceModifier.defaultWeight()) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Label(lesson.name, size = 13.sp, bold = true, color = chipText(lesson.color))
                    if (lesson.changed) Label(" 调", size = 11.sp, bold = true, color = solid(WARN))
                }
                val meta = listOfNotNull("${lesson.start}–${lesson.end}", roomText(lesson.room, lesson.was),
                    lesson.teachers.takeIf { showTeacher && it.isNotEmpty() })
                Label(meta.joinToString(" · "), size = 11.sp,
                    color = if (lesson.changed) solid(WARN) else GlanceTheme.colors.onSurfaceVariant)
            }
            if (date != null && (live || slot.state == State.NEXT)) {
                Spacer(GlanceModifier.width(4.dp))
                // 嵌入的 RemoteViews 不受 Glance 的权重约束，给它一个固定宽度，免得把课名挤没
                Column(modifier = GlanceModifier.width(60.dp), horizontalAlignment = Alignment.End) {
                    Label(if (live) "上课中" else "下一节", size = 10.sp, bold = true,
                        color = if (live) solid(LIVE) else GlanceTheme.colors.primary)
                    Countdown(context, lessonMs(date, if (live) lesson.end else lesson.start), if (live) LIVE else ACCENT)
                }
            }
        }
    }
}

/**
 * 今天没有待上的课：先说清"上完了 / 本来没课 / 假期"，再给一行明天说明，
 * 最后用分隔线隔开——明天有课就直接列明天课表，没课就重点显示"下次上课"。
 */
@Composable
private fun ColumnScope.EmptyState(context: Context, data: WidgetData, plan: DayPlan, small: Boolean) {
    val headlineRest = isRestMessage(plan.empty)
    Label("⭐️ ${plan.empty}", size = if (small) 13.sp else 14.sp, bold = true, lines = 2,
        color = if (headlineRest) solid(REST) else GlanceTheme.colors.onSurface)
    plan.tomorrowHint?.let { hint ->
        Spacer(GlanceModifier.height(2.dp))
        Label(hint, size = 12.sp, lines = 1,
            color = if (isRestMessage(hint)) solid(REST) else GlanceTheme.colors.onSurfaceVariant)
    }
    if (plan.cancelled.isNotEmpty()) Label(cancelledText(plan.cancelled), size = 10.sp, color = solid(WARN), lines = 2)
    val preview = plan.preview ?: return
    val first = preview.slots.firstOrNull()?.lesson ?: return
    Spacer(GlanceModifier.height(6.dp))
    WidgetDivider()
    Spacer(GlanceModifier.height(6.dp))
    if (small) {
        if (plan.showPreviewTitle) {
            // 把第一节时间并进“下次上课”这行，允许换行，避免 2×2 下被截断。
            Label("${preview.title} ${first.start}", size = 11.sp, bold = true, color = GlanceTheme.colors.primary, lines = 2)
            Spacer(GlanceModifier.height(2.dp))
            Label(first.name, size = 13.sp, bold = true, lines = 2, color = chipText(first.color))
        } else {
            Label("${first.start} · ${first.name}", size = 13.sp, bold = true, lines = 2, color = chipText(first.color))
        }
        if (preview.slots.size > 1) Label("共 ${preview.slots.size} 节", size = 10.sp, color = GlanceTheme.colors.onSurfaceVariant)
        return
    }
    // showPreviewTitle：这是"下次上课"（以后某天），标题 + 第一节时间放大、用主色，明确不是明天。
    if (plan.showPreviewTitle) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Label(preview.title, size = 12.sp, bold = true, color = GlanceTheme.colors.primary)
            Spacer(GlanceModifier.width(8.dp))
            Label(first.start, size = 18.sp, bold = true, color = GlanceTheme.colors.primary)
        }
        Spacer(GlanceModifier.height(4.dp))
    }
    val size = LocalSize.current
    val room = ((size.height.value - PADDING.value * 2 - HEADER_H - 50) / ROW_H).toInt()
    val count = maxOf(1, room)
    Column(modifier = GlanceModifier.fillMaxWidth().clickable(openDay(context, data, preview.day))) {
        for (slot in preview.slots.take(count)) {
            LessonRow(context, slot, null)
            Spacer(GlanceModifier.height(4.dp))
        }
        val more = preview.slots.size - count
        if (more > 0) Label("还有 $more 节", size = 11.sp, color = GlanceTheme.colors.onSurfaceVariant)
    }
}
