package wiki.nufe.kebiao

import java.time.Instant
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import wiki.nufe.kebiao.data.Payload
import wiki.nufe.kebiao.data.Snapshot
import wiki.nufe.kebiao.ui.homeSyncText
import wiki.nufe.kebiao.widget.WidgetData
import wiki.nufe.kebiao.widget.placeholderMessage

class LocalFirstUiTest {
    private val updatedAt = Instant.parse("2026-11-05T02:30:00Z").epochSecond
    private val fetchedAt = Instant.parse("2026-11-06T03:00:00Z").toEpochMilli()

    private fun snapshot(status: String = "ok", offline: Boolean = false) = Snapshot(
        Payload.parse("""{"status":"$status","updated_at":$updatedAt}"""),
        fetchedAtMs = fetchedAt,
        offline = offline,
    )

    @Test
    fun emptyWidgetInvitesLocalSchoolLogin() {
        assertEquals("在App登录学校账号", placeholderMessage(WidgetData(null, false, true)))
    }

    @Test
    fun expiredLoginKeepsOfflineTimetableVisible() {
        assertNull(placeholderMessage(WidgetData(snapshot("needs_rebind", true), true, true)))
    }

    @Test
    fun unreachableSchoolKeepsTimetableVisible() {
        assertNull(placeholderMessage(WidgetData(snapshot(offline = true), true, false)))
    }

    @Test
    fun migratedCacheIsNotHiddenByMissingSession() {
        assertNull(placeholderMessage(WidgetData(snapshot("needs_rebind", true), false, true)))
    }

    @Test
    fun updateLabelUsesRealSchoolTimestampInShanghaiTime() {
        assertEquals("上次更新 2026-11-05 10:30", homeSyncText(snapshot()))
        assertEquals("离线 · 上次更新 2026-11-05 10:30", homeSyncText(snapshot(offline = true)))
    }

    @Test
    fun legacyCacheWithoutSchoolTimestampIsNotCalledFreshlySynced() {
        val old = snapshot().copy(payload = Payload.parse("{}"), offline = true)
        assertEquals("离线 · 缓存保存于 2026-11-06 11:00", homeSyncText(old))
        assertEquals("离线 · 上次更新时间未知", homeSyncText(old.copy(fetchedAtMs = 0)))
    }
}
