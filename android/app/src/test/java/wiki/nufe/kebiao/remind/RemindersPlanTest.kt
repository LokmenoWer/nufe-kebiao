package wiki.nufe.kebiao.remind

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import wiki.nufe.kebiao.data.Day
import wiki.nufe.kebiao.data.Lesson
import wiki.nufe.kebiao.data.Now
import wiki.nufe.kebiao.data.Payload
import wiki.nufe.kebiao.data.Refresh
import wiki.nufe.kebiao.data.lessonMs

class RemindersPlanTest {
    private val date = "2026-11-03"
    private val lead = 10
    private val remindAt = lessonMs(date, "18:30") - lead * 60_000L

    private val payload = Payload(
        now = null,
        semester = null,
        semesterId = null,
        updatedAt = null,
        status = "ok",
        today = Day(
            date = date,
            weekday = 2,
            week = 9,
            lessons = listOf(
                Lesson(
                    name = "测试课",
                    start = "18:30",
                    end = "20:50",
                    room = "A101",
                    units = "11–13",
                    teachers = "T",
                    color = "#4b63e0",
                    changed = false,
                    was = null,
                ),
            ),
            cancelled = emptyList(),
        ),
        tomorrow = null,
        week = null,
        nextWeek = null,
        term = null,
        upcoming = null,
        changedAt = null,
        refresh = Refresh(false, false),
        delegate = false,
        pollAfterSeconds = null,
    )

    private fun plan(nowMs: Long) = Reminders.plan(payload, Now(nowMs), lead)

    @Test
    fun keepsReminderThatIsAboutToFire() {
        assertEquals(listOf(remindAt), plan(remindAt - 30_000L).map { it.atMs })
    }

    @Test
    fun dropsReminderThatIsDueOrPast() {
        assertTrue(plan(remindAt).isEmpty())
        assertTrue(plan(remindAt + 1_000L).isEmpty())
    }
}
