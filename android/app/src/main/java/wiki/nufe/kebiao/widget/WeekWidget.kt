package wiki.nufe.kebiao.widget

import android.content.Context
import androidx.compose.runtime.Composable
import androidx.compose.ui.unit.Dp
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
import androidx.glance.layout.Box
import androidx.glance.layout.Column
import androidx.glance.layout.Row
import androidx.glance.layout.Spacer
import androidx.glance.layout.fillMaxHeight
import androidx.glance.layout.fillMaxSize
import androidx.glance.layout.fillMaxWidth
import androidx.glance.layout.height
import androidx.glance.layout.padding
import androidx.glance.layout.width
import kotlin.math.floor
import wiki.nufe.kebiao.data.DayColumn
import wiki.nufe.kebiao.data.Now
import wiki.nufe.kebiao.data.State
import wiki.nufe.kebiao.data.WEEKDAY
import wiki.nufe.kebiao.data.headerNote
import wiki.nufe.kebiao.data.shortDate
import wiki.nufe.kebiao.data.weekColumns
import wiki.nufe.kebiao.data.weekToShow

/**
 * 本周课表：纵轴按真实时间，课越长格子越高；一周里没人上课的时段（午休、傍晚）压成一道细缝，
 * 和 Scriptable 的 full 模式一样铺满整块小组件。
 */
class WeekWidget : GlanceAppWidget() {
    override val sizeMode = SizeMode.Exact

    override suspend fun provideGlance(context: Context, id: GlanceId) {
        val data = loadData(context)
        provideContent {
            GlanceTheme {
                val message = placeholderMessage(data)
                if (message != null) Placeholder(context, message) else WeekContent(context, data)
            }
        }
    }
}

class WeekReceiver : GlanceAppWidgetReceiver() {
    override val glanceAppWidget: GlanceAppWidget = WeekWidget()
}

private const val GAP = 6f          // 没课的时段压成这么高
private const val MERGE_MINUTES = 30 // 课间不到半小时算连着的一段（按比例画出课间）
private const val AXIS_W = 30f
private const val COL_GAP = 3f
private const val DAY_ROW_H = 16f
private const val LABEL_H = 11f

/** 一段连续有课的时间 [start, end]（分钟）。 */
private data class Segment(val start: Int, val end: Int)

/** 时间 → 纵坐标（dp）：有课的段按分钟等比例，段之间固定 GAP。 */
private class TimeScale(private val segments: List<Segment>, height: Float) {
    private val perMinute: Float
    private val offsets: List<Float>

    init {
        val busy = segments.sumOf { it.end - it.start }.coerceAtLeast(1)
        perMinute = ((height - GAP * (segments.size - 1).coerceAtLeast(0)) / busy).coerceAtLeast(0f)
        var y = 0f
        offsets = segments.map { s -> y.also { y += (s.end - s.start) * perMinute + GAP } }
    }

    fun y(minutes: Int): Float {
        for ((i, s) in segments.withIndex()) {
            if (minutes <= s.end) return offsets[i] + (minutes.coerceAtLeast(s.start) - s.start) * perMinute
        }
        return offsets.lastOrNull()?.let { it + (segments.last().end - segments.last().start) * perMinute } ?: 0f
    }
}

private fun segmentsOf(columns: List<DayColumn>): List<Segment> {
    val spans = columns.flatMap { c ->
        c.slots.map { it.lesson.startMinutes to it.lesson.endMinutes } + c.cancelled.map { it.startMinutes to it.endMinutes }
    }.sortedBy { it.first }
    val out = mutableListOf<Segment>()
    for ((s, e) in spans) {
        val last = out.lastOrNull()
        if (last != null && s <= last.end + MERGE_MINUTES) out[out.size - 1] = Segment(last.start, maxOf(last.end, e))
        else out += Segment(s, e)
    }
    return out
}

/** 一个格子：课（含状态）或停课。 */
private data class Cell(
    val start: Int, val end: Int, val name: String, val room: String, val color: String,
    val state: State?, val changed: Boolean, val cancelled: Boolean, var hidden: Int = 0,
)

private fun cellsOf(column: DayColumn): List<Cell> {
    val all = column.slots.map {
        Cell(it.lesson.startMinutes, it.lesson.endMinutes, it.lesson.name, it.lesson.room, it.lesson.color,
            it.state, it.lesson.changed, cancelled = false)
    } + column.cancelled.map { Cell(it.startMinutes, it.endMinutes, it.name, it.room, it.color, null, false, true) }
    // 真正时间重叠的撞课只画先开始的那节，记作 +N
    val out = mutableListOf<Cell>()
    for (cell in all.sortedWith(compareBy({ it.start }, { it.cancelled }))) {
        val last = out.lastOrNull()
        if (last != null && cell.start < last.end) last.hidden++ else out += cell
    }
    return out
}

@Composable
private fun WeekContent(context: Context, data: WidgetData) {
    val snapshot = data.snapshot!!
    val now = Now.current()
    val (title, block) = weekToShow(snapshot.payload, now)
    val columns = weekColumns(block, now)
    val size = LocalSize.current
    val days = block?.days.orEmpty()
    val range = if (days.isNotEmpty()) "${shortDate(days.first().date)}–${shortDate(days.last().date)}" else ""
    val changed = columns.any { c -> c.cancelled.isNotEmpty() || c.slots.any { it.lesson.changed } }
    val firstDay = columns.firstOrNull()?.day
    val note = headerNote(snapshot, firstDay, now, compact = false, changed = changed)
    WidgetRoot(onClick = openDay(context, data, firstDay, view = "week")) {
        Column(modifier = GlanceModifier.fillMaxSize()) {
            Header(title, range, note)
            Spacer(GlanceModifier.height(4.dp))
            val segments = segmentsOf(columns)
            if (columns.isEmpty() || segments.isEmpty()) {
                Label(if (block?.week == null) "假期中" else "这周没有课", size = 14.sp, bold = true)
                return@Column
            }
            val gridH = size.height.value - PADDING.value * 2 - 26 - DAY_ROW_H
            val colW = (size.width.value - PADDING.value * 2 - AXIS_W - COL_GAP * columns.size) / columns.size
            val scale = TimeScale(segments, gridH)
            Row(modifier = GlanceModifier.fillMaxWidth().height(DAY_ROW_H.dp)) {
                Spacer(GlanceModifier.width(AXIS_W.dp))
                // 列间距用左内边距：Glance 的 Row 最多 10 个子元素，周一到周日 + 时间轴已经 8 个
                for (c in columns) {
                    Box(modifier = GlanceModifier.width((colW + COL_GAP).dp).padding(start = COL_GAP.dp),
                        contentAlignment = Alignment.Center) {
                        Label("周${WEEKDAY[c.day.weekday]}", size = 11.sp, bold = c.isToday,
                            color = if (c.isToday) GlanceTheme.colors.primary else GlanceTheme.colors.onSurfaceVariant)
                    }
                }
            }
            Row(modifier = GlanceModifier.fillMaxWidth().height(gridH.dp)) {
                Axis(columns, scale, gridH)
                for (c in columns) DayCells(context, data, c, scale, colW.dp)
            }
        }
    }
}

/** 左边时间轴：每段的开始时间，加上段内各节课的开始时间（离上一个标签太近就省掉）。 */
@Composable
private fun Axis(columns: List<DayColumn>, scale: TimeScale, gridH: Float) {
    val starts = columns.flatMap { c -> c.slots.map { it.lesson.startMinutes } + c.cancelled.map { it.startMinutes } }
        .distinct().sorted()
    Column(modifier = GlanceModifier.width(AXIS_W.dp).height(gridH.dp)) {
        var cursor = 0f
        var count = 0
        for (m in starts) {
            val y = scale.y(m)
            if (y < cursor || y + LABEL_H > gridH || count >= 9) continue
            Label("%02d:%02d".format(m / 60, m % 60), size = 9.sp, color = GlanceTheme.colors.onSurfaceVariant,
                modifier = GlanceModifier.padding(top = (y - cursor).dp).height((y - cursor + LABEL_H).dp))
            cursor = y + LABEL_H
            count++
        }
    }
}

@Composable
private fun DayCells(context: Context, data: WidgetData, column: DayColumn, scale: TimeScale, width: Dp) {
    Column(modifier = GlanceModifier.width(width + COL_GAP.dp).fillMaxHeight().padding(start = COL_GAP.dp)
        .clickable(openDay(context, data, column.day))) {
        var cursor = 0f
        for (cell in cellsOf(column)) {
            val top = scale.y(cell.start)
            val bottom = scale.y(cell.end)
            val h = (bottom - top - 1f).coerceAtLeast(2f)
            // 用上内边距代替 Spacer：每格只占一个子元素，一天五六节课也不会超出 RemoteViews 的子元素上限
            Box(modifier = GlanceModifier.padding(top = (top - cursor).coerceAtLeast(0f).dp)) {
                CellChip(cell, h.dp)
            }
            cursor = top + h + 1f
        }
    }
}


@Composable
private fun CellChip(cell: Cell, height: Dp) {
    val lines = floor((height.value - 2f) / 11.5f).toInt() // 10sp 一行约 11.5dp；格子再矮也尽量写出课名
    val dim = cell.cancelled || cell.state == State.PAST
    val live = cell.state == State.LIVE
    Chip(cell.color, height, strong = live, dim = dim) {
        if (lines < 1) return@Chip
        val textColor = when {
            live -> solid(androidx.compose.ui.graphics.Color.White)
            dim -> GlanceTheme.colors.onSurfaceVariant
            else -> chipText(cell.color)
        }
        val prefix = when {
            cell.cancelled -> "停 "
            cell.changed -> "调 "
            else -> ""
        }
        val more = if (cell.hidden > 0) " +${cell.hidden}" else ""
        Column {
            Label(prefix + cell.name + more, size = 10.sp, bold = true, color = textColor,
                lines = if (lines >= 2 && cell.room.isNotEmpty()) lines - 1 else lines)
            if (lines >= 2 && cell.room.isNotEmpty()) Label(cell.room, size = 9.sp, color = textColor)
        }
    }
}
