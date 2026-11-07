package wiki.nufe.kebiao.data

import java.time.Instant
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.temporal.ChronoUnit
import org.json.JSONArray
import org.json.JSONObject
import org.json.JSONTokener

/** Local, deterministic equivalent of jwxt.normalize / widget.build. No I/O or Android APIs.
 *
 * parse throws IllegalArgumentException on an unrecognised response: callers must only replace
 * their encrypted cache after success. Only student.name/code survive, never personId or raw data.
 * changes contain dated, canonical removed/added occurrences (not upstream scheduling rows).
 */
object TimetableParser {
    private val zone = ZoneId.of("Asia/Shanghai")
    private const val HISTORY_SECONDS = 7 * 86400L
    private const val MAX_JSON = 2 * 1024 * 1024
    // The checked-in semesters.json override takes precedence over the school's detail endpoint.
    private val calendar = mapOf("2026-2027-1" to LocalDate.of(2026, 9, 7))
    private val palette = listOf(
        "#4b63e0", "#0f8b8d", "#2f9e44", "#e8590c", "#ae3ec9", "#1971c2",
        "#c2255c", "#5c940d", "#e67700", "#6741d9", "#0b7285", "#d9480f",
        "#087f5b", "#a61e4d", "#3b5bdb", "#1098ad", "#74b816", "#f08c00",
        "#9c36b5", "#495057",
    )
    private val lessonOrder = compareBy<Occurrence>({ it.date }, { it.start }, { it.end },
        { it.name }, { it.room }, { it.campus }, { it.teachers.joinToString("、") })

    fun semesterId(courseHtml: String): Int = coursePage(courseHtml).first

    fun parse(
        courseHtml: String,
        printData: String,
        detail: String?,
        previous: JSONObject? = null,
        nowMs: Long = System.currentTimeMillis(),
    ): JSONObject {
        val (sid, name) = coursePage(courseHtml)
        val root = jsonObject(printData, "课表数据")
        val error = root.opt("error")
        require((!root.has("success") || root.opt("success") == true) &&
            (error == null || error == JSONObject.NULL || error == false || error == "")) { "课表接口返回错误" }
        val vms = root.optJSONArray("studentTableVms") ?: invalid("课表缺少 studentTableVms 数组")
        val student = JSONObject()
        val activities = if (vms.length() == 0) emptyList() else {
            val vm = vms.optJSONObject(0) ?: invalid("studentTableVms[0] 不是对象")
            for (key in listOf("name", "code")) {
                scalarText(vm, key).takeIf { it.isNotBlank() }?.let { student.put(key, it) }
            }
            readActivities(vm.optJSONArray("activities") ?: invalid("课表缺少 activities 数组"))
        }
        val semesterDetail = detail?.takeIf { it.isNotBlank() }?.let { jsonObject(it, "学期详情") }
        require(semesterDetail == null || semesterDetail.isNull("id") || integer(semesterDetail.opt("id")) == sid) {
            "学期详情与课表学期不一致"
        }
        return assemble(sid, name, activities, student, detailMonday(semesterDetail), previous, Math.floorDiv(nowMs, 1000))
    }

    /**
     * 托管到本站时，本机刷新失败就用本站那份（/mobile/{token}/timetable）。本站存的排课和本机解析器同一套字段
     * （本解析器就是 jwxt.normalize 的移植），照样逐条校验、和旧课表比出调课。
     */
    fun fromServer(server: JSONObject, previous: JSONObject? = null, nowMs: Long = System.currentTimeMillis()): JSONObject {
        val sid = integer(server.opt("semester_id")) ?: invalid("本站课表缺少学期 id")
        val name = scalarText(server, "semester_name").ifBlank { sid.toString() }
        val activities = readActivities(server.optJSONArray("activities") ?: invalid("本站课表缺少 activities 数组"))
        val student = JSONObject()
        server.optJSONObject("student")?.let { s ->
            for (key in listOf("name", "code")) scalarText(s, key).takeIf { it.isNotBlank() }?.let { student.put(key, it) }
        }
        val fetched = (server.opt("fetched_at") as? Number)?.toLong() ?: Math.floorDiv(nowMs, 1000)
        return assemble(sid, name, activities, student, date(server.optString("week1_monday")), previous,
            minOf(fetched, Math.floorDiv(nowMs, 1000)))
    }

    private fun assemble(
        sid: Int, name: String, activities: List<Activity>, student: JSONObject, sourceMonday: LocalDate?,
        previous: JSONObject?, now: Long,
    ): JSONObject {
        val sameSemester = previous?.let { integer(it.opt("semester_id")) == sid } == true
        val cachedMonday = if (sameSemester) date(previous.optString("week1_monday")) else null
        val monday = calendar[name] ?: sourceMonday ?: cachedMonday
            ?: invalid("学期 $name 的校历未知：缺少第 1 周周一，请获取学期详情")
        require(monday.dayOfWeek.value == 1) { "学期校历的第 1 周日期不是周一" }
        val history = if (sameSemester) recent(previous.optJSONArray("changes"), sid, now).toMutableList()
            else mutableListOf()
        if (sameSemester) {
            val oldMonday = cachedMonday ?: invalid("已有课表缺少有效校历，不能比较调课")
            val old = occurrences(readActivities(previous.optJSONArray("activities")
                ?: invalid("已有课表缺少 activities 数组")), oldMonday)
            val current = occurrences(activities, monday)
            val removed = old - current
            val added = current - old
            if (removed.isNotEmpty() || added.isNotEmpty()) {
                history += Change(now, removed.sortedWith(lessonOrder), added.sortedWith(lessonOrder))
            }
        }
        return JSONObject()
            .put("schema_version", 1)
            .put("semester_id", sid)
            .put("semester_name", name)
            .put("week1_monday", monday.toString())
            .put("activities", JSONArray(activities.map { it.json() }))
            .put("student", student)
            .put("updated_at", now)
            .put("changes", JSONArray(history.map { it.json(sid) }))
    }

    /** 后台刷新是否会把"当前学期有课"覆盖成"完全没课"（疑似教务未发布或会话半失效）。 */
    fun wouldWipeCurrentSemester(previous: JSONObject?, next: JSONObject): Boolean {
        val sid = integer(next.opt("semester_id")) ?: return false
        if (previous == null || integer(previous.opt("semester_id")) != sid) return false
        return (previous.optJSONArray("activities")?.length() ?: 0) > 0 &&
            (next.optJSONArray("activities")?.length() ?: 0) == 0
    }

    /** Reprojects the entire cached term at the supplied instant, including offline week rollover. */
    fun payload(
        timetable: JSONObject,
        nowMs: Long = System.currentTimeMillis(),
        status: String = "ok",
    ): Payload {
        val sid = integer(timetable.opt("semester_id")) ?: invalid("课表缺少学期 id")
        val monday = date(timetable.optString("week1_monday")) ?: invalid("课表缺少有效校历")
        require(monday.dayOfWeek.value == 1) { "学期校历的第 1 周日期不是周一" }
        val activities = readActivities(timetable.optJSONArray("activities") ?: invalid("课表缺少 activities 数组"))
        val now = Instant.ofEpochMilli(nowMs).atZone(zone).withNano(0)
        val today = now.toLocalDate()
        val all = occurrences(activities, monday)
        val byDate = all.groupBy { it.date }
        val colors = colors(activities.map { it.name })
        val changes = recent(timetable.optJSONArray("changes"), sid, Math.floorDiv(nowMs, 1000))
        val days = mutableMapOf<LocalDate, JSONObject>()
        fun day(target: LocalDate): JSONObject = days.getOrPut(target) {
            val week = Math.floorDiv(ChronoUnit.DAYS.between(monday, target), 7).toInt() + 1
            val current = byDate[target].orEmpty().filter { it.hasTime }.sortedWith(lessonOrder)
            val lessons = current.associateWith { it.lesson(colors) }
            val cancelled = markChanges(target, lessons, changes, colors)
            JSONObject().put("date", target.toString()).put("weekday", target.dayOfWeek.value)
                .put("week", if (week >= 1) week else JSONObject.NULL)
                .put("lessons", JSONArray(lessons.values.toList()))
                .put("cancelled", JSONArray(cancelled))
        }
        fun week(start: LocalDate): JSONObject {
            val block = (0L..6L).map { day(start.plusDays(it)) }
            return JSONObject().put("monday", start.toString()).put("week", block[0].opt("week"))
                .put("days", JSONArray(block))
        }
        val thisMonday = today.minusDays((today.dayOfWeek.value - 1).toLong())
        val thisWeek = week(thisMonday)
        val nextWeek = week(thisMonday.plusWeeks(1))
        // 全学期分周：周视图可左右滑到本周之前/下周之后的任意一周，至少覆盖到"下周"。
        val weekNo = { d: LocalDate -> Math.floorDiv(ChronoUnit.DAYS.between(monday, d), 7).toInt() + 1 }
        val thisWeekNo = weekNo(thisMonday)
        val lastWeek = maxOf(thisWeekNo + 1, all.maxOfOrNull { weekNo(it.date) } ?: thisWeekNo)
        val weeks = (0L until lastWeek.toLong()).map { w -> week(monday.plusWeeks(w)) }
        // 调课明细：从已投影的 lessons.changed/was 与 cancelled 里收集"今天及以后"的变动，供通知与详情卡展示。
        // 分类后：有 was 的算时间/地点移动；同名的"停课(旧日)+新增(新日)"合并成换日；落单的叫停课/新增。
        val todayStr = today.toString()
        data class Cand(val week: Any?, val date: String, val weekday: Int, val name: String, val lesson: JSONObject)
        val moved = mutableListOf<Cand>()
        val adds = mutableListOf<Cand>()
        val cancels = mutableListOf<Cand>()
        for (block in weeks) {
            val daysArr = block.optJSONArray("days") ?: continue
            for (i in 0 until daysArr.length()) {
                val d = daysArr.optJSONObject(i) ?: continue
                val dateStr = d.optString("date")
                if (dateStr < todayStr) continue
                val wk = if (d.isNull("week")) JSONObject.NULL else d.opt("week")
                val wd = d.optInt("weekday")
                d.optJSONArray("lessons")?.let { lessons ->
                    for (j in 0 until lessons.length()) {
                        val l = lessons.optJSONObject(j) ?: continue
                        if (!l.optBoolean("changed")) continue
                        val cand = Cand(wk, dateStr, wd, l.optString("name"), l)
                        if (l.optString("was").isEmpty()) adds += cand else moved += cand
                    }
                }
                d.optJSONArray("cancelled")?.let { cancelled ->
                    for (j in 0 until cancelled.length()) {
                        val c = cancelled.optJSONObject(j) ?: continue
                        cancels += Cand(wk, dateStr, wd, c.optString("name"), c)
                    }
                }
            }
        }
        fun label(c: Cand) = "周${WEEKDAY[c.weekday]} ${shortDate(c.date)}"
        fun item(c: Cand, detail: String, kind: String) = JSONObject()
            .put("week", c.week).put("date", c.date).put("name", c.name).put("detail", detail).put("kind", kind)
        val changeItems = mutableListOf<JSONObject>()
        val usedAdds = mutableSetOf<Int>()
        for (c in cancels) {
            val pair = adds.withIndex().firstOrNull { (idx, a) -> idx !in usedAdds && a.name == c.name }
            if (pair != null) {
                usedAdds += pair.index
                changeItems += item(pair.value, "${label(c)} → ${label(pair.value)}", "moved")
            } else {
                changeItems += item(c, "${label(c)} 停课", "cancelled")
            }
        }
        adds.forEachIndexed { idx, a -> if (idx !in usedAdds) changeItems += item(a, "${label(a)} 新增", "added") }
        moved.forEach { c -> changeItems += item(c, moveDetail(c.lesson), "moved") }
        val term = if (all.isEmpty()) JSONObject.NULL else JSONObject()
            .put("first_day", all.minOf { it.date }.toString())
            .put("last_day", all.maxOf { it.date }.toString())
        val upcoming = (1L..14L).asSequence().map { day(today.plusDays(it)) }
            .firstOrNull { it.getJSONArray("lessons").length() > 0 }
        return Payload.parse(JSONObject()
            .put("now", now.format(DateTimeFormatter.ISO_OFFSET_DATE_TIME))
            .put("semester", timetable.optString("semester_name"))
            .put("semester_id", sid).put("updated_at", timetable.opt("updated_at"))
            .put("status", status).put("has_calendar", true)
            .put("today", day(today)).put("tomorrow", day(today.plusDays(1)))
            .put("week", thisWeek).put("next_week", nextWeek).put("weeks", JSONArray(weeks)).put("term", term)
            .put("upcoming", upcoming ?: JSONObject.NULL)
            .put("changes", JSONArray(changeItems))
            .put("changed_at", changes.maxOfOrNull { it.at } ?: JSONObject.NULL)
            .put("refresh", JSONObject().put("pending", false).put("due", false))
            .put("delegate", false).put("poll_after", JSONObject.NULL).toString())
    }

    private data class Activity(
        val name: String, val teachers: List<String>, val weekday: Int, val startUnit: Int,
        val endUnit: Int, val weeks: List<Int>, val weeksText: String, val room: String,
        val building: String, val campus: String, val start: String, val end: String,
    ) {
        fun json() = JSONObject().put("courseName", name).put("teachers", JSONArray(teachers))
            .put("weekday", weekday).put("startUnit", startUnit).put("endUnit", endUnit)
            .put("weekIndexes", JSONArray(weeks)).put("weeksStr", weeksText)
            .put("room", room).put("building", building).put("campus", campus)
            .put("startTime", start).put("endTime", end)
    }

    private fun readActivities(array: JSONArray): List<Activity> = (0 until array.length()).mapNotNull { i ->
        val a = array.optJSONObject(i) ?: invalid("第 ${i + 1} 条排课不是对象")
        require(a.has("courseName")) { "第 ${i + 1} 条排课缺少 courseName" }
        val name = scalarText(a, "courseName")
        fun unit(key: String): Int? {
            if (a.isNull(key)) return null
            return integer(a.opt(key)) ?: invalid("排课 $key 不是整数")
        }
        val weekday = unit("weekday")
        val su = unit("startUnit")
        val eu = unit("endUnit")
        require(weekday == null || weekday in 0..7) { "排课星期越界" }
        require(su == null || su >= 0) { "排课节次越界" }
        require(eu == null || eu >= 0) { "排课节次越界" }
        // Explicitly unscheduled practice courses do not enter the week view, as in jwxt.normalize.
        // An arbitrary object must not silently become an empty successful timetable.
        require(a.has("weekday") && a.has("startUnit")) { "排课缺少时间结构" }
        if (weekday == null || weekday == 0 || su == null || su == 0) return@mapNotNull null
        val weeksText = scalarText(a, "weeksStr")
        val rawWeeks = a.opt("weekIndexes")
        require(rawWeeks == null || rawWeeks == JSONObject.NULL || rawWeeks is JSONArray) { "weekIndexes 不是数组" }
        val weeks = if (rawWeeks is JSONArray && rawWeeks.length() > 0) {
            (0 until rawWeeks.length()).map { w ->
                val n = integer(rawWeeks.opt(w)) ?: invalid("周次不是整数")
                require(n in 1..60) { "周次必须在 1–60 之间" }
                n
            }.distinct().sorted()
        } else if (weeksText.isNotBlank()) parseWeeks(weeksText) else {
            require(rawWeeks is JSONArray) { "排课缺少周次" }
            emptyList()
        }
        val teachers = a.opt("teachers")
        require(teachers == null || teachers == JSONObject.NULL || teachers is JSONArray) { "teachers 不是数组" }
        val names = if (teachers is JSONArray) (0 until teachers.length()).mapNotNull { index ->
            val value = teachers.opt(index)
            val text = when (value) {
                is JSONObject -> scalarText(value, "name").ifBlank { scalarText(value, "teacherName") }
                is String -> value
                null, JSONObject.NULL -> ""
                else -> invalid("教师格式异常")
            }
            text.substringBefore('(').substringBefore('（').trim().takeIf { it.isNotEmpty() }
        }.distinct().sorted() else emptyList()
        fun clock(key: String): String = scalarText(a, key).let { hm(it) ?: it }
        Activity(name, names, weekday, su, maxOf(eu ?: su, su), weeks, weeksText,
            scalarText(a, "room"), scalarText(a, "building"), scalarText(a, "campus"),
            clock("startTime"), clock("endTime"))
    }

    private fun parseWeeks(text: String): List<Int> {
        var s = text.replace(Regex("\\s+"), "").replace('（', '(').replace('）', ')')
            .replace('，', ',').replace('、', ',').replace('；', ',').replace(';', ',')
            .replace('～', '-').replace('~', '-').replace('—', '-').replace('–', '-')
            .replace("至", "-").replace("第", "").replace("周", "")
        s = s.replace(Regex("([单双])(?=,|$)"), "($1)")
        val qualifier = Regex("\\(([单双])\\)$")
        val suffix = qualifier.find(s)
        // A single final qualifier (1-8,10-16(单)) applies to the entire expression.
        val global = if (s.count { it == '(' } == 1) suffix?.groupValues?.get(1) else null
        if (global != null) s = s.substring(0, suffix!!.range.first)
        val weeks = sortedSetOf<Int>()
        for (part in s.split(',')) {
            val match = Regex("^(\\d+)(?:-(\\d+))?(?:\\(([单双])\\))?$").matchEntire(part)
                ?: invalid("无法识别周次范围")
            val start = match.groupValues[1].toIntOrNull() ?: invalid("周次越界")
            val endText = match.groupValues[2]
            val end = if (endText.isEmpty()) start else endText.toIntOrNull() ?: invalid("周次越界")
            require(start in 1..60 && end in start..60) { "周次范围必须在 1–60 之间" }
            val parity = match.groupValues[3].ifEmpty { global.orEmpty() }
            weeks += (start..end).filter { parity.isEmpty() || (it % 2 == if (parity == "单") 1 else 0) }
        }
        return weeks.toList()
    }

    private data class Occurrence(
        val date: LocalDate, val name: String, val start: String, val end: String,
        val startUnit: Int, val endUnit: Int, val room: String, val campus: String,
        val teachers: List<String>,
    ) {
        val hasTime get() = hm(start) != null && hm(end) != null && end > start
        private val location get() = listOf(campus, room).filter { it.isNotBlank() }.distinct().joinToString(" ")
        fun lesson(colors: Map<String, String>) = JSONObject()
            .put("name", name.ifEmpty { "未命名课程" }).put("start", start).put("end", end)
            .put("room", location)
            .put("units", if (startUnit == endUnit) "$startUnit" else "$startUnit–$endUnit")
            .put("teachers", teachers.joinToString("、")).put("color", colors[name] ?: palette[0])
        fun json() = JSONObject().put("date", date.toString()).put("courseName", name)
            .put("startTime", start).put("endTime", end).put("startUnit", startUnit).put("endUnit", endUnit)
            .put("room", room).put("campus", campus).put("teachers", JSONArray(teachers))
        fun was(old: Occurrence): String? = when {
            start != old.start -> "原 ${old.start}"
            end != old.end -> "原 ${old.start}–${old.end}"
            room != old.room || campus != old.campus -> "原 ${old.location.ifEmpty { "地点待定" }}"
            teachers != old.teachers -> "原教师 ${old.teachers.joinToString("、").ifEmpty { "待定" }}"
            startUnit != old.startUnit || endUnit != old.endUnit -> "原第 ${old.startUnit}–${old.endUnit} 节"
            else -> null
        }
    }

    private fun occurrences(activities: List<Activity>, monday: LocalDate): Set<Occurrence> {
        // Port of ics.unit_times: infer only the observed boundary of a unit, never invent a bell table.
        val starts = mutableMapOf<Int, String>()
        val ends = mutableMapOf<Int, String>()
        activities.forEach { a ->
            if (a.start.isNotEmpty()) starts.putIfAbsent(a.startUnit, a.start)
            if (a.end.isNotEmpty()) ends.putIfAbsent(a.endUnit, a.end)
        }
        return activities.flatMap { a ->
            val start = a.start.ifEmpty { starts[a.startUnit].orEmpty() }
            val end = a.end.ifEmpty { ends[a.endUnit].orEmpty() }
            a.weeks.map { w ->
                Occurrence(monday.plusWeeks((w - 1).toLong()).plusDays((a.weekday - 1).toLong()),
                    a.name, hm(start).orEmpty(), hm(end).orEmpty(), a.startUnit, a.endUnit,
                    a.room.ifEmpty { a.building }, a.campus, a.teachers)
            }
        }.toSet()
    }

    private data class Change(val at: Long, val removed: List<Occurrence>, val added: List<Occurrence>) {
        fun json(sid: Int) = JSONObject().put("semester_id", sid).put("at", at)
            .put("removed", JSONArray(removed.map { it.json() })).put("added", JSONArray(added.map { it.json() }))
    }

    private fun recent(array: JSONArray?, sid: Int, now: Long): List<Change> {
        if (array == null) return emptyList()
        return (0 until array.length()).mapNotNull { i ->
            val ev = array.optJSONObject(i) ?: invalid("调课历史格式异常")
            val at = (ev.opt("at") as? Number)?.toLong() ?: invalid("调课历史缺少时间")
            if (integer(ev.opt("semester_id")) != sid || at < now - HISTORY_SECONDS || at > now) return@mapNotNull null
            fun entries(key: String): List<Occurrence> {
                val entries = ev.optJSONArray(key) ?: invalid("调课历史缺少 $key")
                return (0 until entries.length()).map { j ->
                    val o = entries.optJSONObject(j) ?: invalid("调课历史排课格式异常")
                    val teachers = o.optJSONArray("teachers") ?: invalid("调课历史缺少教师数组")
                    Occurrence(date(o.optString("date")) ?: invalid("调课历史日期异常"),
                        scalarText(o, "courseName"), scalarText(o, "startTime"), scalarText(o, "endTime"),
                        integer(o.opt("startUnit")) ?: invalid("调课历史缺少节次"),
                        integer(o.opt("endUnit")) ?: invalid("调课历史缺少节次"),
                        scalarText(o, "room"), scalarText(o, "campus"),
                        (0 until teachers.length()).map { t -> teachers.getString(t) }.distinct().sorted())
                }
            }
            Change(at, entries("removed"), entries("added"))
        }.sortedBy { it.at }
    }

    private fun markChanges(
        target: LocalDate, lessons: Map<Occurrence, JSONObject>, changes: List<Change>, colors: Map<String, String>,
    ): List<JSONObject> {
        val handled = mutableSetOf<Occurrence>()
        val cancelled = linkedSetOf<Occurrence>()
        for (event in changes.asReversed()) {
            val old = event.removed.filter { it.date == target && it.hasTime }.toMutableList()
            val added = event.added.filter { it.date == target && it.hasTime }
            for (current in added) {
                val previous = old.filter { it.name == current.name }.minByOrNull {
                    // Prefer the same slot when a course has several lessons in one day.
                    kotlin.math.abs(it.startUnit.toLong() - current.startUnit)
                }
                if (previous != null) old.remove(previous)
                if (current !in handled) {
                    lessons[current]?.let { lesson ->
                        lesson.put("changed", true)
                        previous?.let { current.was(it) }?.let { lesson.put("was", it) }
                    }
                }
            }
            for (removed in old) {
                if (removed !in handled && removed !in lessons) cancelled += removed
            }
            handled += event.removed
            handled += event.added
        }
        // Removal and later re-addition may have arrived in separate refreshes.
        for ((current, lesson) in lessons) {
            if (!lesson.optBoolean("changed") || lesson.has("was")) continue
            val before = cancelled.firstOrNull { it.name == current.name } ?: continue
            current.was(before)?.let { lesson.put("was", it) }
            cancelled.remove(before)
        }
        return cancelled.sortedWith(lessonOrder).map { it.lesson(colors) }
    }

    /** 把 was（"原 X"）配上现在值，写成"旧 → 新"：时间/节次/教师/地点分别取对应新字段。 */
    private fun moveDetail(l: JSONObject): String {
        val old = l.optString("was").removePrefix("原").trim()
        val new = when {
            old.startsWith("教师") -> l.optString("teachers")
            old.startsWith("第") -> "第 ${l.optString("units")} 节"
            old.contains("–") -> "${l.optString("start")}–${l.optString("end")}"
            Regex("""\d{1,2}:\d{2}""").matches(old) -> l.optString("start")
            else -> l.optString("room")
        }
        return "$old → $new"
    }

    private fun colors(names: List<String>): Map<String, String> {
        val used = mutableSetOf<Int>()
        return names.distinct().sorted().associateWith { name ->
            var h = 2166136261L
            var i = 0
            while (i < name.length) {
                val cp = Character.codePointAt(name, i)
                h = ((h xor cp.toLong()) * 16777619L) and 0xffffffffL
                i += Character.charCount(cp)
            }
            var index = (h % palette.size).toInt()
            for (attempt in palette.indices) {
                if (index !in used) break
                index = (index + 1) % palette.size
            }
            used += index
            palette[index]
        }
    }

    private fun detailMonday(detail: JSONObject?): LocalDate? {
        if (detail == null) return null
        for (key in listOf("startDate", "beginDate", "startOn", "start")) {
            val value = detail.opt(key) as? String ?: continue
            val day = date(value.take(10)) ?: continue
            return day.minusDays((day.dayOfWeek.value - 1).toLong())
        }
        return null
    }

    private fun coursePage(html: String): Pair<Int, String> {
        require(html.length <= 1024 * 1024) { "课表页面过大" }
        require(Regex("\\bpersonId\\s*=\\s*[1-9]\\d*(?=\\s*[,;<\\r\\n]|\\s*$)").containsMatchIn(html)) {
            "课表页结构异常：未找到 personId / currentSemester"
        }
        val start = Regex("\\bcurrentSemester\\s*=\\s*\\{").find(html)?.range?.last
            ?: invalid("课表页结构异常：未找到 currentSemester")
        val values = objectScalars(html, start)
        val id = values["id"]?.toIntOrNull()?.takeIf { it > 0 }
            ?: invalid("课表页结构异常：currentSemester 缺少顶层 id")
        val select = Regex("<select\\b([^<>]*)>", RegexOption.IGNORE_CASE).findAll(html).firstOrNull {
            Regex("\\bid\\s*=\\s*(['\"])allSemesters\\1").containsMatchIn(it.groupValues[1])
        }
        if (select != null) {
            val end = html.indexOf("</select>", select.range.last + 1, ignoreCase = true)
            if (end >= 0) {
                val options = html.substring(select.range.last + 1, end)
                for (option in Regex("<option\\b([^<>]*)>([^<]*)</option>", RegexOption.IGNORE_CASE).findAll(options)) {
                    val value = Regex("\\bvalue\\s*=\\s*(['\"])(\\d+)\\1").find(option.groupValues[1])
                    if (value?.groupValues?.get(2)?.toIntOrNull() == id) {
                        val name = unescapeHtml(option.groupValues[2]).trim()
                        if (name.isNotEmpty()) return id to name
                    }
                }
            }
        }
        return id to values["name"].orEmpty().ifBlank { id.toString() }
    }

    /** Scan the complete JS object, respecting nested arrays, braces and escaped quotes. */
    private fun objectScalars(text: String, start: Int): Map<String, String> {
        val result = mutableMapOf<String, String>()
        var depth = 0
        var i = start
        while (i < text.length) {
            val ch = text[i]
            when {
                ch == '{' || ch == '[' -> { depth++; i++ }
                ch == '}' || ch == ']' -> {
                    depth--; i++
                    if (depth == 0) return result
                }
                ch == '\'' || ch == '"' || (depth == 1 && (ch.isLetter() || ch == '_')) -> {
                    val quoted = ch == '\'' || ch == '"'
                    val (key, after) = if (quoted) quoted(text, i) else {
                        var end = i + 1
                        while (end < text.length && (text[end].isLetterOrDigit() || text[end] == '_')) end++
                        text.substring(i, end) to end
                    }
                    i = after
                    if (depth != 1) continue
                    while (i < text.length && text[i].isWhitespace()) i++
                    if (i >= text.length || text[i] != ':') continue
                    i++
                    while (i < text.length && text[i].isWhitespace()) i++
                    if (i >= text.length) break
                    if (text[i] == '\'' || text[i] == '"') {
                        val (value, end) = quoted(text, i)
                        result[key] = value
                        i = end
                    } else if (text[i] != '{' && text[i] != '[') {
                        val from = i
                        while (i < text.length && text[i] != ',' && text[i] != '}') i++
                        result[key] = text.substring(from, i).trim()
                    }
                }
                else -> i++
            }
        }
        invalid("课表页 currentSemester 对象不完整")
    }

    private fun quoted(text: String, start: Int): Pair<String, Int> {
        val out = StringBuilder()
        var i = start + 1
        while (i < text.length) {
            val ch = text[i++]
            if (ch == text[start]) return out.toString() to i
            if (ch != '\\') { out.append(ch); continue }
            if (i >= text.length) break
            when (val escaped = text[i++]) {
                'u' -> {
                    if (i + 4 > text.length) invalid("课表页字符串转义异常")
                    val code = text.substring(i, i + 4).toIntOrNull(16) ?: invalid("课表页字符串转义异常")
                    out.append(code.toChar()); i += 4
                }
                'n' -> out.append('\n')
                'r' -> out.append('\r')
                't' -> out.append('\t')
                else -> out.append(escaped)
            }
        }
        invalid("课表页字符串不完整")
    }

    private fun unescapeHtml(text: String): String = Regex("&(#x[0-9a-fA-F]+|#\\d+|amp|quot|apos|lt|gt|nbsp);").replace(text) {
        val entity = it.groupValues[1]
        when (entity) {
            "amp" -> "&"; "quot" -> "\""; "apos" -> "'"; "lt" -> "<"; "gt" -> ">"; "nbsp" -> " "
            else -> {
                val code = if (entity.startsWith("#x")) entity.drop(2).toIntOrNull(16) else entity.drop(1).toIntOrNull()
                if (code != null && Character.isValidCodePoint(code)) String(Character.toChars(code)) else it.value
            }
        }
    }

    private fun jsonObject(text: String, label: String): JSONObject {
        require(text.length <= MAX_JSON) { "$label 过大" }
        try {
            validateJson(text)
            val tokens = JSONTokener(text)
            val value = tokens.nextValue()
            require(value is JSONObject && tokens.nextClean() == '\u0000') { "$label 不是完整 JSON 对象" }
            return value
        } catch (e: IllegalArgumentException) {
            throw e
        } catch (_: Exception) {
            invalid("$label JSON 格式异常")
        }
    }

    // Both Android's and the JVM org.json implementations accept JavaScript-like invalid JSON.
    // Validate the grammar first, so truncated / malformed responses cannot replace a good cache.
    private fun validateJson(text: String) {
        var i = 0
        fun space() { while (i < text.length && text[i] in " \r\n\t") i++ }
        fun string() {
            require(i < text.length && text[i++] == '"') { "JSON 属性必须使用双引号" }
            while (i < text.length) {
                val c = text[i++]
                if (c == '"') return
                require(c >= ' ') { "JSON 字符串包含控制字符" }
                if (c == '\\') {
                    require(i < text.length) { "JSON 转义不完整" }
                    val escaped = text[i++]
                    if (escaped == 'u') {
                        require(i + 4 <= text.length && text.substring(i, i + 4).all { it in "0123456789abcdefABCDEF" }) {
                            "JSON Unicode 转义不完整"
                        }
                        i += 4
                    } else require(escaped in "\"\\/bfnrt") { "JSON 转义无效" }
                }
            }
            invalid("JSON 字符串不完整")
        }
        fun value(depth: Int) {
            require(depth <= 64) { "JSON 嵌套过深" }
            space()
            require(i < text.length) { "JSON 数据不完整" }
            when (val c = text[i]) {
                '"' -> string()
                '{', '[' -> {
                    i++
                    val close = if (c == '{') '}' else ']'
                    space()
                    if (i < text.length && text[i] == close) { i++; return }
                    while (true) {
                        if (c == '{') {
                            space(); string(); space()
                            require(i < text.length && text[i++] == ':') { "JSON 缺少冒号" }
                        }
                        value(depth + 1); space()
                        require(i < text.length) { "JSON 数据不完整" }
                        if (text[i] == close) { i++; break }
                        require(text[i++] == ',') { "JSON 缺少逗号" }
                    }
                }
                't', 'f', 'n' -> {
                    val literal = when (c) { 't' -> "true"; 'f' -> "false"; else -> "null" }
                    require(text.startsWith(literal, i)) { "JSON 值无效" }
                    i += literal.length
                }
                else -> {
                    val from = i
                    while (i < text.length && text[i] in "0123456789.eE+-") i++
                    require(Regex("-?(0|[1-9][0-9]*)(\\.[0-9]+)?([eE][+-]?[0-9]+)?").matches(text.substring(from, i))) {
                        "JSON 数字无效"
                    }
                }
            }
        }
        value(0); space()
        require(i == text.length) { "JSON 尾部包含额外内容" }
    }

    private fun scalarText(o: JSONObject, key: String): String {
        val value = o.opt(key)
        if (value == null || value == JSONObject.NULL) return ""
        require(value is String || value is Number) { "$key 不是文本" }
        return value.toString().trim()
    }

    private fun integer(value: Any?): Int? = when (value) {
        is Number -> value.toString().toIntOrNull()
        is String -> value.trim().toIntOrNull()
        else -> null
    }

    private fun date(value: String): LocalDate? = try { LocalDate.parse(value) } catch (_: Exception) { null }

    private fun hm(value: String): String? {
        val parts = value.split(':')
        if (parts.size < 2) return null
        val h = parts[0].trim().toIntOrNull() ?: return null
        val m = parts[1].trim().toIntOrNull() ?: return null
        if (h !in 0..23 || m !in 0..59) return null
        return LocalTime.of(h, m).format(DateTimeFormatter.ofPattern("HH:mm"))
    }

    private fun invalid(message: String): Nothing = throw IllegalArgumentException(message)
}
