package wiki.nufe.kebiao.widget

import android.content.Context
import android.graphics.Typeface
import android.os.Build
import android.text.Layout
import android.text.StaticLayout
import android.text.TextPaint
import android.text.TextUtils
import android.util.TypedValue
import wiki.nufe.kebiao.data.Cancelled
import wiki.nufe.kebiao.data.DayColumn
import wiki.nufe.kebiao.data.Lesson
import wiki.nufe.kebiao.data.State

/**
 * 本周小组件的版面：和 Scriptable 的 week 模式同一套规则，但字不用估——用画小组件的同一套系统字体
 * （StaticLayout，行高、汉字回退字体、折行都和 TextView 一致）量出来。
 *
 * 纵轴按节次（学校课表的样子）：用到的每一节一样高，课从起始节画到结束节，时间轴写每节课准确的开始时间和“第几节”；
 * 有课没写节次又补不上时改按真实时间。时间轴上还在每节课底边旁标下课时间，所以格子放不下时间和教室两样时，
 * 优先写教室，起止时间看时间轴；放得下时卡片上也写“10:00–12:15”（列窄就缩字或分两行）。
 * 每张卡片先保证写得下它这一档的内容，剩下的高度再按节数分。依次试“写全”到“只写课名”几档、每档几种字号，
 * 取第一个摆得下的；小组件可以随便拉伸，所以拉大了字跟着变大、连教师都写上，拉小了逐级少写。
 */

/** 一张格子：课（含状态）或停课。 */
class Cell(
    val lesson: Lesson?,
    val cancelled: Cancelled?,
    val start: Int,
    val end: Int,
    val color: String,
    val state: State?,
    var hidden: Int = 0,    // 时间重叠的撞课画不下，记作 +N
) {
    val name get() = lesson?.name ?: cancelled!!.name
}

fun cellsOf(column: DayColumn): List<Cell> {
    val all = column.slots.map {
        Cell(it.lesson, null, it.lesson.startMinutes, it.lesson.endMinutes, it.lesson.color, it.state)
    } + column.cancelled.map { Cell(null, it, it.startMinutes, it.endMinutes, it.color, null) }
    val out = mutableListOf<Cell>()
    for (cell in all.filter { it.end > it.start }.sortedWith(compareBy({ it.start }, { it.lesson == null }))) {
        val last = out.lastOrNull()
        if (last != null && cell.start < last.end) {
            if (cell.lesson != null) last.hidden++
        } else {
            out += cell
        }
    }
    return out
}

enum class Kind { NAME, TIME, ROOM, WAS, TEACHER }

/** 卡片里的一段字：maxLines 行以内，放不下截成“…”。 */
data class Piece(val text: String, val kind: Kind, val maxLines: Int, val sp: Float? = null)

/** 字号基准（sp），乘上版面选中的 fontScale。 */
fun pieceSp(kind: Kind) = when (kind) {
    Kind.NAME -> 11f
    Kind.TIME -> 9.5f
    Kind.ROOM, Kind.WAS -> 9.5f
    Kind.TEACHER -> 9f
}

fun pieceBold(kind: Kind) = kind == Kind.NAME || kind == Kind.TIME

const val CHIP_PAD_H = 11f  // 竖条 3 + 左右内边距各 4（见 Common.kt 的 Chip）
const val CHIP_PAD_V = 4f   // 上下内边距各 2
const val CHIP_SLACK = 1f
const val CHIP_GAP = 1f     // 上下两张卡片之间

/** 用系统字体量字：多高、能不能一行放下。结果缓存，同一次排版里同样的字只量一次。 */
class TextMeasure(context: Context) {
    private val metrics = context.resources.displayMetrics
    private val density = metrics.density
    private val paints = HashMap<Pair<Float, Boolean>, TextPaint>()
    private val cache = HashMap<String, Float>()

    private fun paint(sp: Float, bold: Boolean) = paints.getOrPut(sp to bold) {
        TextPaint(TextPaint.ANTI_ALIAS_FLAG).apply {
            // 和 TextView 一样按 sp 换算：跟随系统字体大小（含 Android 14 的非线性缩放）
            textSize = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_SP, sp, metrics)
            typeface = if (bold) Typeface.DEFAULT_BOLD else Typeface.DEFAULT
        }
    }

    fun width(text: String, sp: Float, bold: Boolean): Float = paint(sp, bold).measureText(text) / density

    fun fits(text: String, sp: Float, bold: Boolean, widthDp: Float) = width(text, sp, bold) <= widthDp

    /** 在 widthDp 宽里最多 maxLines 行的高度（dp）。 */
    fun height(text: String, sp: Float, bold: Boolean, widthDp: Float, maxLines: Int): Float =
        cache.getOrPut("$sp|$bold|$widthDp|$maxLines|$text") {
            layout(text, sp, bold, widthDp, maxLines).height / density
        }

    /** 不限行数时要几行。 */
    fun lines(text: String, sp: Float, bold: Boolean, widthDp: Float): Int =
        layout(text, sp, bold, widthDp, Int.MAX_VALUE).lineCount

    private fun layout(text: String, sp: Float, bold: Boolean, widthDp: Float, maxLines: Int): StaticLayout {
        val px = (widthDp * density).toInt().coerceAtLeast(1)
        val builder = StaticLayout.Builder.obtain(text, 0, text.length, paint(sp, bold), px)
            .setIncludePad(true)
            .setBreakStrategy(Layout.BREAK_STRATEGY_HIGH_QUALITY)
            .setHyphenationFrequency(Layout.HYPHENATION_FREQUENCY_NONE)
            .setMaxLines(maxLines)
            .setEllipsize(TextUtils.TruncateAt.END)
        if (Build.VERSION.SDK_INT >= 28) builder.setUseLineSpacingFromFallbacks(true)
        return builder.build()
    }
}

/** 楼名 + 编号（“德业楼Z4-106”）里的编号；编号里没有字母（“食工楼203”）就拆不开，返回 null。 */
fun roomCode(room: String): String? = Regex("""^[一-鿿]+(?=[A-Za-z])""").find(room)
    ?.let { room.substring(it.range.last + 1) }?.takeIf { it.isNotEmpty() }

/** 一段字实际用的字号：时间按列宽单独定了字号就用它的，否则按基准乘版面字号。 */
fun Piece.size(fs: Float) = sp ?: (pieceSp(kind) * fs)

/**
 * 卡片上的起止时间（放得下时卡片也写，和 iOS 一样）：
 * 一行写得下“10:00–12:15”就一行（列窄时字号按宽度缩，最多缩到 80%）；
 * 再窄就分两行“10:00–”“12:15”；连“10:00–”都放不下（极窄）才只写开始时间。
 */
fun timePiece(l: Lesson, m: TextMeasure, innerW: Float, fs: Float): Piece {
    val base = pieceSp(Kind.TIME) * fs
    val one = "${l.start}–${l.end}"
    val fitOne = base * innerW / m.width(one, base, true)
    if (fitOne >= base * 0.8f) return Piece(one, Kind.TIME, 1, minOf(base, fitOne * 0.98f))
    val head = "${l.start}–"
    val fitTwo = base * innerW / maxOf(m.width(head, base, true), m.width(l.end, base, true))
    if (fitTwo >= base * 0.7f) return Piece("$head\n${l.end}", Kind.TIME, 2, minOf(base, fitTwo * 0.98f))
    return Piece(l.start, Kind.TIME, 1, minOf(base, base * innerW / m.width(l.start, base, true) * 0.98f))
}

/**
 * 一张卡片从写得最全到最省的几档内容（下标就是 level）。
 * 时间和教室放不下两样时，**优先写教室**：起止时间这时由左边时间轴给出——开始时间标在卡片顶边旁、
 * 下课时间（“–09:45”）标在卡片底边旁。onAxis：时间轴上正好标着这节课的开始和下课时间，卡片上可以省掉时间。
 *   0 课名写全、起止时间、教室带楼名、原安排、教师
 *   1 去掉教师
 *   2 教室只写编号（“Z4-118”）
 *   3 去掉时间（在时间轴上）
 *   4 去掉原安排
 *   5 课名最多两行、教室编号一行
 *   6 课名一行、教室编号一行
 *   7 课名一行
 * 时间轴上没有这节课的准确时间（按节次排时同一节开始的课差几分钟、或者很矮的小组件里标签被省掉）时，
 * 卡片自己保留时间，仍然是教室在前：放不下就只写开始时间。
 */
fun variants(cell: Cell, m: TextMeasure, innerW: Float, fs: Float, onAxis: Boolean = true): List<List<Piece>> {
    val more = if (cell.hidden > 0) " +${cell.hidden}" else ""
    val c = cell.cancelled
    if (c != null) {
        val text = "停 ${c.name}"
        val time = Piece("${c.start}–${c.end}", Kind.TIME, 2)
        return listOf(
            listOf(Piece(text, Kind.NAME, 3), time),
            listOf(Piece(text, Kind.NAME, 2), time.copy(maxLines = 1)),
            listOf(Piece(text, Kind.NAME, 2)),
            listOf(Piece(text, Kind.NAME, 1)),
        )
    }
    val l = cell.lesson!!
    val name = (if (l.changed) "调 " else "") + l.name + more
    val time = timePiece(l, m, innerW, fs)
    val start = Piece(l.start, Kind.TIME, 1, time.sp)
    val roomFull = roomFull(l, m, roomSp(fs), innerW)
    val roomShort = roomShort(l, m, roomSp(fs), innerW)
    val roomLine = roomShort?.copy(maxLines = 1)
    val was = l.was?.let { Piece(it, Kind.WAS, 2) }
    val teacher = l.teachers.takeIf { it.isNotEmpty() }?.let { Piece(it, Kind.TEACHER, 1) }
    val fullName = Piece(name, Kind.NAME, 6)
    val rich = listOf(
        listOfNotNull(fullName, time, roomFull, was, teacher),
        listOfNotNull(fullName, time, roomFull, was),
        listOfNotNull(fullName, time, roomShort, was),
    )
    if (onAxis) {
        return rich + listOf(
            listOfNotNull(fullName, roomShort, was),
            listOfNotNull(fullName, roomShort),
            listOfNotNull(Piece(name, Kind.NAME, 2), roomLine),
            listOfNotNull(Piece(name, Kind.NAME, 1), roomLine),
            listOf(Piece(name, Kind.NAME, 1)),
        )
    }
    return rich + listOf(
        listOfNotNull(fullName, roomShort, time),
        listOfNotNull(fullName, roomShort, start),
        listOfNotNull(Piece(name, Kind.NAME, 2), roomLine, start),
        listOfNotNull(Piece(name, Kind.NAME, 1), roomLine, start),
        listOfNotNull(Piece(name, Kind.NAME, 1), roomLine),
        listOf(Piece(name, Kind.NAME, 1)),
    )
}

private fun roomSp(fs: Float) = pieceSp(Kind.ROOM) * fs

/** 教室写全：一行放不下时楼名单独一行（“德业楼\nZ4-106”），编号不被拆开。 */
private fun roomFull(l: Lesson, m: TextMeasure, sp: Float, innerW: Float): Piece? {
    if (l.room.isEmpty()) return null
    val code = roomCode(l.room)
    if (m.fits(l.room, sp, false, innerW) || code == null) return Piece(l.room, Kind.ROOM, 2)
    return Piece("${l.room.removeSuffix(code)}\n$code", Kind.ROOM, 3)
}

/** 教室只写编号（“Z4-106”）：一行放不下整个教室时省掉楼名。 */
private fun roomShort(l: Lesson, m: TextMeasure, sp: Float, innerW: Float): Piece? {
    if (l.room.isEmpty()) return null
    val code = roomCode(l.room)
    if (m.fits(l.room, sp, false, innerW) || code == null) return Piece(l.room, Kind.ROOM, 2)
    return Piece(code, Kind.ROOM, 2)
}

fun piecesHeight(pieces: List<Piece>, m: TextMeasure, innerW: Float, fs: Float): Float =
    CHIP_PAD_V + CHIP_SLACK + pieces.sumOf { p ->
        m.height(p.text, p.size(fs), pieceBold(p.kind), innerW, p.maxLines).toDouble()
    }.toFloat()

/** 课名必须写全的几档（maxLines = 6）：课名真要几行就算几行，超过 6 行就不算这一档。 */
fun fullyFits(pieces: List<Piece>, m: TextMeasure, innerW: Float, fs: Float): Boolean = pieces.all { p ->
    p.maxLines < 6 || m.lines(p.text, p.size(fs), pieceBold(p.kind), innerW) <= p.maxLines
}

/** 某张卡片在某档字号下写到 level 这一档要多高；这档写不全返回 null。 */
fun need(cell: Cell, level: Int, m: TextMeasure, innerW: Float, fs: Float, onAxis: Boolean): Float? {
    val all = variants(cell, m, innerW, fs, onAxis)
    val pieces = all[level.coerceAtMost(all.size - 1)]
    if (!fullyFits(pieces, m, innerW, fs)) return null
    return piecesHeight(pieces, m, innerW, fs)
}

/** 卡片实际拿到 height 高时，写得最全、又放得下的那一档。 */
fun bestPieces(cell: Cell, height: Float, m: TextMeasure, innerW: Float, fs: Float, onAxis: Boolean): List<Piece> {
    val all = variants(cell, m, innerW, fs, onAxis)
    for (pieces in all) {
        if (fullyFits(pieces, m, innerW, fs) && piecesHeight(pieces, m, innerW, fs) <= height + 0.5f) return pieces
    }
    return all.last()
}

/** 一节课从哪条分界线画到哪条；onAxis：时间轴上标着它准确的开始和下课时间。 */
data class Span(val cell: Cell, val col: Int, val a: Int, val b: Int) {
    var onAxis = false
}

/** 时间轴上的开始时间标签，按节次排时下面注“第几节”。 */
data class AxisLabel(val time: String, val unit: Int?)

class GridLayout(
    val bounds: FloatArray,           // 每条分界线的 y（dp）
    val starts: Map<Int, AxisLabel>,  // 分界线下标 → 开始时间（标在线下方，只含画得下的）
    val ends: Map<Int, String>,       // 分界线下标 → 下课时间（标在线上方，只含画得下的）
    val spans: List<Span>,
    val fontScale: Float,
    val byUnit: Boolean,
)

const val GAP = 6f

/**
 * 时间轴标签占的地方：above[k] 是分界线 k 上方的下课时间标签高，below[k] 是下方的开始时间标签高。
 * 相邻两条有标签的分界线之间至少放得下“上一条下方的 + 这一条上方的”。
 */
private class Marks(val above: FloatArray, val below: FloatArray) {
    companion object {
        fun none(n: Int) = Marks(FloatArray(n), FloatArray(n))
    }
}

private fun place(ideal: FloatArray, gap: BooleanArray, cons: List<Triple<Int, Int, Float>>, marks: Marks,
                  height: Float, t: Float): FloatArray {
    val gaps = gap.count { it } * GAP
    val total = ideal.sum()
    val unit = if (total > 0) (height - gaps) / total else 0f
    val ending = Array(ideal.size) { mutableListOf<Triple<Int, Int, Float>>() }
    for (c in cons) ending[c.second - 1] += c
    val y = FloatArray(ideal.size + 1)
    var prev = if (marks.below[0] > 0f) 0 else -1
    for (k in ideal.indices) {
        var v = y[k] + if (gap[k]) GAP else t * unit * ideal[k]
        for (c in ending[k]) v = maxOf(v, y[c.first] + c.third)
        val n = k + 1
        if (marks.above[n] > 0f || marks.below[n] > 0f) {
            v = maxOf(v, (if (prev >= 0) y[prev] + marks.below[prev] else 0f) + marks.above[n])
            prev = n
        }
        y[n] = v
    }
    if (prev >= 0) y[ideal.size] = maxOf(y[ideal.size], y[prev] + marks.below[prev])
    return y
}

/** 摆得下就尽量按比例（t 越大越接近纯按节次 / 时长）；t = 0 也摆不下返回 null。 */
private fun solve(ideal: FloatArray, gap: BooleanArray, cons: List<Triple<Int, Int, Float>>, marks: Marks,
                  height: Float): FloatArray? {
    val fits = { t: Float -> place(ideal, gap, cons, marks, height, t)[ideal.size] <= height + 0.01f }
    if (fits(1f)) return place(ideal, gap, cons, marks, height, 1f)
    if (!fits(0f)) return null
    var lo = 0f
    var hi = 1f
    repeat(20) {
        val mid = (lo + hi) / 2
        if (fits(mid)) lo = mid else hi = mid
    }
    return place(ideal, gap, cons, marks, height, lo)
}

/** 拉大了字也跟着放大；最多缩到 0.8 倍，再小就看不清。 */
val FONT_SCALES = floatArrayOf(1.5f, 1.35f, 1.2f, 1.1f, 1f, 0.9f, 0.8f)
const val LEVELS = 7  // 排版时只试 0–6 档（课名、教室都在）；都摆不下就纯按比例，卡片自己能写多少写多少

/** 纵轴：segs 是相邻两条分界线之间的一段（ideal 按比例分高度，gap 是固定的 GAP）；startIdx / endIdx 是每节课的上下分界线。 */
private class Axis(
    val ideal: FloatArray,
    val gap: BooleanArray,
    val startIdx: (Cell) -> Int,
    val endIdx: (Cell) -> Int,
    val unitAt: Map<Int, Int>,  // 按节次时：分界线 → 从它开始的那一节
)

/** 课的节次：“3–5” → 3..5，“6” → 6..6；没写返回 null。 */
private fun unitRange(units: String): IntRange? {
    val m = Regex("""^(\d+)(?:\D+(\d+))?$""").find(units.trim()) ?: return null
    val a = m.groupValues[1].toInt()
    val b = m.groupValues[2].ifEmpty { m.groupValues[1] }.toInt()
    return if (b >= a) a..b else null
}

/**
 * 按节次（学校课表的样子）：用到的每一节一样高，节号不连续或前后两节隔了半小时以上（午休、傍晚）的地方压成 GAP。
 * 没写节次的课（停课也没有）按同一周别的课补：开始时间相同的取它的起始节、结束时间相同的取它的结束节；
 * 有一节补不上就返回 null，整张表改按真实时间排，免得画错位置。
 */
private fun unitAxis(all: List<Cell>): Axis? {
    val units = HashMap<Cell, IntRange>()
    val first = HashMap<Int, Int>()
    val last = HashMap<Int, Int>()
    for (c in all) {
        val r = c.lesson?.units?.let(::unitRange) ?: continue
        units[c] = r
        first[c.start] = r.first
        last[c.end] = r.last
    }
    for (c in all) {
        if (c in units) continue
        val a = first[c.start] ?: return null
        val b = last[c.end] ?: return null
        if (b < a) return null
        units[c] = a..b
    }
    val startOf = HashMap<Int, Int>()
    val endOf = HashMap<Int, Int>()
    for ((c, r) in units) {
        startOf[r.first] = minOf(startOf[r.first] ?: Int.MAX_VALUE, c.start)
        endOf[r.last] = maxOf(endOf[r.last] ?: Int.MIN_VALUE, c.end)
    }
    val used = units.values.flatMap { it.toList() }.toSortedSet().toList()
    val ideal = mutableListOf<Float>()
    val gap = mutableListOf<Boolean>()
    val top = HashMap<Int, Int>()
    val bottom = HashMap<Int, Int>()
    used.forEachIndexed { i, u ->
        val prev = used.getOrNull(i - 1)
        val apart = prev != null && (u != prev + 1 ||
            (startOf[u] != null && endOf[u - 1] != null && startOf.getValue(u) - endOf.getValue(u - 1) >= 30))
        if (apart) {
            ideal += 0f
            gap += true
        }
        top[u] = ideal.size
        ideal += 1f
        gap += false
        bottom[u] = ideal.size
    }
    return Axis(ideal.toFloatArray(), gap.toBooleanArray(), { top.getValue(units.getValue(it).first) },
        { bottom.getValue(units.getValue(it).last) }, top.entries.associate { (u, k) -> k to u })
}

/** 按真实时间：有课的时段合并成几块，块里每个上下课时刻一条分界线，块之间压成 GAP。 */
private fun timeAxis(all: List<Cell>): Axis {
    val spans = all.map { it.start to it.end }.sortedBy { it.first }
    val blocks = mutableListOf<IntArray>()
    for ((s, e) in spans) {
        val last = blocks.lastOrNull()
        if (last != null && s <= last[1]) last[1] = maxOf(last[1], e) else blocks += intArrayOf(s, e)
    }
    val marks = all.flatMap { listOf(it.start, it.end) }.toSortedSet()
    val ideal = mutableListOf<Float>()
    val gap = mutableListOf<Boolean>()
    val at = HashMap<Int, Int>()  // 时刻 → 分界线下标（= 它上面有几段）
    blocks.forEachIndexed { i, (bs, be) ->
        if (i > 0) {
            ideal += 0f
            gap += true
        }
        val inBlock = marks.filter { it in bs..be }
        inBlock.forEachIndexed { j, t ->
            if (j > 0) {
                ideal += (t - inBlock[j - 1]).toFloat()
                gap += false
            }
            at[t] = ideal.size
        }
    }
    return Axis(ideal.toFloatArray(), gap.toBooleanArray(), { at.getValue(it.start) }, { at.getValue(it.end) }, emptyMap())
}

private fun hm(minutes: Int) = "%02d:%02d".format(minutes / 60, minutes % 60)

/**
 * 纵轴优先按节次；有课没写节次又补不上时退回按真实时间。
 * labelH / endH：开始时间（含粗体）、下课时间标签的高度。
 */
fun layoutGrid(columns: List<List<Cell>>, height: Float, innerW: Float, m: TextMeasure, labelH: Float, endH: Float): GridLayout? {
    val all = columns.flatten()
    if (all.isEmpty()) return null
    val unitAxis = unitAxis(all)
    val axis = unitAxis ?: timeAxis(all)
    val n = axis.ideal.size + 1
    val cellSpans = columns.flatMapIndexed { col, cells -> cells.map { Span(it, col, axis.startIdx(it), axis.endIdx(it)) } }
    // 每条线下方标从它开始的最早时间、上方标在它下课的最晚时间；按节次排时同一节的课可能差几分钟，
    // 对不上的那几节课卡片上自己写时间
    val starts = cellSpans.groupBy { it.a }.mapValues { (k, list) -> AxisLabel(hm(list.minOf { it.cell.start }), axis.unitAt[k]) }
    val ends = cellSpans.groupBy { it.b }.mapValues { (_, list) -> hm(list.maxOf { it.cell.end }) }
    fun markOnAxis(shownStarts: Map<Int, AxisLabel>, shownEnds: Map<Int, String>) {
        for (s in cellSpans) {
            s.onAxis = shownStarts[s.a]?.time == hm(s.cell.start) && shownEnds[s.b] == hm(s.cell.end)
        }
    }
    markOnAxis(starts, ends)
    val marks = Marks(FloatArray(n) { if (it in ends) endH else 0f }, FloatArray(n) { if (it in starts) labelH else 0f })
    for (level in 0 until LEVELS) {
        for (fs in FONT_SCALES) {
            // 教师、楼名不值得缩字：只有课名、时间、教室这些要紧的才往小里挤
            if (level <= 1 && fs < 1f) continue
            val cons = cellSpans.map { s ->
                val h = need(s.cell, level, m, innerW, fs, s.onAxis) ?: return@map null
                Triple(s.a, s.b, h + CHIP_GAP)
            }
            if (cons.any { it == null }) continue
            val bounds = solve(axis.ideal, axis.gap, cons.filterNotNull(), marks, height) ?: continue
            return GridLayout(bounds, starts, ends, cellSpans, fs, unitAxis != null)
        }
    }
    // 全都摆不下（很矮的小组件）：纯按比例，时间轴标签从上往下能放几个放几个，被省掉时间的课卡片上自己写
    val bounds = solve(axis.ideal, axis.gap, emptyList(), Marks.none(n), height) ?: return null
    val items = starts.keys.map { Triple(bounds[it], labelH, it) } + ends.keys.map { Triple(bounds[it] - endH, endH, -1 - it) }
    val shownStarts = HashMap<Int, AxisLabel>()
    val shownEnds = HashMap<Int, String>()
    var lastBottom = 0f
    for ((top, h, key) in items.sortedBy { it.first }) {
        if (top < lastBottom - 0.5f || top + h > height + 0.5f) continue
        if (key >= 0) shownStarts[key] = starts.getValue(key) else shownEnds[-1 - key] = ends.getValue(-1 - key)
        lastBottom = top + h
    }
    markOnAxis(shownStarts, shownEnds)
    return GridLayout(bounds, shownStarts, shownEnds, cellSpans, FONT_SCALES.last(), unitAxis != null)
}
