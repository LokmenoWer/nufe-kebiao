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
 * 本周小组件的版面：和 Scriptable 的 full 模式同一套规则，但字不用估——用画小组件的同一套系统字体
 * （StaticLayout，行高、汉字回退字体、折行都和 TextView 一致）量出来。
 *
 * 纵轴按真实时间：一周里有课的时段合并成几块，块里每个上下课时刻是一条分界线，块之间压成 GAP；
 * 每张卡片先保证写得下它这一档的内容，剩下的高度再按分钟数分。依次试“写全”到“只写课名”几档、每档几种字号，
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
data class Piece(val text: String, val kind: Kind, val maxLines: Int)

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

/**
 * 一张卡片从写得最全到最省的几档内容（下标就是 level）：
 *   0 课名写全、起止时间、教室带楼名、原安排、教师
 *   1 去掉教师
 *   2 教室只写编号
 *   3 去掉时间（开始时间在左边时间轴上，卡片的长短就是时长）
 *   4 课名最多两行 + 教室编号
 *   5 课名最多两行
 *   6 课名一行
 * 时间：一行写得下“10:00–11:35”就写全，否则写“–11:35”（开始时间已经标在时间轴上卡片顶边旁）。
 */
fun variants(cell: Cell, m: TextMeasure, innerW: Float, fs: Float, labeled: Boolean = true): List<List<Piece>> {
    val more = if (cell.hidden > 0) " +${cell.hidden}" else ""
    val c = cell.cancelled
    if (c != null) {
        val text = "停 ${c.name}"
        return listOf(listOf(Piece(text, Kind.NAME, 3)), listOf(Piece(text, Kind.NAME, 2)), listOf(Piece(text, Kind.NAME, 1)))
    }
    val l = cell.lesson!!
    val name = (if (l.changed) "调 " else "") + l.name + more
    val sp = pieceSp(Kind.TIME) * fs
    val full = "${l.start}–${l.end}"
    val time = Piece(when {
        m.fits(full, sp, true, innerW) -> full
        labeled -> "–${l.end}"
        else -> l.start
    }, Kind.TIME, 1)
    // 时间轴上没标出这节课的开始时间（太挤被省掉）：时间排到最前，每一档都写
    if (!labeled) {
        val start = Piece(time.text, Kind.TIME, 1)
        return listOf(
            listOfNotNull(start, Piece(name, Kind.NAME, 6), roomFull(l, m, roomSp(fs), innerW), l.was?.let { Piece(it, Kind.WAS, 2) }),
            listOfNotNull(start, Piece(name, Kind.NAME, 6), roomShort(l, m, roomSp(fs), innerW)),
            listOfNotNull(start, Piece(name, Kind.NAME, 2), roomShort(l, m, roomSp(fs), innerW)?.copy(maxLines = 1)),
            listOf(start, Piece(name, Kind.NAME, 2)),
            listOf(start, Piece(name, Kind.NAME, 1)),
            listOf(Piece(name, Kind.NAME, 1)),
        )
    }
    val roomFull = roomFull(l, m, roomSp(fs), innerW)
    val roomShort = roomShort(l, m, roomSp(fs), innerW)
    val was = l.was?.let { Piece(it, Kind.WAS, 2) }
    val teacher = l.teachers.takeIf { it.isNotEmpty() }?.let { Piece(it, Kind.TEACHER, 1) }
    val fullName = Piece(name, Kind.NAME, 6)
    return listOf(
        listOfNotNull(fullName, time, roomFull, was, teacher),
        listOfNotNull(fullName, time, roomFull, was),
        listOfNotNull(fullName, time, roomShort, was),
        listOfNotNull(fullName, roomShort),
        listOfNotNull(Piece(name, Kind.NAME, 2), roomShort?.copy(maxLines = 1)),
        listOf(Piece(name, Kind.NAME, 2)),
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

/** 课名必须写全的几档（0–3）里，课名真要几行就算几行；写不全就不算这一档。 */
fun piecesHeight(pieces: List<Piece>, m: TextMeasure, innerW: Float, fs: Float): Float =
    CHIP_PAD_V + CHIP_SLACK + pieces.sumOf { p ->
        m.height(p.text, pieceSp(p.kind) * fs, pieceBold(p.kind), innerW, p.maxLines).toDouble()
    }.toFloat()

fun fullyFits(pieces: List<Piece>, m: TextMeasure, innerW: Float, fs: Float): Boolean = pieces.all { p ->
    p.maxLines < 6 || m.lines(p.text, pieceSp(p.kind) * fs, pieceBold(p.kind), innerW) <= p.maxLines
}

/** 某张卡片在某档字号下写到 level 这一档要多高；这档写不全（课名超过 6 行）返回 null。 */
fun need(cell: Cell, level: Int, m: TextMeasure, innerW: Float, fs: Float): Float? {
    val all = variants(cell, m, innerW, fs)
    val pieces = all[level.coerceAtMost(all.size - 1)]
    if (!fullyFits(pieces, m, innerW, fs)) return null
    return piecesHeight(pieces, m, innerW, fs)
}

/** 卡片实际拿到 height 高时，写得最全、又放得下的那一档。 */
fun bestPieces(cell: Cell, height: Float, m: TextMeasure, innerW: Float, fs: Float, labeled: Boolean): List<Piece> {
    val all = variants(cell, m, innerW, fs, labeled)
    for (pieces in all) {
        if (fullyFits(pieces, m, innerW, fs) && piecesHeight(pieces, m, innerW, fs) <= height + 0.5f) return pieces
    }
    return all.last()
}

/** 一节课从哪条分界线画到哪条。 */
data class Span(val cell: Cell, val col: Int, val a: Int, val b: Int)

class GridLayout(
    val bounds: FloatArray,           // 每条分界线的 y（dp）
    val minutes: IntArray,            // 每条分界线的时刻（分钟），间隔处为 -1
    val spans: List<Span>,
    val fontScale: Float,
    val labeled: Set<Int>,            // 时间轴上标了时间的分界线
)

const val GAP = 6f

private fun place(ideal: FloatArray, gap: BooleanArray, cons: List<Triple<Int, Int, Float>>, labels: List<Int>,
                  labelH: Float, height: Float, t: Float): FloatArray {
    val gaps = gap.count { it } * GAP
    val total = ideal.sum()
    val unit = if (total > 0) (height - gaps) / total else 0f
    val ending = Array(ideal.size) { mutableListOf<Triple<Int, Int, Float>>() }
    for (c in cons) ending[c.second - 1] += c
    val prevLabel = HashMap<Int, Int>()
    labels.forEachIndexed { i, k -> if (i > 0) prevLabel[k] = labels[i - 1] }
    val y = FloatArray(ideal.size + 1)
    for (k in ideal.indices) {
        var v = y[k] + if (gap[k]) GAP else t * unit * ideal[k]
        for (c in ending[k]) v = maxOf(v, y[c.first] + c.third)
        prevLabel[k + 1]?.let { v = maxOf(v, y[it] + labelH) }
        y[k + 1] = v
    }
    labels.lastOrNull()?.let { y[ideal.size] = maxOf(y[ideal.size], y[it] + labelH) }
    return y
}

/** 摆得下就尽量按比例（t 越大越接近纯按时长）；t = 0 也摆不下返回 null。 */
private fun solve(ideal: FloatArray, gap: BooleanArray, cons: List<Triple<Int, Int, Float>>, labels: List<Int>,
                  labelH: Float, height: Float): FloatArray? {
    val fits = { t: Float -> place(ideal, gap, cons, labels, labelH, height, t)[ideal.size] <= height + 0.01f }
    if (fits(1f)) return place(ideal, gap, cons, labels, labelH, height, 1f)
    if (!fits(0f)) return null
    var lo = 0f
    var hi = 1f
    repeat(20) {
        val mid = (lo + hi) / 2
        if (fits(mid)) lo = mid else hi = mid
    }
    return place(ideal, gap, cons, labels, labelH, height, lo)
}

/** 拉大了字也跟着放大；最多缩到 0.8 倍，再小就看不清。 */
val FONT_SCALES = floatArrayOf(1.2f, 1.1f, 1f, 0.9f, 0.8f)
const val LEVELS = 6  // 排版时只试 0–5 档；都摆不下就纯按时长，卡片自己能写多少写多少

fun layoutGrid(columns: List<List<Cell>>, height: Float, innerW: Float, m: TextMeasure, labelH: Float): GridLayout? {
    val all = columns.flatten()
    if (all.isEmpty()) return null
    // 合并成块：块里每个上下课时刻一条分界线
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
    val boundMinutes = IntArray(ideal.size + 1) { -1 }
    for ((t, k) in at) boundMinutes[k] = t
    val cellSpans = columns.flatMapIndexed { col, cells -> cells.map { Span(it, col, at.getValue(it.start), at.getValue(it.end)) } }
    val labels = cellSpans.map { it.a }.distinct().sorted()
    for (level in 0 until LEVELS) {
        for (fs in FONT_SCALES) {
            // 教师、楼名不值得缩字：只有课名、时间、教室这些要紧的才往小里挤
            if (level <= 1 && fs < 1f) continue
            val cons = cellSpans.map { s ->
                val h = need(s.cell, level, m, innerW, fs) ?: return@map null
                Triple(s.a, s.b, h + CHIP_GAP)
            }
            if (cons.any { it == null }) continue
            val bounds = solve(ideal.toFloatArray(), gap.toBooleanArray(), cons.filterNotNull(), labels, labelH, height)
                ?: continue
            return GridLayout(bounds, boundMinutes, cellSpans, fs, labels.toSet())
        }
    }
    // 全都摆不下（很矮的小组件）：纯按时长，时间轴标签挤不下的省掉
    val bounds = solve(ideal.toFloatArray(), gap.toBooleanArray(), emptyList(), emptyList(), labelH, height)
        ?: return null
    val shown = mutableSetOf<Int>()
    var lastY = -labelH
    for (k in labels) {
        if (bounds[k] >= lastY + labelH && bounds[k] + labelH <= height + 0.5f) {
            shown += k
            lastY = bounds[k]
        }
    }
    return GridLayout(bounds, boundMinutes, cellSpans, FONT_SCALES.last(), shown)
}
