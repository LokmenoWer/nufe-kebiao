package wiki.nufe.kebiao.data

import org.junit.Assert.assertEquals
import org.junit.Test

class WeekRoutingTest {
    private fun lesson(start: String, end: String) = Lesson(
        name = "课", start = start, end = end, room = "A101", units = "1", teachers = "T",
        color = "#4b63e0", changed = false, was = null,
    )
    private fun day(date: String, weekday: Int, lessons: List<Lesson>) =
        Day(date = date, weekday = weekday, week = 9, lessons = lessons, cancelled = emptyList())

    // 2026-11-06 是周五，第 9 周；周一 11/02 起，下周周一 11/09。
    // week 的周五必须与 today 携带同一批课，weekToShow 读的是 week.days。
    private fun weekWith(fridayLessons: List<Lesson>) = WeekBlock(week = 9, monday = "2026-11-02", days = listOf(
        day("2026-11-02", 1, emptyList()), day("2026-11-03", 2, emptyList()),
        day("2026-11-04", 3, emptyList()), day("2026-11-05", 4, emptyList()),
        day("2026-11-06", 5, fridayLessons), day("2026-11-07", 6, emptyList()),
        day("2026-11-08", 7, emptyList()),
    ))
    private val nextWeek = WeekBlock(week = 10, monday = "2026-11-09", days = listOf(
        day("2026-11-09", 1, listOf(lesson("08:00", "09:25"))),
    ))

    private fun payload(todayLessons: List<Lesson>) = Payload(
        now = null, semester = null, semesterId = null, updatedAt = null, status = "ok",
        today = day("2026-11-06", 5, todayLessons), tomorrow = day("2026-11-07", 6, emptyList()),
        week = weekWith(todayLessons), nextWeek = nextWeek, term = null, upcoming = null, changedAt = null,
        refresh = Refresh(false, false), delegate = false, pollAfterSeconds = null,
    )

    private fun now(hm: String) = Now(lessonMs("2026-11-06", hm))

    @Test fun routesToNextWeekWhenTodayClassesHaveAllEnded() {
        // 今天 08:00–09:25 的课，12:40 时已结束 → 本周无剩余课 → 下周
        assertEquals("下周", weekToShow(payload(listOf(lesson("08:00", "09:25"))), now("12:40")).first)
    }

    @Test fun staysOnThisWeekWhileTodayStillHasUpcomingClass() {
        // 今天 14:00 的课尚未结束 → 仍看本周
        assertEquals("本周", weekToShow(payload(listOf(lesson("14:00", "15:25"))), now("12:40")).first)
    }

    @Test fun staysOnThisWeekWhenNextWeekHasNoClass() {
        val p = payload(emptyList()).copy(nextWeek = nextWeek.copy(days = emptyList()))
        assertEquals("本周", weekToShow(p, now("12:40")).first)
    }
}
