package wiki.nufe.kebiao.data

import java.time.Instant
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.temporal.ChronoUnit

/** 学校课表按北京时间算；手机设成别的时区也照北京时间判断“今天”和“正在上课”。 */
val SHANGHAI: ZoneId = ZoneId.of("Asia/Shanghai")

const val STALE_SECONDS = 2 * 86400L         // 课表超过这么久没更新就提示
const val CHANGE_NOTE_SECONDS = 86400L       // 一天内发现过调课：标题栏提示

val WEEKDAY = arrayOf("", "一", "二", "三", "四", "五", "六", "日")

data class Now(val ms: Long) {
    private val zoned: ZonedDateTime = Instant.ofEpochMilli(ms).atZone(SHANGHAI)
    val date: String = zoned.toLocalDate().toString()
    val minutes: Int = zoned.hour * 60 + zoned.minute
    val weekday: Int = zoned.dayOfWeek.value

    companion object {
        /** 调试预览用：把时钟拨到别的时刻看各种状态；正式运行时一直是 0。 */
        @Volatile var offsetMs = 0L

        fun current() = Now(System.currentTimeMillis() + offsetMs)
    }
}

enum class State { PAST, LIVE, NEXT, LATER }

data class Slot(val lesson: Lesson, val state: State)

/** "2026-10-26" + "08:20"（北京时间）→ 毫秒时刻，给倒计时和提醒用。 */
fun lessonMs(date: String, hm: String): Long =
    LocalDate.parse(date).atTime(LocalTime.of(toMinutes(hm) / 60, toMinutes(hm) % 60)).atZone(SHANGHAI)
        .toInstant().toEpochMilli()

fun shortDate(iso: String): String {
    val parts = iso.split("-")
    if (parts.size < 3) return ""
    return "${parts[1].toInt()}/${parts[2].toInt()}"
}

fun daysBetween(from: String, to: String): Long = ChronoUnit.DAYS.between(LocalDate.parse(from), LocalDate.parse(to))

/** 只有“今天”才标 NEXT；明天、本周其他天传 markNext = false。 */
fun annotate(lessons: List<Lesson>, nowMinutes: Int, markNext: Boolean = true): List<Slot> {
    var nextMarked = !markNext
    return lessons.map { lesson ->
        val state = when {
            lesson.endMinutes <= nowMinutes -> State.PAST
            lesson.startMinutes <= nowMinutes -> State.LIVE
            !nextMarked -> State.NEXT.also { nextMarked = true }
            else -> State.LATER
        }
        Slot(lesson, state)
    }
}

/** 缓存可能是昨天拿的：跨过午夜后把“明天”当今天用；更旧的数据不再显示具体课程。 */
fun resolveDays(payload: Payload, now: Now): Pair<Day?, Day?> {
    val today = payload.today
    val tomorrow = payload.tomorrow
    if (today != null && today.date != now.date) {
        return if (tomorrow != null && tomorrow.date == now.date) tomorrow to null else null to null
    }
    return today to tomorrow
}

fun cancelledOn(day: Day?, nowMinutes: Int = -1): List<Cancelled> =
    day?.cancelled.orEmpty().filter { it.endMinutes > nowMinutes }

sealed interface TermPhase {
    data class Before(val days: Long) : TermPhase
    data object After : TermPhase
    data object During : TermPhase
}

fun termState(payload: Payload, now: Now): TermPhase {
    val term = payload.term ?: return TermPhase.During
    if (term.firstDay.isEmpty() || term.lastDay.isEmpty()) return TermPhase.During
    if (now.date < term.firstDay) return TermPhase.Before(daysBetween(now.date, term.firstDay))
    if (now.date > term.lastDay) return TermPhase.After
    return TermPhase.During
}

fun emptyText(day: Day?, payload: Payload, now: Now, tomorrow: Boolean): String = when (val t = termState(payload, now)) {
    is TermPhase.Before -> "距开学还有 ${t.days} 天"
    TermPhase.After -> "本学期课程已结束"
    TermPhase.During -> when {
        day == null -> "数据过旧，点按刷新"
        day.week == null -> "假期中"
        tomorrow -> "明天没课，好好休息吧!"
        else -> "今天没课，好好休息吧!"
    }
}

/** 是否是"没课/休息"类绿色文案（区别于"今天课程已上完!"）。 */
fun isRestMessage(text: String): Boolean = text.contains("好好休息")

/** 今天之后第一个有课的日子：明天有课就是明天，否则用服务端给的 upcoming（两周内）。 */
fun nextClassDay(payload: Payload, now: Now, tomorrow: Day?): Day? {
    if (tomorrow != null && tomorrow.lessons.isNotEmpty()) return tomorrow
    val up = payload.upcoming ?: return null
    return if (up.date > now.date && up.lessons.isNotEmpty()) up else null
}

fun dayLabel(day: Day, now: Now): String {
    val tomorrowIso = LocalDate.parse(now.date).plusDays(1).toString()
    if (day.date == tomorrowIso) return "明天 · 周${WEEKDAY[day.weekday]}"
    return "下次上课 · 周${WEEKDAY[day.weekday]} ${shortDate(day.date)}"
}

data class Preview(val title: String, val day: Day, val slots: List<Slot>, val cancelled: List<Cancelled>)

/**
 * 今天小组件要画的内容。标题恒为"今天"，课上完/没课时把"明天"降级成一行说明 + 课表预告，
 * 不再把整块切成"明天"视图，避免"今天/明天"来回跳导致的歧义。
 */
data class DayPlan(
    val title: String,
    val day: Day?,
    val slots: List<Slot>,
    val total: Int,
    val cancelled: List<Cancelled>,
    val empty: String,
    val tomorrowHint: String? = null,
    val preview: Preview? = null,
    val showPreviewTitle: Boolean = true,
)

private fun previewOf(day: Day, now: Now) =
    Preview(dayLabel(day, now), day, annotate(day.lessons, -1, false), cancelledOn(day))

/** 今天还有课就画今天；今天的课都上完或本来就没课时，统一用"今天"标题 + 明天/下次上课预告。 */
fun buildTodayPlan(payload: Payload, now: Now): DayPlan {
    val (today, tomorrow) = resolveDays(payload, now)
    if (today == null) return DayPlan("今天", null, emptyList(), 0, emptyList(), "数据过旧，点按刷新")
    val all = annotate(today.lessons, now.minutes)
    val remaining = all.filter { it.state != State.PAST }
    val cancelled = cancelledOn(today, now.minutes)
    if (remaining.isNotEmpty()) return DayPlan("今天", today, remaining, all.size, cancelled, "")
    val headline = if (all.isNotEmpty()) "今天课程已上完!" else emptyText(today, payload, now, tomorrow = false)
    val base = DayPlan("今天", today, emptyList(), all.size, cancelled, headline)
    val normalDay = termState(payload, now) is TermPhase.During && today.week != null
    return when {
        // 上课日且明天有课：说明"明天还有 x 节课"，下面直接列明天课表（不再显示"明天"标题）。
        normalDay && tomorrow != null && tomorrow.lessons.isNotEmpty() ->
            base.copy(tomorrowHint = "明天还有 ${tomorrow.lessons.size} 节课",
                preview = previewOf(tomorrow, now), showPreviewTitle = false)
        // 上课日且明天没课：今天上完课才补一句"明天没课"；今天本就空堂时不再重复，只预告下次上课。
        normalDay ->
            base.copy(tomorrowHint = if (all.isEmpty()) null else "明天没课，好好休息吧!",
                preview = nextClassDay(payload, now, null)?.let { previewOf(it, now) })
        // 假期 / 开学前 / 结课：只给"下次上课"预告。
        else -> base.copy(preview = nextClassDay(payload, now, tomorrow)?.let { previewOf(it, now) })
    }
}

/** 本周剩下的课都上完（含今天已结束的课不算）、且下周有课时改看下周（不限周末）。 */
fun weekToShow(payload: Payload, now: Now): Pair<String, WeekBlock?> {
    val current = payload.week
    val next = payload.nextWeek
    val restHasClass = current?.days.orEmpty().any { day ->
        when {
            day.date > now.date -> day.lessons.isNotEmpty()
            day.date == now.date -> day.lessons.any { it.endMinutes > now.minutes }
            else -> false
        }
    }
    val nextHasClass = next?.days.orEmpty().any { it.lessons.isNotEmpty() }
    if (!restHasClass && nextHasClass) return "下周" to next
    return "本周" to current
}

data class DayColumn(val day: Day, val isToday: Boolean, val slots: List<Slot>, val cancelled: List<Cancelled>)

/** 周一到周五总是画；周末画到最后一个有课的日子。 */
fun weekColumns(block: WeekBlock?, now: Now): List<DayColumn> {
    val days = block?.days.orEmpty()
    var count = minOf(5, days.size)
    days.forEachIndexed { i, d -> if (i >= 5 && (d.lessons.isNotEmpty() || d.cancelled.isNotEmpty())) count = i + 1 }
    return days.take(count).map { d ->
        val isToday = d.date == now.date
        val nowMin = when {
            isToday -> now.minutes
            d.date < now.date -> 24 * 60
            else -> -1
        }
        DayColumn(
            day = d, isToday = isToday, slots = annotate(d.lessons, nowMin, isToday),
            cancelled = if (d.date < now.date) emptyList() else cancelledOn(d, if (isToday) now.minutes else -1),
        )
    }
}

enum class Tone { WARN, ACCENT, FAINT }

data class Note(val text: String, val tone: Tone)

/** 自动刷新停了、要用户自己登录一次才会恢复。error 只是暂时失败，服务器会自己重试。 */
val Payload.needsLogin get() = status == "needs_rebind" || status == "captcha"

/** 标题栏右侧的一句话：登录失效 > 很久没更新 > 离线 > 更新中 > 有调课 > 第几周。 */
fun headerNote(snapshot: Snapshot, day: Day?, now: Now, compact: Boolean, changed: Boolean = false): Note? {
    val p = snapshot.payload
    if (p.needsLogin) return Note("需重新登录", Tone.WARN)
    val nowSec = now.ms / 1000
    val age = p.updatedAt?.let { nowSec - it } ?: 0
    if (age > STALE_SECONDS) return Note("${age / 86400} 天未更新", Tone.WARN)
    if (p.status == "error") return Note("刷新失败", Tone.WARN)
    if (snapshot.offline) return Note("离线", Tone.FAINT)
    if (p.refresh.pending || p.refresh.due) return Note("更新中", Tone.ACCENT)
    val recent = p.changedAt != null && nowSec - p.changedAt < CHANGE_NOTE_SECONDS
    if (changed || recent) return Note("有调课", Tone.WARN)
    if (day?.week != null && !compact) return Note("第 ${day.week} 周", Tone.FAINT)
    return null
}

/** 网站上对应的视图：今天 / 明天 → 日视图那一天；本周 → 周视图。 */
fun siteLink(base: String, payload: Payload, day: Day?, view: String = "day"): String {
    val params = mutableListOf<String>()
    payload.semesterId?.let { params += "sem=$it" }
    day?.week?.let { params += "w=$it" }
    params += "view=$view"
    if (view == "day" && day != null) params += "d=${day.weekday - 1}"
    return "$base#${params.joinToString("&")}"
}
