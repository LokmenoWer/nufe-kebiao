package wiki.nufe.kebiao.ui

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import wiki.nufe.kebiao.data.Cancelled
import wiki.nufe.kebiao.data.Lesson
import wiki.nufe.kebiao.data.Payload

class ChinaRomTest {
    @Test
    fun detectsMainlandRomFamiliesByBrandAndSystemProperties() {
        assertEquals(RomFamily.COLOR_OS, detectRomFamily("OPPO", "OPPO") { "" })
        assertEquals(RomFamily.COLOR_OS, detectRomFamily("OnePlus", "OnePlus") { "" })
        assertEquals(RomFamily.COLOR_OS, detectRomFamily("realme", "realme") { "" })
        assertEquals(RomFamily.HYPER_OS, detectRomFamily("Xiaomi", "Redmi") { "" })
        assertEquals(RomFamily.HYPER_OS, detectRomFamily("Xiaomi", "POCO") { "" })
        assertEquals(RomFamily.ORIGIN_OS, detectRomFamily("vivo", "iQOO") { "" })
        assertEquals(RomFamily.MAGIC_OS, detectRomFamily("HONOR", "HONOR") { "" })
        assertEquals(RomFamily.HARMONY_OS, detectRomFamily("HUAWEI", "HUAWEI") { "" })
        assertEquals(RomFamily.FLYME, detectRomFamily("Meizu", "meizu") { "" })
        assertEquals(RomFamily.ZUI, detectRomFamily("Lenovo", "Lenovo") { "" })
        assertEquals(RomFamily.OTHER, detectRomFamily("motorola", "motorola") { "" })
        assertEquals(
            RomFamily.ZUI,
            detectRomFamily("motorola", "motorola") { key ->
                if (key == "ro.com.zui.version") "16.0" else ""
            },
        )
        assertEquals(RomFamily.ONE_UI, detectRomFamily("samsung", "samsung") { "" })
        assertEquals(RomFamily.OTHER, detectRomFamily("Google", "google") { "" })

        // Property-based fallback when brand string is generic
        assertEquals(
            RomFamily.HYPER_OS,
            detectRomFamily("Generic", "Generic") { key ->
                if (key == "ro.mi.os.version.name") "OS2.0" else ""
            },
        )
        assertEquals(
            RomFamily.COLOR_OS,
            detectRomFamily("Generic", "Generic") { key ->
                if (key == "ro.build.version.oplusrom") "V16.0.0" else ""
            },
        )
        assertEquals(
            RomFamily.ONE_UI,
            detectRomFamily("Generic", "Generic") { key ->
                if (key == "ro.build.version.oneui") "80000" else ""
            },
        )
    }

    @Test
    fun mapsLiveUpdateNameAndTierByRomAndSdk() {
        assertEquals("流体云", liveUpdateName(RomFamily.COLOR_OS))
        assertEquals("超级岛 / 焦点通知", liveUpdateName(RomFamily.HYPER_OS))
        assertEquals("原子岛", liveUpdateName(RomFamily.ORIGIN_OS))
        assertEquals("灵动胶囊", liveUpdateName(RomFamily.MAGIC_OS))
        assertEquals("实况窗", liveUpdateName(RomFamily.HARMONY_OS))
        assertEquals("实况通知", liveUpdateName(RomFamily.FLYME))
        assertEquals("实时通知", liveUpdateName(RomFamily.ONE_UI))
        assertEquals("实时活动", liveUpdateName(RomFamily.OTHER))

        assertEquals(
            LiveUpdateTier.NATIVE_PROMOTED,
            detectLiveUpdateTier(sdkInt = 36, family = RomFamily.OTHER, promotedApiAvailable = true),
        )
        assertEquals(
            LiveUpdateTier.FLYME_CAPSULE,
            detectLiveUpdateTier(sdkInt = 34, family = RomFamily.FLYME, promotedApiAvailable = false),
        )
        assertEquals(
            LiveUpdateTier.OEM_EXTRAS,
            detectLiveUpdateTier(sdkInt = 35, family = RomFamily.HYPER_OS, promotedApiAvailable = false),
        )
        assertEquals(
            LiveUpdateTier.OEM_EXTRAS,
            detectLiveUpdateTier(sdkInt = 35, family = RomFamily.ORIGIN_OS, promotedApiAvailable = false),
        )
        assertEquals(
            LiveUpdateTier.OEM_EXTRAS,
            detectLiveUpdateTier(sdkInt = 35, family = RomFamily.MAGIC_OS, promotedApiAvailable = false),
        )
        assertEquals(
            LiveUpdateTier.UNSUPPORTED,
            detectLiveUpdateTier(sdkInt = 35, family = RomFamily.OTHER, promotedApiAvailable = false),
        )
        assertEquals(
            LiveUpdateTier.UNSUPPORTED,
            detectLiveUpdateTier(sdkInt = 35, family = RomFamily.ONE_UI, promotedApiAvailable = false),
        )
    }

    @Test
    fun formatsRomSummaryForDiagnostics() {
        val summary = romSummary("Xiaomi", "Redmi") { key ->
            if (key == "ro.mi.os.version.name") "OS2.0.102.0" else ""
        }
        assertEquals("HyperOS / MIUI (OS2.0.102.0)", summary)
        assertTrue(RomFamily.HYPER_OS.needsAutoStart)
        assertTrue(RomFamily.COLOR_OS.needsAutoStart)
        assertTrue(RomFamily.ORIGIN_OS.needsAutoStart)
        assertTrue(RomFamily.MAGIC_OS.needsAutoStart)
        assertTrue(RomFamily.HARMONY_OS.needsAutoStart)
        assertFalse(RomFamily.OTHER.needsAutoStart)
    }

    @Test
    fun payloadFromJsonMatchesParseAndPrecomputesLessonMinutes() {
        val raw = JSONObject()
            .put("semester", "2026-2027-1")
            .put("semester_id", 901)
            .put("status", "ok")
        val fromJson = Payload.fromJson(raw)
        val fromString = Payload.parse(raw.toString())
        assertEquals(fromString, fromJson)

        val lesson = Lesson(
            name = "高等数学",
            start = "08:20",
            end = "09:45",
            room = "博观楼 101",
            units = "1–2",
            teachers = "张老师",
            color = "#4b63e0",
            changed = false,
            was = null,
        )
        assertEquals(8 * 60 + 20, lesson.startMinutes)
        assertEquals(9 * 60 + 45, lesson.endMinutes)

        val cancelled = Cancelled("大学英语", "14:00", "15:25", "德济楼 202", "#495057")
        assertEquals(14 * 60, cancelled.startMinutes)
        assertEquals(15 * 60 + 25, cancelled.endMinutes)
    }
}
