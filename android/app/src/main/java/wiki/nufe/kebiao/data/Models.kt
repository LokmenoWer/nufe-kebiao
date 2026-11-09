package wiki.nufe.kebiao.data

import org.json.JSONArray
import org.json.JSONObject

/**
 * 本机整学期课表缓存投影出的展示数据（TimetableParser.payload()）。
 * 周次、缺失时间的兜底、课程颜色都由本机解析器算好，这里只解析，不重算。
 */
data class Payload(
    val now: String?,
    val semester: String?,
    val semesterId: String?,
    val updatedAt: Long?,          // 课表最近一次从学校拉到的时刻（秒）
    val status: String,            // ok | error | captcha | needs_rebind
    val today: Day?,
    val tomorrow: Day?,
    val week: WeekBlock?,
    val nextWeek: WeekBlock?,
    val weeks: List<WeekBlock> = emptyList(),
    val changes: List<TimetableChange> = emptyList(),
    val term: Term?,
    val upcoming: Day?,
    val changedAt: Long?,
    val refresh: Refresh,
    val delegate: Boolean,         // 这台设备在替服务器向学校拉课表
    val pollAfterSeconds: Long?,   // 服务器建议多久后再来问
) {
    companion object {
        fun parse(text: String): Payload = fromJson(JSONObject(text))

        fun fromJson(o: JSONObject): Payload {
            return Payload(
                now = o.str("now"),
                semester = o.str("semester"),
                semesterId = if (o.isNull("semester_id")) null else o.opt("semester_id")?.toString(),
                updatedAt = o.long("updated_at"),
                status = o.str("status") ?: "ok",
                today = o.optJSONObject("today")?.let(Day::parse),
                tomorrow = o.optJSONObject("tomorrow")?.let(Day::parse),
                week = o.optJSONObject("week")?.let(WeekBlock::parse),
                nextWeek = o.optJSONObject("next_week")?.let(WeekBlock::parse),
                weeks = o.optJSONArray("weeks").objects().map(WeekBlock::parse),
                changes = o.optJSONArray("changes").objects().map(TimetableChange::parse),
                term = o.optJSONObject("term")?.let { Term(it.optString("first_day"), it.optString("last_day")) },
                upcoming = o.optJSONObject("upcoming")?.let(Day::parse),
                changedAt = o.long("changed_at"),
                refresh = o.optJSONObject("refresh").let {
                    Refresh(pending = it?.optBoolean("pending") == true, due = it?.optBoolean("due") == true)
                },
                delegate = o.optBoolean("delegate"),
                pollAfterSeconds = o.long("poll_after"),
            )
        }
    }
}

/** 本机加密课表缓存里的个人信息（姓名 / 学号）；不含邮箱，也不向任何服务器索取。 */
data class Profile(val student: StudentInfo?, val mail: MailInfo?) {
    companion object {
        fun parse(text: String): Profile {
            val o = JSONObject(text)
            return Profile(
                student = o.optJSONObject("student")?.let(StudentInfo::parse)?.takeIf { it.hasData },
                mail = o.optJSONObject("mail")?.let {
                    MailInfo(bound = it.optBoolean("bound"), email = it.str("email"))
                },
            )
        }
    }
}

data class StudentInfo(
    val name: String?,
    val code: String?,
) {
    val hasData get() = name != null || code != null

    companion object {
        fun parse(o: JSONObject) = StudentInfo(
            name = o.str("name"),
            code = o.str("code"),
        )
    }
}

data class MailInfo(val bound: Boolean, val email: String?)

data class Day(
    val date: String,              // 2026-11-02
    val weekday: Int,              // 1 = 周一
    val week: Int?,                // 第几周；放假 / 学期外为 null
    val lessons: List<Lesson>,
    val cancelled: List<Cancelled>,
) {
    companion object {
        fun parse(o: JSONObject) = Day(
            date = o.optString("date"),
            weekday = o.optInt("weekday", 1),
            week = if (o.isNull("week")) null else o.optInt("week"),
            lessons = o.optJSONArray("lessons").objects().map(Lesson::parse),
            cancelled = o.optJSONArray("cancelled").objects().map(Cancelled::parse),
        )
    }
}

data class Lesson(
    val name: String,
    val start: String,             // 08:20
    val end: String,
    val room: String,
    val units: String,             // "1–2"
    val teachers: String,
    val color: String,             // #4b63e0，与网页同色
    val changed: Boolean,
    val was: String?,              // "原 A101" / "原 10:00"
) {
    val startMinutes: Int = toMinutes(start)
    val endMinutes: Int = toMinutes(end)

    companion object {
        fun parse(o: JSONObject) = Lesson(
            name = o.optString("name", "未命名课程"),
            start = o.optString("start"),
            end = o.optString("end"),
            room = o.optString("room"),
            units = o.optString("units"),
            teachers = o.optString("teachers"),
            color = o.optString("color", "#4b63e0"),
            changed = o.optBoolean("changed"),
            was = o.str("was"),
        )
    }
}

data class Cancelled(val name: String, val start: String, val end: String, val room: String, val color: String) {
    val startMinutes: Int = toMinutes(start)
    val endMinutes: Int = toMinutes(end)

    companion object {
        fun parse(o: JSONObject) = Cancelled(
            name = o.optString("name"),
            start = o.optString("start"),
            end = o.optString("end"),
            room = o.optString("room"),
            color = o.optString("color", "#495057"),
        )
    }
}

data class WeekBlock(val week: Int?, val monday: String, val days: List<Day>) {
    companion object {
        fun parse(o: JSONObject) = WeekBlock(
            week = if (o.isNull("week")) null else o.optInt("week"),
            monday = o.optString("monday"),
            days = o.optJSONArray("days").objects().map(Day::parse),
        )
    }
}

data class Term(val firstDay: String, val lastDay: String)

/** 一条调课明细：第几周、日期、课程名、变更说明（原时间/原地点/停课/新增）、类型。 */
data class TimetableChange(val week: Int?, val date: String, val name: String, val detail: String, val kind: String) {
    companion object {
        fun parse(o: JSONObject) = TimetableChange(
            week = if (o.isNull("week")) null else o.optInt("week"),
            date = o.optString("date"),
            name = o.optString("name"),
            detail = o.optString("detail"),
            kind = o.optString("kind"),
        )
    }
}

data class Refresh(val pending: Boolean, val due: Boolean)

fun toMinutes(hm: String): Int {
    val parts = hm.split(":")
    val h = parts.getOrNull(0)?.toIntOrNull() ?: return 0
    val m = parts.getOrNull(1)?.toIntOrNull() ?: return 0
    return h * 60 + m
}

private fun JSONObject.str(key: String): String? = if (isNull(key)) null else optString(key).ifEmpty { null }

private fun JSONObject.long(key: String): Long? = if (isNull(key) || !has(key)) null else optDouble(key).toLong()

private fun JSONArray?.objects(): List<JSONObject> =
    if (this == null) emptyList() else (0 until length()).mapNotNull { optJSONObject(it) }
