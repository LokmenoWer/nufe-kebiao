package wiki.nufe.kebiao.data

import java.time.Instant
import java.util.TimeZone
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class TimetableParserTest {
    private fun fixture(name: String) = checkNotNull(javaClass.getResourceAsStream("/timetable/$name"))
        .bufferedReader(Charsets.UTF_8).use { it.readText() }
    private val html get() = fixture("course.html")
    private fun ms(time: String = "2026-09-07T07:00:00+08:00") = Instant.parse(
        java.time.OffsetDateTime.parse(time).toInstant().toString(),
    ).toEpochMilli()
    private fun activity(
        name: String = "测试课程", day: Int = 1, weeks: List<Int> = listOf(1, 2, 3),
        room: String = "A101", start: String = "08:20", end: String = "09:45",
        teacher: String = "甲老师", su: Int = 1, eu: Int = 2,
    ) = JSONObject().put("courseName", name).put("teachers", JSONArray(listOf(teacher)))
        .put("weekday", day).put("startUnit", su).put("endUnit", eu)
        .put("weekIndexes", JSONArray(weeks)).put("room", room)
        .put("startTime", start).put("endTime", end)
    private fun print(vararg activities: JSONObject) = JSONObject().put("studentTableVms", JSONArray().put(
        JSONObject().put("name", "合成同学").put("code", "SYNTHETIC-002")
            .put("activities", JSONArray(activities.toList())),
    )).toString()
    private fun parse(vararg activities: JSONObject, previous: JSONObject? = null, now: Long = ms()) =
        TimetableParser.parse(html, print(*activities), null, previous, now)
    private fun projected(t: JSONObject, time: String = "2026-09-07T07:00:00+08:00") =
        TimetableParser.payload(t, ms(time))
    private fun assertInvalid(message: String? = null, block: () -> Unit) {
        try {
            block()
            fail("Expected IllegalArgumentException")
        } catch (e: IllegalArgumentException) {
            if (message != null) assertTrue("Unexpected message: ${e.message}", e.message.orEmpty().contains(message))
        }
    }
    private fun weeks(t: JSONObject): List<Int> = t.getJSONArray("activities").getJSONObject(0)
        .getJSONArray("weekIndexes").let { a -> (0 until a.length()).map { a.getInt(it) } }

    @Test fun parsesSyntheticFixtureAndNeverPersistsRawIdentity() {
        val t = TimetableParser.parse(html, fixture("print-data.json"), fixture("detail.json"), nowMs = ms())
        assertEquals(901, TimetableParser.semesterId(html))
        assertEquals(901, t.getInt("semester_id"))
        assertEquals("2026-2027-1", t.getString("semester_name"))
        assertEquals("2026-09-07", t.getString("week1_monday"))
        assertEquals(ms() / 1000, t.getLong("updated_at"))
        assertEquals(0, t.getJSONArray("changes").length())
        assertEquals(setOf("name", "code"), t.getJSONObject("student").keys().asSequence().toSet())
        assertEquals("测试同学", t.getJSONObject("student").getString("name"))
        assertEquals(2, t.getJSONArray("activities").length())
        assertEquals(listOf(1, 2, 3), weeks(t))
        val stored = t.toString()
        for (secret in listOf("person_id", "personId", "rawStudent", "studentName", "department", "900001", "700001", "合成工号")) {
            assertFalse(secret, stored.contains(secret))
        }
        val p = projected(t)
        val lesson = p.today!!.lessons.single()
        assertEquals("08:20", lesson.start)
        assertEquals("09:45", lesson.end)
        assertEquals("1–2", lesson.units)
        assertEquals(setOf("甲老师", "乙老师"), lesson.teachers.split("、").toSet())
        assertEquals("仙林 A101", lesson.room)
        assertEquals("红山 B楼", p.tomorrow!!.lessons.single().room)
        assertEquals("08:20", p.tomorrow.lessons.single().start)
        assertFalse(lesson.changed)
        assertNull(p.changedAt)
        assertFalse(p.delegate)
        assertFalse(p.refresh.pending)
        assertFalse(p.refresh.due)
        assertNull(p.pollAfterSeconds)
    }

    @Test fun readsOnlyCompleteTopLevelSemesterIdWithNestedObjectsArraysAndQuotes() {
        assertEquals(901, TimetableParser.semesterId(html))
        assertEquals(77, TimetableParser.semesterId("var personId=1; var currentSemester={calendarAssoc:{id:9}, id:77};"))
        assertEquals(77, TimetableParser.semesterId("var personId=1; var currentSemester={\"x\":[{\"id\":9}],\"id\":77};"))
        assertInvalid { TimetableParser.semesterId("var personId=1;var currentSemester={'calendarAssoc':{'id':9}};") }
        assertInvalid { TimetableParser.semesterId("var personId=1;var currentSemester={'id':77,") }
        assertInvalid { TimetableParser.semesterId("var mypersonId=1;var mycurrentSemester={'id':77};") }
        assertInvalid { TimetableParser.semesterId("<html>统一身份认证</html>") }
        assertInvalid { TimetableParser.semesterId("var personId=1;var currentSemester={'id':-1};") }
        assertInvalid { TimetableParser.semesterId("var personId=1;var currentSemester={'id':999999999999999999};") }
    }

    @Test fun decodesSelectedSemesterOptionAndIgnoresOutsideOptions() {
        val page = """var personId=1;var currentSemester={'id':22};
            <option value="22">错误</option>
            <select id="allSemesters"><option value='22'> 测试&amp;&#x5B66;&#26399; </option></select>"""
        val t = TimetableParser.parse(page, print(), """{"beginDate":"2030-02-06"}""")
        assertEquals("测试&学期", t.getString("semester_name"))
        assertEquals("2030-02-04", t.getString("week1_monday"))
    }

    @Test fun knownCalendarOverridesSchoolStartDate() {
        val t = TimetableParser.parse(html, print(), """{"startDate":"2026-08-01"}""")
        assertEquals("2026-09-07", t.getString("week1_monday"))
    }

    @Test fun unknownCalendarNeedsExplicitTopLevelDateAndCanReuseSameSemesterCache() {
        val unknown = "var personId=1;var currentSemester={'id':902,'name':'2030-2031-1'};"
        assertInvalid("校历未知") { TimetableParser.parse(unknown, print(), null) }
        assertInvalid("校历未知") { TimetableParser.parse(unknown, print(), """{"calendarAssoc":{"startDate":"2030-09-02"}}""") }
        assertInvalid("校历未知") { TimetableParser.parse(unknown, print(), """{"startDate":"2030-02-30"}""") }
        for (key in listOf("startDate", "beginDate", "startOn", "start")) {
            val t = TimetableParser.parse(unknown, print(activity()), """{"$key":"2030-09-04T00:00:00"}""", nowMs = ms())
            assertEquals("2030-09-02", t.getString("week1_monday"))
            assertEquals("2030-09-02", TimetableParser.parse(unknown, print(activity()), null, t, ms()).getString("week1_monday"))
        }
        assertInvalid("校历未知") { TimetableParser.parse(unknown, print(), null, parse(activity())) }
        assertInvalid("不一致") { TimetableParser.parse(html, print(), """{"id":902,"startDate":"2030-09-02"}""") }
    }

    @Test fun normalizesWeekRangesParityAndContinuousUnits() {
        val examples = mapOf(
            "第1～6周（单周）" to listOf(1, 3, 5),
            "1-6(双)" to listOf(2, 4, 6),
            "1-6双周" to listOf(2, 4, 6),
            "1-3,5,7至8" to listOf(1, 2, 3, 5, 7, 8),
            "1-4(单),5-8(双)" to listOf(1, 3, 6, 8),
            "1-4，6-8（双）" to listOf(2, 4, 6, 8),
            "第 1、3、60 周" to listOf(1, 3, 60),
        )
        for ((text, expected) in examples) {
            val a = activity(su = 3, eu = 5).apply { remove("weekIndexes"); put("weeksStr", text) }
            val t = parse(a)
            assertEquals(text, expected, weeks(t))
            if (1 in expected) assertEquals("3–5", projected(t).today!!.lessons.single().units)
        }
        val authoritative = activity(weeks = listOf(2, 2, 4)).put("weeksStr", "1-3(单)")
        assertEquals(listOf(2, 4), weeks(parse(authoritative)))
        assertEquals("3", projected(parse(activity(su = 3, eu = 1))).today!!.lessons.single().units)
    }

    @Test fun rejectsInvalidWeekAndActivityStructuresInsteadOfDroppingLessons() {
        for (text in listOf("0-3", "2-61", "5-2", "1-99999999999999", "1-3(未知)", "1,,2", "不是周次")) {
            assertInvalid { parse(activity().apply { remove("weekIndexes"); put("weeksStr", text) }) }
        }
        for (bad in listOf(0, 61, -1, "x", 1.2)) {
            assertInvalid { parse(activity().put("weekIndexes", JSONArray(listOf(bad)))) }
        }
        for (a in listOf(
            JSONObject(), activity(day = 8), activity(su = -1), activity().put("weekday", "x"),
            activity().put("weekIndexes", "1-3"), activity().put("teachers", "甲老师"),
            activity().put("room", JSONObject()), activity().apply { remove("weekday") },
            activity().apply { remove("startUnit") }, activity().apply { remove("weekIndexes") },
        )) assertInvalid { parse(a) }
    }

    @Test fun acceptsExplicitEmptyTimetablesButRejectsMalformedResponsesWithoutMutatingPrevious() {
        val old = parse(activity())
        val before = old.toString()
        for (bad in listOf(
            "{}", "[]", "null", "<html>login</html>", "{", "{\"studentTableVms\":null}",
            "{\"studentTableVms\":{}}", "{\"studentTableVms\":[null]}", "{\"studentTableVms\":[{}]}",
            "{\"studentTableVms\":[{\"activities\":null}]}", "{\"studentTableVms\":[{\"activities\":[null]}]}",
            "{\"studentTableVms\":[]} garbage", "{studentTableVms:[]}", "{\"studentTableVms\":[],}",
            "{\"studentTableVms\":[,]}", "{\"studentTableVms\":[],\"success\":false}",
            "{\"studentTableVms\":[],\"error\":\"session expired\"}",
            "{\"studentTableVms\":[],\"error\":{\"message\":\"expired\"}}",
        )) {
            assertInvalid { TimetableParser.parse(html, bad, null, old, ms()) }
            assertEquals(before, old.toString())
        }
        val empty = TimetableParser.parse(html, "{\"studentTableVms\":[]}", null, old, ms())
        assertEquals(0, empty.getJSONArray("activities").length())
        assertEquals(1, projected(empty).today!!.cancelled.size)
        assertNull(projected(empty).term)
        val first = parse()
        assertNull(projected(first).changedAt)
        assertTrue(projected(first).today!!.lessons.isEmpty())
    }

    @Test fun wouldWipeCurrentSemesterOnlyForSameSemesterNonEmptyToEmpty() {
        val nonEmpty = parse(activity())
        val empty = parse()
        assertTrue(TimetableParser.wouldWipeCurrentSemester(nonEmpty, empty))
        assertFalse(TimetableParser.wouldWipeCurrentSemester(nonEmpty, nonEmpty))
        assertFalse(TimetableParser.wouldWipeCurrentSemester(empty, empty))
        assertFalse(TimetableParser.wouldWipeCurrentSemester(null, empty))
        val other = JSONObject(nonEmpty.toString()).put("semester_id", 902)
        assertFalse(TimetableParser.wouldWipeCurrentSemester(other, empty))
        assertFalse(TimetableParser.wouldWipeCurrentSemester(nonEmpty, JSONObject()))
    }

    @Test fun rejectsOversizedAndDeeplyNestedData() {
        assertInvalid("过大") { TimetableParser.semesterId("x".repeat(1024 * 1024 + 1)) }
        assertInvalid("过大") { TimetableParser.parse(html, "x".repeat(2 * 1024 * 1024 + 1), null) }
        assertInvalid("嵌套") { TimetableParser.parse(html, "{\"studentTableVms\":[],\"x\":" + "[".repeat(70) + "0" + "]".repeat(70) + "}", null) }
    }

    @Test fun onlyInfersObservedUnitBoundariesAndNeverInventsTimes() {
        val noTime = activity("缺时间", day = 2, start = "", end = "")
        val t = parse(activity(), noTime)
        assertEquals("08:20", projected(t).tomorrow!!.lessons.single().start)
        assertTrue(projected(parse(noTime)).tomorrow!!.lessons.isEmpty())
        assertEquals("2026-09-22", projected(parse(noTime)).term!!.lastDay)
        val otherUnits = activity("其他节次", day = 2, su = 3, eu = 4, start = "", end = "")
        assertTrue(projected(parse(activity(), otherUnits)).tomorrow!!.lessons.isEmpty())
        assertTrue(projected(parse(activity(start = "25:00"))).today!!.lessons.isEmpty())
        assertTrue(projected(parse(activity(start = "09:45", end = "09:45"))).today!!.lessons.isEmpty())
        assertTrue(projected(parse(activity(start = "23:00", end = "00:30"))).today!!.lessons.isEmpty())
    }

    @Test fun independentlyInfersStartAndEndWithoutSplittingContinuousLessons() {
        val first = activity("第一节", su = 1, eu = 1, start = "08:20", end = "09:00")
        val last = activity("第三节", su = 3, eu = 3, start = "10:00", end = "10:40")
        val merged = activity("连读", day = 2, su = 1, eu = 3, start = "", end = "")
        val lesson = projected(parse(first, last, merged)).tomorrow!!.lessons.single()
        assertEquals("1–3", lesson.units)
        assertEquals("08:20", lesson.start)
        assertEquals("10:40", lesson.end)
    }

    @Test fun reprojectsAcrossShanghaiMidnightEvenWhenDeviceTimezoneDiffers() {
        val original = TimeZone.getDefault()
        try {
            TimeZone.setDefault(TimeZone.getTimeZone("America/Los_Angeles"))
            val t = parse(activity(), activity("周二课程", day = 2))
            val before = TimetableParser.payload(t, Instant.parse("2026-09-07T15:59:59Z").toEpochMilli())
            val after = TimetableParser.payload(t, Instant.parse("2026-09-07T16:00:00Z").toEpochMilli(), "offline")
            assertEquals("2026-09-07", before.today!!.date)
            assertEquals("2026-09-08", after.today!!.date)
            assertEquals("周二课程", after.today.lessons.single().name)
            assertEquals("offline", after.status)
            assertTrue(after.now!!.endsWith("+08:00"))
            assertEquals(t.getLong("updated_at"), after.updatedAt)
        } finally { TimeZone.setDefault(original) }
    }

    @Test fun sundayRolloverAndOfflineWeekProjectionUseCachedWholeSemester() {
        val t = parse(activity())
        val sunday = projected(t, "2026-09-13T23:59:59+08:00")
        val monday = projected(t, "2026-09-14T00:00:00+08:00")
        assertEquals(1, sunday.week!!.week)
        assertEquals(2, sunday.nextWeek!!.week)
        assertEquals(2, sunday.tomorrow!!.week)
        assertEquals("2026-09-14", monday.week!!.monday)
        assertEquals(2, monday.today!!.week)
        assertEquals(1, monday.today.lessons.size)
        assertEquals(3, monday.nextWeek!!.week)
        assertEquals(7, monday.week.days.size)
        assertEquals("2026-09-15", monday.tomorrow!!.date)
    }

    @Test fun termBoundsUpcomingAndBeforeAfterSemesterFollowWidgetSemantics() {
        val t = parse(activity(weeks = listOf(1, 3)))
        val before = projected(t, "2026-09-06T12:00:00+08:00")
        assertNull(before.today!!.week)
        assertTrue(before.today.lessons.isEmpty())
        assertEquals("2026-09-07", before.upcoming!!.date)
        assertEquals("2026-09-07", before.term!!.firstDay)
        assertEquals("2026-09-21", before.term.lastDay)
        assertNull(projected(t, "2026-08-23T12:00:00+08:00").upcoming)
        assertEquals("2026-09-07", projected(t, "2026-08-24T12:00:00+08:00").upcoming!!.date)
        assertEquals("2026-09-21", projected(t).upcoming!!.date) // excludes today; includes day 14
        val after = projected(t, "2026-09-22T12:00:00+08:00")
        assertTrue(after.today!!.lessons.isEmpty())
        assertNull(after.upcoming)
        assertEquals("2026-09-21", after.term!!.lastDay)
    }

    @Test fun newSemesterDoesNotReusePreviousCalendarOrProduceChanges() {
        val old = parse(activity())
        val page = "var personId=1;var currentSemester={'id':902,'name':'2030-2031-1'};"
        val t = TimetableParser.parse(page, print(activity(room = "Z999")), """{"startDate":"2030-09-02"}""", old, ms())
        assertEquals(0, t.getJSONArray("changes").length())
        assertEquals("2030-09-02", projected(t).term!!.firstDay)
        assertNull(projected(t).changedAt)
    }

    @Test fun detectsRoomTeacherCampusTimeAndCancellationWithSecondsTimestamp() {
        val initial = parse(activity())
        val at = ms() + 120_000
        val variants = listOf(
            activity(room = "B202") to "原 A101",
            activity(teacher = "乙老师") to "原教师 甲老师",
            activity().put("campus", "红山") to "原 A101",
            activity(start = "10:00", end = "11:25") to "原 08:20",
            activity(end = "10:00") to "原 08:20–09:45",
        )
        for ((a, expected) in variants) {
            val t = parse(a, previous = initial, now = at)
            val p = TimetableParser.payload(t, at)
            val lesson = p.today!!.lessons.single()
            assertTrue(lesson.changed)
            assertEquals(expected, lesson.was)
            assertEquals(at / 1000, p.changedAt)
            assertTrue(p.today.cancelled.isEmpty())
        }
        val stopped = parse(previous = initial, now = at)
        val day = TimetableParser.payload(stopped, at).today!!
        assertTrue(day.lessons.isEmpty())
        assertEquals("测试课程", day.cancelled.single().name)
        assertEquals("A101", day.cancelled.single().room)
    }

    @Test fun splitWeeksOnlyMarksTheChangedConcreteDate() {
        val old = parse(activity(weeks = (1..16).toList()))
        val at = ms() + 60_000
        val t = parse(activity(weeks = (1..16).filter { it != 9 }), activity(weeks = listOf(9), room = "B202"), previous = old, now = at)
        val ev = t.getJSONArray("changes").getJSONObject(0)
        assertEquals(1, ev.getJSONArray("removed").length())
        assertEquals(1, ev.getJSONArray("added").length())
        assertEquals("2026-11-02", ev.getJSONArray("added").getJSONObject(0).getString("date"))
        assertFalse(TimetableParser.payload(t, at).today!!.lessons.single().changed)
        // Diff discovered close to the affected date is visible there, not on the unchanged weeks.
        val november = ms("2026-11-02T07:00:00+08:00")
        val refreshed = parse(activity(weeks = (1..16).filter { it != 9 }), activity(weeks = listOf(9), room = "B202"), previous = old, now = november)
        assertTrue(TimetableParser.payload(refreshed, november).today!!.lessons.single().changed)
        assertFalse(TimetableParser.payload(refreshed, november).nextWeek!!.days[0].lessons.single().changed)
    }

    @Test fun splittingMergingReorderingAndTeacherMetadataDoNotProduceFalseChanges() {
        val old = parse(activity(weeks = listOf(1, 2, 3, 4)), activity("另一门课", day = 2))
        val reordered = parse(
            activity("另一门课", day = 2),
            activity(weeks = listOf(4, 3, 3)).put("weeksStr", "3~4"),
            activity(weeks = listOf(2, 1)).put("teachers", JSONArray().put(JSONObject().put("name", "甲老师(新工号)"))),
            previous = old, now = ms() + 10_000,
        )
        assertEquals(0, reordered.getJSONArray("changes").length())
        assertFalse(projected(reordered).today!!.lessons.single().changed)
        val duplicate = parse(activity(weeks = listOf(1, 2, 3, 4)), activity(weeks = listOf(1, 2, 3, 4)), activity("另一门课", day = 2), previous = old)
        assertEquals(0, duplicate.getJSONArray("changes").length())
        assertEquals(1, projected(duplicate).today!!.lessons.size)
        val teachers = activity().put("teachers", JSONArray(listOf("甲老师", "乙老师")))
        val reversed = activity().put("teachers", JSONArray(listOf("乙老师", "甲老师")))
        assertEquals(0, parse(reversed, previous = parse(teachers)).getJSONArray("changes").length())
    }

    @Test fun missingAndExplicitEquivalentTimesHaveNoDiff() {
        val old = parse(activity(), activity("另课", day = 2, start = "", end = ""))
        val t = parse(activity(), activity("另课", day = 2, start = "8:20:00", end = "9:45:00"), previous = old)
        assertEquals(0, t.getJSONArray("changes").length())
    }

    @Test fun movedWeekdayShowsOldDayCancellationAndNewDayChange() {
        val t = parse(activity(day = 2), previous = parse(activity()), now = ms() + 60_000)
        val p = TimetableParser.payload(t, ms() + 60_000)
        assertTrue(p.today!!.lessons.isEmpty())
        assertEquals(1, p.today.cancelled.size)
        assertTrue(p.tomorrow!!.lessons.single().changed)
        assertTrue(p.tomorrow.cancelled.isEmpty())
    }

    @Test fun multipleChangesUseMostRecentPreviousStateAndDoNotCancelIntermediateRooms() {
        val a = parse(activity())
        val b = parse(activity(room = "B202"), previous = a, now = ms() + 60_000)
        val c = parse(activity(room = "C303"), previous = b, now = ms() + 120_000)
        val p = TimetableParser.payload(c, ms() + 120_000)
        assertEquals("原 B202", p.today!!.lessons.single().was)
        assertTrue(p.today.cancelled.isEmpty())
        val removed = parse(previous = c, now = ms() + 180_000)
        assertEquals("C303", TimetableParser.payload(removed, ms() + 180_000).today!!.cancelled.single().room)
        val reverted = parse(activity(), previous = c, now = ms() + 180_000)
        val revert = TimetableParser.payload(reverted, ms() + 180_000)
        assertEquals("原 C303", revert.today!!.lessons.single().was)
        assertTrue(revert.today.cancelled.isEmpty())
    }

    @Test fun sevenDayHistoryIsRetainedWithoutNewChangesAndExpiresOffline() {
        val at = ms() + 60_000
        val changed = parse(activity(room = "B202"), previous = parse(activity()), now = at)
        val same = parse(activity(room = "B202"), previous = changed, now = at + 1000)
        assertEquals(1, same.getJSONArray("changes").length())
        assertEquals(at / 1000, TimetableParser.payload(same, at + 1000).changedAt)
        assertEquals(at / 1000, TimetableParser.payload(same, at + 7 * 86400_000L).changedAt)
        val expired = TimetableParser.payload(same, at + 7 * 86400_000L + 1000)
        assertNull(expired.changedAt)
        assertFalse(expired.today!!.lessons.single().changed)
        val pruned = parse(activity(room = "B202"), previous = same, now = at + 7 * 86400_000L + 1000)
        assertEquals(0, pruned.getJSONArray("changes").length())
    }

    @Test fun additionsDuringRefreshAreChangesButInitialImportIsNot() {
        val initial = parse(activity())
        val t = parse(activity(), activity("新课程", day = 2), previous = initial, now = ms() + 1000)
        assertFalse(TimetableParser.payload(t, ms() + 1000).today!!.lessons.single().changed)
        assertTrue(TimetableParser.payload(t, ms() + 1000).tomorrow!!.lessons.single().changed)
        assertNull(projected(initial).changedAt)
    }

    @Test fun repeatedSameNameLessonsPairWithTheSameSlot() {
        val old = parse(activity(), activity(start = "14:00", end = "15:25", su = 6, eu = 7))
        val t = parse(activity(room = "B202"), activity(room = "C303", start = "14:00", end = "15:25", su = 6, eu = 7), previous = old, now = ms() + 1000)
        val p = TimetableParser.payload(t, ms() + 1000)
        assertEquals(listOf("原 A101", "原 A101"), p.today!!.lessons.map { it.was })
        assertTrue(p.today.cancelled.isEmpty())
    }

    @Test fun colorAssignmentAndSortingAreStableAcrossRowOrder() {
        val a = activity("数学")
        val b = activity("写作", start = "07:00", end = "08:00")
        val first = projected(parse(a, b)).today!!.lessons
        val second = projected(parse(b, a)).today!!.lessons
        assertEquals(first, second)
        assertEquals("写作", first[0].name)
        assertNotEquals(first[0].color, first[1].color)
        // FNV-1a over Unicode code points; fixed reference from app/widget.py's palette.
        assertEquals("#9c36b5", projected(parse(activity("测试课程"))).today!!.lessons.single().color)
    }

    @Test fun crossYearProjectionAndTimestampsRemainInShanghaiSeconds() {
        val at = ms("2026-12-31T23:59:59+08:00") + 999
        val t = parse(activity(day = 5, weeks = listOf(17)), now = at)
        val before = TimetableParser.payload(t, at)
        val after = TimetableParser.payload(t, at + 1)
        assertEquals("2026-12-31", before.today!!.date)
        assertEquals("2027-01-01", after.today!!.date)
        assertEquals(17, after.today.week)
        assertEquals(1, after.today.lessons.size)
        assertEquals("2026-12-31T23:59:59+08:00", before.now)
        assertEquals(at / 1000, t.getLong("updated_at"))
    }

    @Test fun cancellationAndReadditionAcrossRefreshesUseLatestHistory() {
        val old = parse(activity())
        val stopped = parse(previous = old, now = ms() + 1000)
        val back = parse(activity(room = "B202"), previous = stopped, now = ms() + 2000)
        val p = TimetableParser.payload(back, ms() + 2000)
        assertEquals("原 A101", p.today!!.lessons.single().was)
        assertTrue(p.today.cancelled.isEmpty())
        val sameRoom = parse(activity(), previous = stopped, now = ms() + 2000)
        assertTrue(TimetableParser.payload(sameRoom, ms() + 2000).today!!.cancelled.isEmpty())
    }

    @Test fun doesNotMutatePreviousOrCurrentCacheDuringParsingAndProjection() {
        val old = parse(activity())
        val original = old.toString()
        val current = parse(activity(room = "B202"), previous = old, now = ms() + 1000)
        assertEquals(original, old.toString())
        val stored = current.toString()
        TimetableParser.payload(current, ms() + 1000)
        TimetableParser.payload(current, ms() + 15 * 86400_000L)
        assertEquals(stored, current.toString())
    }

    @Test fun cachedPayloadRejectsMissingCalendarInsteadOfGuessing() {
        val t = parse(activity()).apply { remove("week1_monday") }
        assertInvalid("校历") { TimetableParser.payload(t, ms()) }
    }

    @Test fun changeDetailsCoverMoveTimeMoveDayCancelAndAdd() {
        val at = ms() + 60_000   // 2026-09-07 第1周 周一
        val base = parse(activity(weeks = listOf(1)))   // 周一 08:20 一节
        // 1 同一天上午调到下午
        val movedTime = parse(activity(weeks = listOf(1), start = "14:00", end = "15:25", su = 6, eu = 7), previous = base, now = at)
        val c1 = TimetableParser.payload(movedTime, at).changes
        assertEquals(1, c1.size)
        assertEquals("moved", c1[0].kind)
        assertEquals(1, c1[0].week)
        assertEquals("原 08:20", c1[0].detail)
        // 2 从某天调到另一天（周一 → 周二）：旧日停课 + 新日新增
        val movedDay = parse(activity(day = 2, weeks = listOf(1)), previous = base, now = at)
        val c2 = TimetableParser.payload(movedDay, at).changes
        assertEquals(2, c2.size)
        assertTrue(c2.any { it.kind == "cancelled" && it.detail == "停课" })
        assertTrue(c2.any { it.kind == "added" && it.detail == "新增" })
        // 3 某天停课
        val cancelled = parse(previous = base, now = at)
        val c3 = TimetableParser.payload(cancelled, at).changes
        assertEquals(1, c3.size)
        assertEquals("cancelled", c3[0].kind)
        assertEquals("停课", c3[0].detail)
        // 4 新增某课一节
        val added = parse(activity(weeks = listOf(1)), activity("新课程", day = 2, weeks = listOf(1)), previous = base, now = at)
        val c4 = TimetableParser.payload(added, at).changes
        assertEquals(1, c4.size)
        assertEquals("added", c4[0].kind)
        assertEquals("新增", c4[0].detail)
        assertEquals("新课程", c4[0].name)
    }
}
