package wiki.nufe.kebiao.sync

import org.junit.Assert.assertEquals
import org.junit.Test
import wiki.nufe.kebiao.data.Day
import wiki.nufe.kebiao.data.Lesson
import wiki.nufe.kebiao.data.Now
import wiki.nufe.kebiao.data.Payload
import wiki.nufe.kebiao.data.Refresh
import wiki.nufe.kebiao.data.lessonMs

class SyncScheduleTest {
    private val date = "2026-11-03"

    private fun payload(
        delegate: Boolean = false,
        pollAfter: Long? = null,
        refresh: Refresh = Refresh(false, false),
    ) = Payload(
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
        refresh = refresh,
        delegate = delegate,
        pollAfterSeconds = pollAfter,
    )

    private fun now(hm: String) = Now(lessonMs(date, hm))

    @Test
    fun capsRedrawAtThirtyMinutes() {
        val next = Sync.nextRedrawMs(payload(), now("13:00"))
        assertEquals(lessonMs(date, "13:30"), next)
    }

    @Test
    fun delegateWakesThirtySevenMinutesBeforeClass() {
        val next = Sync.nextRedrawMs(payload(delegate = true), now("17:30"))
        assertEquals(lessonMs(date, "17:53"), next)
    }

    @Test
    fun nonDelegateDoesNotUsePreClassDelegateCheckpoint() {
        val next = Sync.nextRedrawMs(payload(), now("17:30"))
        assertEquals(lessonMs(date, "18:00"), next)
    }

    @Test
    fun pollAfterOnlyAppliesToDelegatedDevices() {
        val delegated = Sync.nextRedrawMs(payload(delegate = true, pollAfter = 600), now("13:00"))
        val serverOnly = Sync.nextRedrawMs(payload(delegate = false, pollAfter = 600), now("13:00"))
        assertEquals(lessonMs(date, "13:10"), delegated)
        assertEquals(lessonMs(date, "13:30"), serverOnly)
    }

    @Test
    fun pendingRefreshIsCheckedAgainInTwoMinutes() {
        val next = Sync.nextRedrawMs(
            payload(delegate = false, refresh = Refresh(pending = true, due = false)),
            now("13:00"),
        )
        assertEquals(lessonMs(date, "13:02"), next)
    }

    @Test
    fun alignsExactClassStartWhenSecondsAreNonZero() {
        val twentySecondsBefore = Now(lessonMs(date, "18:29") + 40_000L)
        val next = Sync.nextRedrawMs(payload(), twentySecondsBefore)
        assertEquals(lessonMs(date, "18:30"), next)
    }
}
