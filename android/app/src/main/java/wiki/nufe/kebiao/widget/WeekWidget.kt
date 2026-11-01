package wiki.nufe.kebiao.widget

import android.content.Context
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
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
import androidx.glance.unit.ColorProvider
import wiki.nufe.kebiao.data.DayColumn
import wiki.nufe.kebiao.data.Now
import wiki.nufe.kebiao.data.State
import wiki.nufe.kebiao.data.WEEKDAY
import wiki.nufe.kebiao.data.headerNote
import wiki.nufe.kebiao.data.shortDate
import wiki.nufe.kebiao.data.weekColumns
import wiki.nufe.kebiao.data.weekToShow

/**
 * 本周课表：纵轴按真实时间，按内容排（见 WeekLayout.kt）。小组件可以随意拉伸，版面全按当前尺寸算：
 * 拉高了字变大、写上教师，拉矮了逐级少写；拉窄到一列不足 40dp 时只画从今天起放得下的几天。
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

private const val COL_GAP = 3f
private const val DAY_ROW_H = 16f
private const val HEADER_H = 26f
private const val MIN_COL_W = 40f   // 再窄连两个字都放不下：少画几天
private const val AXIS_SP = 9f

/**
 * 放不下的时候先省掉没课的日子（今天除外），还放不下就只画从今天（或这周第一个还没过去的日子）起的几天。
 */
private fun visibleColumns(columns: List<DayColumn>, maxCols: Int, today: String): List<DayColumn> {
    if (columns.size <= maxCols) return columns
    val busy = columns.filter { it.isToday || it.slots.isNotEmpty() || it.cancelled.isNotEmpty() }
    if (busy.size <= maxCols) return busy
    val from = busy.indexOfFirst { it.day.date >= today }.let { if (it < 0) 0 else it }
    val start = from.coerceAtMost(busy.size - maxCols)
    return busy.subList(start, start + maxCols)
}

@Composable
private fun WeekContent(context: Context, data: WidgetData) {
    val snapshot = data.snapshot!!
    val now = Now.current()
    val (title, block) = weekToShow(snapshot.payload, now)
    val size = LocalSize.current
    val m = TextMeasure(context)
    val axisW = m.width("00:00", AXIS_SP, false) + 4f
    val labelH = m.height("00:00", AXIS_SP, false, 100f, 1)
    val usableW = size.width.value - PADDING.value * 2 - axisW
    val maxCols = maxOf(1, (usableW / (MIN_COL_W + COL_GAP)).toInt())
    val columns = visibleColumns(weekColumns(block, now), maxCols, now.date)
    val range = if (columns.isNotEmpty()) {
        val first = shortDate(columns.first().day.date)
        val last = shortDate(columns.last().day.date)
        if (first == last) first else "$first–$last"
    } else ""
    val changed = columns.any { c -> c.cancelled.isNotEmpty() || c.slots.any { it.lesson.changed } }
    val firstDay = columns.firstOrNull()?.day
    val note = headerNote(snapshot, firstDay, now, compact = size.width.value < 220, changed = changed)
    WidgetRoot(onClick = openDay(context, data, firstDay, view = "week")) {
        Column(modifier = GlanceModifier.fillMaxSize()) {
            Header(title, range, note, compact = size.width.value < 180)
            Spacer(GlanceModifier.height(4.dp))
            val cells = columns.map(::cellsOf)
            val gridH = size.height.value - PADDING.value * 2 - HEADER_H - DAY_ROW_H
            val colW = if (columns.isEmpty()) 0f else usableW / columns.size - COL_GAP
            val layout = if (gridH > 0) layoutGrid(cells, gridH, colW - CHIP_PAD_H, m, labelH) else null
            if (layout == null) {
                Label(if (block?.week == null) "假期中" else "这周没有课", size = 14.sp, bold = true)
                return@Column
            }
            Row(modifier = GlanceModifier.fillMaxWidth().height(DAY_ROW_H.dp)) {
                Spacer(GlanceModifier.width(axisW.dp))
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
                Axis(layout, axisW, gridH, labelH)
                columns.forEachIndexed { i, c ->
                    DayCells(context, data, c, layout, i, colW, m)
                }
            }
        }
    }
}

/** 左边时间轴：每张卡片顶边旁标它的开始时间（排版时已经保证标签之间放得下）。 */
@Composable
private fun Axis(layout: GridLayout, axisW: Float, gridH: Float, labelH: Float) {
    Column(modifier = GlanceModifier.width(axisW.dp).height(gridH.dp)) {
        var cursor = 0f
        for (k in layout.labeled.sorted().take(9)) { // Column 最多 10 个子元素
            val y = layout.bounds[k]
            val t = layout.minutes[k]
            if (t < 0) continue
            Label("%02d:%02d".format(t / 60, t % 60), size = AXIS_SP.sp, color = GlanceTheme.colors.onSurfaceVariant,
                modifier = GlanceModifier.padding(top = (y - cursor).coerceAtLeast(0f).dp))
            cursor = maxOf(cursor, y) + labelH
        }
    }
}

@Composable
private fun DayCells(context: Context, data: WidgetData, column: DayColumn, layout: GridLayout, col: Int, width: Float,
                     m: TextMeasure) {
    Column(modifier = GlanceModifier.width((width + COL_GAP).dp).fillMaxHeight().padding(start = COL_GAP.dp)
        .clickable(openDay(context, data, column.day))) {
        var cursor = 0f
        for (span in layout.spans.filter { it.col == col }.take(10)) {
            val top = layout.bounds[span.a]
            val h = (layout.bounds[span.b] - top - CHIP_GAP).coerceAtLeast(2f)
            // 用上内边距代替 Spacer：每格只占一个子元素，一天五六节课也不会超出 RemoteViews 的子元素上限
            Box(modifier = GlanceModifier.padding(top = (top - cursor).coerceAtLeast(0f).dp)) {
                CellChip(span.cell, h, width - CHIP_PAD_H, layout.fontScale, span.a in layout.labeled, m)
            }
            cursor = top + h + CHIP_GAP
        }
    }
}

@Composable
private fun CellChip(cell: Cell, height: Float, innerW: Float, fs: Float, labeled: Boolean, m: TextMeasure) {
    val dim = cell.cancelled != null || cell.state == State.PAST
    val live = cell.state == State.LIVE
    Chip(cell.color, height.dp, strong = live, dim = dim) {
        if (height < CHIP_PAD_V + 8f) return@Chip
        val pieces = bestPieces(cell, height, m, innerW, fs, labeled)
        Column {
            var left = height - CHIP_PAD_V
            for (p in pieces) {
                val sp = pieceSp(p.kind) * fs
                val bold = pieceBold(p.kind)
                // 最省的一档也放不下时（很矮的格子），能写几行写几行，别让半行字被切掉
                var lines = p.maxLines
                while (lines > 1 && m.height(p.text, sp, bold, innerW, lines) > left) lines--
                val h = m.height(p.text, sp, bold, innerW, lines)
                if (h > left + 0.5f) break
                left -= h
                Label(p.text, size = sp.sp, bold = bold, lines = lines, color = pieceColor(p.kind, cell, live, dim))
            }
        }
    }
}

@Composable
private fun pieceColor(kind: Kind, cell: Cell, live: Boolean, dim: Boolean): ColorProvider = when {
    live -> solid(Color.White)
    cell.cancelled != null -> solid(WARN)
    kind == Kind.WAS -> solid(WARN)
    dim -> GlanceTheme.colors.onSurfaceVariant
    kind == Kind.NAME || kind == Kind.TIME -> chipText(cell.color)
    else -> GlanceTheme.colors.onSurfaceVariant
}
