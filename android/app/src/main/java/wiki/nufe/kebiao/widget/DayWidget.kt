package wiki.nufe.kebiao.widget

import android.content.Context
import androidx.compose.runtime.Composable
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
import wiki.nufe.kebiao.data.Slot
import wiki.nufe.kebiao.data.State
import wiki.nufe.kebiao.data.WEEKDAY
import wiki.nufe.kebiao.data.buildPlan
import wiki.nufe.kebiao.data.headerNote
import wiki.nufe.kebiao.data.lessonMs
import wiki.nufe.kebiao.data.shortDate

/** 今天：正在上的课倒计时到下课，下一节 1 小时内倒计时到上课；当天课上完预告下次上课。 */
class TodayWidget : DayWidget(tomorrow = false)

/** 明天：明天的全部课；明天没课时预告下次上课。 */
class TomorrowWidget : DayWidget(tomorrow = true)

class TodayReceiver : GlanceAppWidgetReceiver() {
    override val glanceAppWidget: GlanceAppWidget = TodayWidget()
}

class TomorrowReceiver : GlanceAppWidgetReceiver() {
    override val glanceAppWidget: GlanceAppWidget = TomorrowWidget()
}

private const val COUNTDOWN_WITHIN_MS = 60 * 60_000L
private const val ROW_H = 44      // 列表一行（课名 + 时间教室）的高度，含间距；汉字行高约 1.4 倍字号
private const val HEADER_H = 26

open class DayWidget(private val tomorrow: Boolean) : GlanceAppWidget() {
    override val sizeMode = SizeMode.Exact

    override suspend fun provideGlance(context: Context, id: GlanceId) {
        val data = loadData(context)
        provideContent {
            GlanceTheme {
                val message = placeholderMessage(data)
                if (message != null) Placeholder(context, message) else DayContent(context, data, tomorrow)
            }
        }
    }
}

@Composable
private fun DayContent(context: Context, data: WidgetData, tomorrow: Boolean) {
    val snapshot = data.snapshot!!
    val now = Now.current()
    val plan = buildPlan(snapshot.payload, tomorrow, now)
    val size = LocalSize.current
    val small = size.width < 200.dp
    val changed = plan.slots.any { it.lesson.changed } || plan.cancelled.isNotEmpty()
    val note = headerNote(snapshot, plan.day, now, compact = small, changed = changed)
    val subtitle = plan.day?.let { "周${WEEKDAY[it.weekday]} ${shortDate(it.date)}" }.orEmpty()
    WidgetRoot(onClick = openDay(context, data, plan.day)) {
        Column(modifier = GlanceModifier.fillMaxSize()) {
            Header(plan.title, if (small) "" else subtitle, note, compact = small)
            Spacer(GlanceModifier.height(4.dp))
            when {
                plan.slots.isEmpty() -> EmptyState(context, data, plan, small)
                small -> Focus(context, plan)
                else -> LessonList(context, plan.slots, plan.cancelled, plan.day?.date, size.height.value - PADDING.value * 2 - HEADER_H)
            }
        }
    }
}

private const val MORE_H = 16   // “还有 N 节”一行

/** 小号：只画最要紧的一节课——正在上的，或下一节。 */
@Composable
private fun ColumnScope.Focus(context: Context, plan: DayPlan) {
    val slot = plan.slots.first()
    val lesson = slot.lesson
    val date = plan.day?.date
    Label(lesson.name, size = 15.sp, bold = true, lines = 2)
    Spacer(GlanceModifier.height(2.dp))
    Label("${lesson.start}–${lesson.end}", size = 12.sp, color = GlanceTheme.colors.onSurfaceVariant)
    Label(roomText(lesson.room, lesson.was), size = 12.sp, color = GlanceTheme.colors.onSurfaceVariant)
    Spacer(GlanceModifier.defaultWeight())
    StateLine(context, slot, date)
    val more = plan.slots.size - 1
    if (more > 0 && slot.state != State.LIVE) {
        Label("之后还有 $more 节", size = 11.sp, color = GlanceTheme.colors.onSurfaceVariant)
    } else if (more > 0) {
        Label("下一节 ${plan.slots[1].lesson.start} ${plan.slots[1].lesson.name}", size = 11.sp,
            color = GlanceTheme.colors.onSurfaceVariant)
    }
}

/** “正在上课 · 还剩 mm:ss” / “下一节 · mm:ss 后”，一小时以外只写开始时间。 */
@Composable
private fun StateLine(context: Context, slot: Slot, date: String?) {
    if (date == null) return
    val lesson = slot.lesson
    Row(verticalAlignment = Alignment.CenterVertically) {
        when (slot.state) {
            State.LIVE -> {
                Label("上课中 · 还剩 ", size = 12.sp, color = solid(LIVE), bold = true)
                Countdown(context, lessonMs(date, lesson.end), LIVE)
            }
            State.NEXT -> {
                val start = lessonMs(date, lesson.start)
                if (start - Now.current().ms <= COUNTDOWN_WITHIN_MS) {
                    Label("距上课 ", size = 12.sp, color = GlanceTheme.colors.primary, bold = true)
                    Countdown(context, start, ACCENT)
                } else {
                    Label("下一节 · ${lesson.start} 开始", size = 12.sp, color = GlanceTheme.colors.primary, bold = true)
                }
            }
            else -> Unit
        }
    }
}

fun roomText(room: String, was: String? = null): String {
    val place = room.ifEmpty { "地点待定" }
    return if (was != null) "$place（$was）" else place
}

/** 中 / 大号：按高度能放几行就放几行，放不下的合成“还有 N 节”。 */
@Composable
private fun ColumnScope.LessonList(context: Context, slots: List<Slot>, cancelled: List<Cancelled>, date: String?, height: Float) {
    // 全放得下就全放；放不下时留出“还有 N 节”那一行
    val fitAll = ((height + 4) / ROW_H).toInt()
    val showCancelled = cancelled.isNotEmpty() && fitAll > slots.size
    val shown = if (slots.size <= fitAll) slots
    else slots.take(maxOf(1, ((height - MORE_H + 4) / ROW_H).toInt()))
    for (slot in shown) {
        LessonRow(context, slot, date)
        Spacer(GlanceModifier.height(4.dp))
    }
    val hidden = slots.size - shown.size
    if (hidden > 0) {
        val last = slots.last().lesson
        Label("还有 $hidden 节 · 到 ${last.end}", size = 11.sp, color = GlanceTheme.colors.onSurfaceVariant)
    }
    if (showCancelled) {
        Label("停课：" + cancelled.joinToString("、") { "${it.start} ${it.name}" }, size = 11.sp, color = solid(WARN))
    }
}

@Composable
private fun LessonRow(context: Context, slot: Slot, date: String?) {
    val lesson = slot.lesson
    val live = slot.state == State.LIVE
    Chip(lesson.color, height = 40.dp) {
        Row(modifier = GlanceModifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Column(modifier = GlanceModifier.defaultWeight()) {
                Label((if (lesson.changed) "调 · " else "") + lesson.name, size = 13.sp, bold = true,
                    color = chipText(lesson.color))
                val meta = listOf("${lesson.start}–${lesson.end}", roomText(lesson.room, lesson.was))
                Label(meta.joinToString("  "), size = 11.sp, color = GlanceTheme.colors.onSurfaceVariant)
            }
            if (date != null && (live || slot.state == State.NEXT)) {
                Spacer(GlanceModifier.width(4.dp))
                val target = lessonMs(date, if (live) lesson.end else lesson.start)
                if (live || target - Now.current().ms <= COUNTDOWN_WITHIN_MS) {
                    // 嵌入的 RemoteViews 不受 Glance 的权重约束，给它一个固定宽度，免得把课名挤没
                    Column(modifier = GlanceModifier.width(56.dp), horizontalAlignment = Alignment.End) {
                        Label(if (live) "还剩" else "距上课", size = 10.sp, color = GlanceTheme.colors.onSurfaceVariant)
                        Countdown(context, target, if (live) LIVE else ACCENT)
                    }
                } else {
                    Label("下一节", size = 11.sp, bold = true, color = GlanceTheme.colors.primary)
                }
            }
        }
    }
}

/** 没课：说明原因，能预告就把下次上课的头几节列出来。 */
@Composable
private fun ColumnScope.EmptyState(context: Context, data: WidgetData, plan: DayPlan, small: Boolean) {
    Label(plan.empty, size = if (small) 13.sp else 14.sp, bold = true, lines = 2)
    val preview = plan.preview ?: return
    Spacer(GlanceModifier.height(6.dp))
    Label(preview.title, size = 11.sp, color = GlanceTheme.colors.onSurfaceVariant)
    Spacer(GlanceModifier.height(3.dp))
    val size = LocalSize.current
    val room = ((size.height.value - PADDING.value * 2 - HEADER_H - 50) / (if (small) 16 else ROW_H)).toInt()
    val count = maxOf(1, room)
    Column(modifier = GlanceModifier.fillMaxWidth().clickable(openDay(context, data, preview.day))) {
        for (slot in preview.slots.take(count)) {
            if (small) {
                Label("${slot.lesson.start} ${slot.lesson.name}", size = 12.sp, color = chipText(slot.lesson.color))
            } else {
                LessonRow(context, slot, null)
                Spacer(GlanceModifier.height(4.dp))
            }
        }
        val more = preview.slots.size - count
        if (more > 0) Label("还有 $more 节", size = 11.sp, color = GlanceTheme.colors.onSurfaceVariant)
    }
}
