package wiki.nufe.kebiao.widget

import android.content.Context
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
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
import androidx.glance.background
import androidx.glance.layout.Alignment
import androidx.glance.layout.Box
import androidx.glance.layout.Column
import androidx.glance.layout.Row
import androidx.glance.layout.RowScope
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
 * 本周课表：和 iOS 的 week 模式一样按节次排、每节课写准确的起止时间，按内容排（见 WeekLayout.kt）。小组件可以随意拉伸，版面全按当前尺寸算：
 * 拉高了字变大、写上教师，拉矮了逐级少写；拉窄到一列不足 40dp 时只画从今天起放得下的几天。
 */
class WeekWidget : GlanceAppWidget() {
    override val sizeMode = SizeMode.Exact
    override val previewSizeMode = SizeMode.Responsive(
        setOf(DpSize(250.dp, 180.dp), DpSize(250.dp, 250.dp)),
    )

    override suspend fun provideGlance(context: Context, id: GlanceId) {
        provideContent {
            val data = rememberWidgetData(context)
            GlanceTheme(colors = WidgetColors) {
                val message = placeholderMessage(data)
                if (message != null) Placeholder(context, message) else WeekContent(context, data)
            }
        }
    }

    override suspend fun providePreview(context: Context, widgetCategory: Int) {
        provideContent { GlanceTheme(colors = WidgetColors) { WeekPreview(context) } }
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
private const val UNIT_SP = 8f      // 时间轴上“第几节”
private const val END_SP = 8.5f     // 时间轴上的下课时间“–09:45”

/** Android 15+ 小组件选择器里的生成预览：不读账号数据，画一张稳定的示意周视图。 */
@Composable
private fun WeekPreview(context: Context) {
    WidgetRoot(onClick = openApp(context)) {
        Column(GlanceModifier.fillMaxSize()) {
            Header("本周", "11/2–11/8", null, compact = true)
            Spacer(GlanceModifier.height(4.dp))
            Row(GlanceModifier.fillMaxWidth().height(16.dp)) {
                Spacer(GlanceModifier.width(26.dp))
                for (day in listOf("一", "二", "三", "四", "五")) {
                    Box(GlanceModifier.defaultWeight(), contentAlignment = Alignment.Center) {
                        Label(day, size = 9.sp, color = GlanceTheme.colors.onSurfaceVariant)
                    }
                }
            }
            Row(GlanceModifier.fillMaxWidth().defaultWeight()) {
                Column(GlanceModifier.width(26.dp).fillMaxHeight()) {
                    Label("08:00", size = 8.sp, color = GlanceTheme.colors.onSurfaceVariant)
                    Spacer(GlanceModifier.defaultWeight())
                    Label("14:00", size = 8.sp, color = GlanceTheme.colors.onSurfaceVariant)
                    Spacer(GlanceModifier.defaultWeight())
                    Label("20:00", size = 8.sp, color = GlanceTheme.colors.onSurfaceVariant)
                }
                PreviewColumn(listOf(PreviewBlock("高数", "#4b63e0"), null, PreviewBlock("英语", "#2f9e44")))
                PreviewColumn(listOf(null, PreviewBlock("会计", "#e8590c"), null, PreviewBlock("体育", "#495057")))
                PreviewColumn(listOf(PreviewBlock("线代", "#9c36b5"), null, PreviewBlock("实验", "#0f8b8d")))
                PreviewColumn(listOf(null, PreviewBlock("英语", "#2f9e44"), null, PreviewBlock("营销", "#c2255c")))
                PreviewColumn(listOf(PreviewBlock("经济学", "#e67700"), PreviewBlock("计算机", "#1971c2")))
            }
        }
    }
}

private data class PreviewBlock(val name: String, val color: String)

@Composable
private fun RowScope.PreviewColumn(blocks: List<PreviewBlock?>) {
    Column(GlanceModifier.defaultWeight().fillMaxHeight().padding(horizontal = 1.dp)) {
        blocks.forEach { block ->
            if (block == null) {
                Spacer(GlanceModifier.defaultWeight())
            } else {
                Chip(block.color, height = 26.dp) {
                    Label(block.name, size = 9.sp, bold = true, color = chipText(block.color))
                }
                Spacer(GlanceModifier.height(2.dp))
            }
        }
    }
}

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
    val widthScale = when {
        size.width.value >= 520f -> 1.25f
        size.width.value >= 360f -> 1.1f
        else -> 1f
    }
    val heightScale = when {
        size.height.value >= 400f -> 1.25f
        size.height.value >= 280f -> 1.1f
        else -> 1f
    }
    val uiScale = minOf(widthScale, heightScale)
    val axisSp = AXIS_SP * uiScale
    val unitSp = UNIT_SP * uiScale
    val endSp = END_SP * uiScale
    val axisW = maxOf(m.width("00:00", axisSp, true), m.width("第10节", unitSp, false),
        m.width("–00:00", endSp, false)) + 4f
    val labelH = m.height("00:00", axisSp, true, 100f, 1)
    val endH = m.height("–00:00", endSp, false, 100f, 1)
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
            val layout = if (gridH > 0) layoutGrid(cells, gridH, colW - CHIP_PAD_H, m, labelH, endH) else null
            if (layout == null) {
                Label(if (block?.week == null) "假期中" else "这周没有课", size = 14.sp, bold = true)
                return@Column
            }
            Row(modifier = GlanceModifier.fillMaxWidth().height(DAY_ROW_H.dp)) {
                Spacer(GlanceModifier.width(axisW.dp))
                // 列间距用左内边距：Glance 的 Row 最多 10 个子元素，周一到周日 + 时间轴已经 8 个
                for (c in columns) {
                    Box(modifier = GlanceModifier.defaultWeight().padding(start = COL_GAP.dp),
                        contentAlignment = Alignment.Center) {
                        Label("周${WEEKDAY[c.day.weekday]}", size = (11f * uiScale).sp, bold = c.isToday,
                            color = if (c.isToday) GlanceTheme.colors.primary else GlanceTheme.colors.onSurfaceVariant)
                    }
                }
            }
            Row(modifier = GlanceModifier.fillMaxWidth().height(gridH.dp)) {
                Axis(layout, axisW, gridH, labelH, endH, m, axisSp, unitSp, endSp)
                columns.forEachIndexed { i, c ->
                    DayCells(context, data, c, layout, i, colW, m, GlanceModifier.defaultWeight())
                }
            }
        }
    }
}

/** 时间轴上的一个标签：开始时间（粗体，在分界线下方，可带“第几节”）或下课时间（“–09:45”，在分界线上方）。 */
private data class AxisItem(val top: Float, val height: Float, val text: String, val start: Boolean, val unit: Int?)

/**
 * 左边时间轴：每节课顶边旁写它准确的开始时间，底边旁写下课时间“–09:45”——格子里放不下时间和教室两样时，
 * 卡片优先写教室，起止时间就看这里。按节次排时，到下一个标签之间放得下就在开始时间下面注“第几节”。
 * 排版时已经保证标签之间互不重叠。Glance 的 Column 最多 10 个子元素，标签按 9 个一组装进小 Column 再竖着叠。
 */
@Composable
private fun Axis(
    layout: GridLayout,
    axisW: Float,
    gridH: Float,
    labelH: Float,
    endH: Float,
    m: TextMeasure,
    axisSp: Float,
    unitSp: Float,
    endSp: Float,
) {
    val unitH = m.height("第1节", unitSp, false, 100f, 1)
    val items = (layout.starts.map { (k, l) -> AxisItem(layout.bounds[k], labelH, l.time, true, l.unit) } +
        layout.ends.map { (k, t) -> AxisItem(layout.bounds[k] - endH, endH, "–$t", false, null) })
        .sortedWith(compareBy({ it.top }, { it.start }))
    Column(modifier = GlanceModifier.width(axisW.dp).height(gridH.dp)) {
        var cursor = 0f
        for (group in items.withIndex().chunked(9).take(10)) {
            Column {
                for ((i, item) in group) {
                    val next = items.getOrNull(i + 1)?.top ?: gridH
                    val showUnit = item.start && item.unit != null && next - item.top >= labelH + unitH
                    Column(modifier = GlanceModifier.padding(top = (item.top - cursor).coerceAtLeast(0f).dp)) {
                        if (item.start) {
                            Label(item.text, size = axisSp.sp, bold = true, color = GlanceTheme.colors.onSurfaceVariant)
                            if (showUnit) Label("第${item.unit}节", size = unitSp.sp, color = GlanceTheme.colors.onSurfaceVariant)
                        } else {
                            Label(item.text, size = endSp.sp, color = GlanceTheme.colors.onSurfaceVariant)
                        }
                    }
                    cursor = maxOf(cursor, item.top) + item.height + if (showUnit) unitH else 0f
                }
            }
        }
    }
}

@Composable
private fun DayCells(
    context: Context,
    data: WidgetData,
    column: DayColumn,
    layout: GridLayout,
    col: Int,
    width: Float,
    m: TextMeasure,
    modifier: GlanceModifier,
) {
    // 每格叠在 Box 里、各自用上内边距从列顶算起，不接在上一格后面：桌面原地套用更新（reapply）偶尔会丢掉一格，
    // 接着排的话后面的课全跟着往上挪、落到别的节次上；各算各的，最多少一格，不会错位。
    // 每格只占一个子元素，一天最多十节课也不会超出 RemoteViews 的子元素上限。
    val spans = layout.spans.filter { it.col == col }.take(10)
    Box(modifier = modifier.fillMaxHeight().padding(start = COL_GAP.dp)
        .clickable(openDay(context, data, column.day))) {
        for (span in spans) {
            val top = layout.bounds[span.a]
            val h = (layout.bounds[span.b] - top - CHIP_GAP).coerceAtLeast(2f)
            Box(modifier = GlanceModifier.fillMaxWidth().padding(top = top.dp)) {
                CellChip(span.cell, h, width - CHIP_PAD_H, layout.fontScale, span.onAxis, m)
            }
        }
    }
}

@Composable
private fun CellChip(cell: Cell, height: Float, innerW: Float, fs: Float, onAxis: Boolean, m: TextMeasure) {
    val dim = cell.cancelled != null || cell.state == State.PAST
    val live = cell.state == State.LIVE
    // 按最省的一档（课名一行）都放不下上下边距的矮格子：去掉边距，课名缩到能放下为止（最小 7sp）
    val nameOnly = variants(cell, m, innerW, fs).last().first()
    val tight = piecesHeight(listOf(nameOnly), m, innerW, fs) > height + 0.5f
    Chip(cell.color, height.dp, strong = live, dim = dim, tight = tight) {
        if (tight) {
            var sp = nameOnly.size(fs)
            while (sp > 7f && m.height(nameOnly.text, sp, true, innerW, 1) > height) sp -= 0.5f
            if (m.height(nameOnly.text, sp, true, innerW, 1) <= height + 0.5f) {
                Label(nameOnly.text, size = sp.sp, bold = true, lines = 1, color = pieceColor(Kind.NAME, cell, live, dim))
            }
            return@Chip
        }
        val pieces = bestPieces(cell, height, m, innerW, fs, onAxis)
        Column {
            var left = height - CHIP_PAD_V
            for (p in pieces) {
                val sp = p.size(fs)
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
    // 和 iOS 一样：调过的课课名、时间标橙色
    cell.lesson?.changed == true && (kind == Kind.NAME || kind == Kind.TIME) -> solid(WARN)
    kind == Kind.NAME || kind == Kind.TIME -> chipText(cell.color)
    else -> GlanceTheme.colors.onSurfaceVariant
}
