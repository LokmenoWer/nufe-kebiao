package wiki.nufe.kebiao.data

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class UpdateCheckTest {
    private fun newer(a: String, b: String) = (UpdateCheck.compare(a, b) ?: 0) > 0

    @Test
    fun comparesVersions() {
        assertTrue(newer("1.2.0", "1.1.1"))
        assertTrue(newer("1.10.0", "1.9.9"))
        assertTrue(newer("1.2.0", "1.2.0-beta.9"))
        assertTrue(newer("1.2.0-beta.10", "1.2.0-beta.2"))
        assertTrue(newer("1.2.0-beta.1", "1.1.1"))
        assertTrue(newer("1.2.0-rc.1", "1.2.0-beta.2"))
        assertFalse(newer("1.2.0-beta.2", "1.2.0-beta.2"))
        assertEquals(0, UpdateCheck.compare("v1.2.0", "1.2.0"))
        assertNull(UpdateCheck.compare("android-v1.0.0-preview", "1.1.1"))
    }

    private fun release(tag: String, pre: Boolean = false, apk: Boolean = true, draft: Boolean = false,
                        url: String = "https://github.com/LokmenoWer/nufe-kebiao/releases/tag/$tag") = JSONObject()
        .put("tag_name", tag).put("prerelease", pre).put("draft", draft).put("html_url", url)
        .put("assets", JSONArray().put(JSONObject().put("name", if (apk) "app.apk" else "notes.txt")))

    private fun list(vararg r: JSONObject) = JSONArray(r.toList()).toString()

    @Test
    fun stableUsersOnlySeeStableReleases() {
        val json = list(release("1.2.0-beta.1", pre = true), release("1.1.1"), release("android-v1.0.0-preview", pre = true))
        assertNull(UpdateCheck.pick(json, "1.1.1"))
        assertEquals("1.1.1", UpdateCheck.pick(json, "1.1.0")?.version)
    }

    @Test
    fun betaUsersSeeNewestOfBoth() {
        val json = list(release("1.2.0-beta.3", pre = true), release("1.1.1"), release("1.2.0-beta.2", pre = true))
        assertEquals("1.2.0-beta.3", UpdateCheck.pick(json, "1.2.0-beta.2")?.version)
        assertNull(UpdateCheck.pick(json, "1.2.0-beta.3"))
        val stable = list(release("1.2.0"), release("1.2.0-beta.3", pre = true))
        assertEquals("1.2.0", UpdateCheck.pick(stable, "1.2.0-beta.2")?.version)
    }

    @Test
    fun ignoresUnusableReleases() {
        assertNull(UpdateCheck.pick(list(release("2.0.0", apk = false)), "1.1.1"))
        assertNull(UpdateCheck.pick(list(release("2.0.0", draft = true)), "1.1.1"))
        assertNull(UpdateCheck.pick(list(release("2.0.0", url = "https://evil.example/x")), "1.1.1"))
        assertNull(UpdateCheck.pick(list(release("2.0.0", url = "http://github.com/LokmenoWer/nufe-kebiao/releases/x")), "1.1.1"))
    }
}
