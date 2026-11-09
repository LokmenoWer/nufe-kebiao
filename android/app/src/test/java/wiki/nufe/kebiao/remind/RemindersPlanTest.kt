package wiki.nufe.kebiao.remind

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import wiki.nufe.kebiao.data.Day
import wiki.nufe.kebiao.data.Lesson
import wiki.nufe.kebiao.data.LiveCapsuleTextMode
import wiki.nufe.kebiao.data.LiveDismissMode
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

    @Test
    fun oemIslandExtrasResolveDismissAndFormatCapsuleText() {
        val startMs = 1_000_000L
        val endMs = startMs + 45 * 60_000L
        assertEquals(
            startMs,
            OemIslandExtras.resolveDismissMs(startMs, endMs, LiveDismissMode.AT_CLASS_START),
        )
        assertEquals(
            startMs + 5 * 60_000L,
            OemIslandExtras.resolveDismissMs(startMs, endMs, LiveDismissMode.FIVE_MIN_AFTER_START),
        )
        assertEquals(
            endMs,
            OemIslandExtras.resolveDismissMs(startMs, endMs, LiveDismissMode.UNTIL_CLASS_END),
        )

        assertEquals("博远JC406", OemIslandExtras.compactRoom("博远楼JC406"))
        assertEquals("德济202", OemIslandExtras.compactRoom("德济楼 202"))

        val sampleItem = Reminders.Item(
            atMs = startMs,
            title = "10 分钟后：高等数学",
            text = "10:00–11:25 · 博远楼JC406",
            date = "2026-11-03",
            start = "10:00",
            end = "11:25",
            name = "高等数学（上）",
            room = "博远楼JC406",
        )
        assertEquals(
            "剩 10 分钟",
            OemIslandExtras.formatCapsuleShortText(
                item = sampleItem,
                isBeforeClass = true,
                remainMinutes = 10,
                textMode = LiveCapsuleTextMode.COUNTDOWN,
            ),
        )
        assertEquals(
            "博远JC406",
            OemIslandExtras.formatCapsuleShortText(
                item = sampleItem,
                isBeforeClass = true,
                remainMinutes = 10,
                textMode = LiveCapsuleTextMode.ROOM_FIRST,
            ),
        )
        assertEquals(
            "高等数学（",
            OemIslandExtras.formatCapsuleShortText(
                item = sampleItem,
                isBeforeClass = true,
                remainMinutes = 10,
                textMode = LiveCapsuleTextMode.COURSE_SHORT,
            ),
        )
    }

    @Test
    fun oemIslandExtrasBuildValidMiuiFocusParamJson() {
        val json = OemIslandExtras.buildMiuiFocusParamJson(
            course = "算法设计与分析",
            room = "博远楼JC406",
            timeRange = "10:00–11:25",
            meta = "博远楼JC406 · 10:00–11:25",
            status = "10 分钟后上课",
            shortText = "剩 10 分钟",
            progress = 50,
            showProgress = true,
        )
        val parsed = JSONObject(json)
        val v2 = parsed.getJSONObject("param_v2")
        assertEquals(1, v2.getInt("protocol"))
        assertEquals("schedule", v2.getString("business"))
        assertTrue(v2.getBoolean("updatable"))
        assertEquals(
            "剩 10 分钟",
            v2.getJSONObject("param_island")
                .getJSONObject("smallIslandArea")
                .getJSONObject("textInfo")
                .getString("title"),
        )
        assertEquals(
            50,
            v2.getJSONObject("param_island")
                .getJSONObject("progressInfo")
                .getInt("progress"),
        )
    }
}
