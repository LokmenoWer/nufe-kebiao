package wiki.nufe.kebiao.data

import com.sun.net.httpserver.HttpExchange
import com.sun.net.httpserver.HttpServer
import java.net.InetSocketAddress
import org.json.JSONArray
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class LocalFetchTest {
    private lateinit var server: HttpServer
    private val cookies = mutableListOf<String>()
    private val base get() = "http://127.0.0.1:${server.address.port}"

    private fun HttpExchange.reply(code: Int, body: String = "", vararg headers: Pair<String, String>) {
        headers.forEach { (k, v) -> responseHeaders.add(k, v) }
        val bytes = body.toByteArray()
        sendResponseHeaders(code, if (bytes.isEmpty()) -1 else bytes.size.toLong())
        if (bytes.isNotEmpty()) responseBody.use { it.write(bytes) } else close()
    }

    @Before
    fun start() {
        server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/course") { e ->
            cookies += e.requestHeaders.getFirst("Cookie").orEmpty()
            e.reply(302, "", "Location" to "/course2", "Set-Cookie" to "SESSION=new; Path=/student")
        }
        server.createContext("/course2") { e ->
            cookies += e.requestHeaders.getFirst("Cookie").orEmpty()
            e.reply(200, "<html>课表</html>", "Set-Cookie" to "OTHER=x; Domain=evil.example")
        }
        server.createContext("/print") { e ->
            cookies += e.requestHeaders.getFirst("Cookie").orEmpty()
            assertEquals("XMLHttpRequest", e.requestHeaders.getFirst("X-Requested-With"))
            e.reply(200, """{"studentTableVms":[]}""")
        }
        server.createContext("/missing") { e -> e.reply(404) }
        server.createContext("/boom") { e -> e.reply(500) }
        server.createContext("/login") { e -> e.reply(302, "", "Location" to "https://auth.example/login") }
        server.createContext("/offsite") { e -> e.reply(302, "", "Location" to "https://evil.example/steal") }
        server.createContext("/loop") { e -> e.reply(302, "", "Location" to "/loop") }
        server.start()
    }

    @After
    fun stop() = server.stop(0)

    private fun task(vararg steps: Pair<String, String>) = JSONObject()
        .put("id", "t1")
        .put("headers", JSONObject().put("X-Requested-With", "XMLHttpRequest"))
        .put("login_hosts", JSONArray().put("auth.example"))
        .put("steps", JSONArray(steps.map { (kind, path) ->
            JSONObject().put("kind", kind).put("url", base + path).put("cookie", "SESSION=old; A=1")
        }))

    private fun local() = LocalFetch("test", allowed = { it.startsWith(base) })

    @Test
    fun followsRedirectsAndCarriesNewCookies() {
        val out = local().run(task("course" to "/course", "print" to "/print", "detail" to "/missing"))
        assertEquals("t1", out.getString("id"))
        assertFalse(out.has("error"))
        assertEquals("<html>课表</html>", out.getString("course_html"))
        assertEquals("""{"studentTableVms":[]}""", out.getString("print_data"))
        assertFalse(out.has("detail")) // 学期详情拿不到不要紧
        assertEquals(listOf("SESSION=old; A=1", "SESSION=new; A=1", "SESSION=new; A=1"), cookies)
        // 别的域名发的 cookie 不收
        assertEquals(1, out.getJSONArray("set_cookies").length())
        assertEquals("SESSION", out.getJSONArray("set_cookies").getJSONObject(0).getString("name"))
        assertEquals(2, out.getJSONArray("final_urls").length())
    }

    private fun errorOf(path: String, local: LocalFetch = local()) =
        local.run(task("course" to path)).getString("error")

    @Test
    fun reportsFailuresForServerFallback() {
        assertEquals("http_500", errorOf("/boom"))
        assertEquals("http_404", errorOf("/missing"))
        assertEquals("expired", errorOf("/login"))
        assertEquals("blocked", errorOf("/offsite"))
        assertEquals("redirect", errorOf("/loop"))
    }

    @Test
    fun failureCarriesNoCookies() {
        val out = local().run(task("course" to "/course", "print" to "/boom"))
        assertEquals(setOf("id", "error"), out.keys().asSequence().toSet())
    }

    @Test
    fun stopsWhenBudgetRunsOut() {
        var now = 0L
        val slow = LocalFetch("test", allowed = { true }, clock = { now.also { now += LocalFetch.BUDGET_MS + 1 } })
        assertEquals("timeout", errorOf("/course", slow))
    }

    @Test
    fun onlyAllowsJwxtOverHttps() {
        assertTrue(LocalFetch.isJwxtUrl("https://jwxt.nufe.edu.cn/student/x?a=1"))
        assertFalse(LocalFetch.isJwxtUrl("http://jwxt.nufe.edu.cn/student"))
        assertFalse(LocalFetch.isJwxtUrl("https://jwxt.nufe.edu.cn.evil.example/"))
        assertFalse(LocalFetch.isJwxtUrl("https://jwxt.nufe.edu.cn@evil.example/"))
        assertEquals("expired", LocalFetch("t").run(task("course" to "/x").put("steps", JSONArray().put(
            JSONObject().put("kind", "course").put("url", "https://jwxt.nufe.edu.cn/student/login/"),
        ))).getString("error"))
    }

    @Test
    fun resolvesRelativeLocations() {
        val from = "https://jwxt.nufe.edu.cn/student/a/b?x=1"
        assertEquals("https://jwxt.nufe.edu.cn/c", LocalFetch.resolveLocation(from, "/c"))
        assertEquals("https://jwxt.nufe.edu.cn/student/a/c", LocalFetch.resolveLocation(from, "c"))
        assertEquals("https://jwxt.nufe.edu.cn/student/a/b?y=2", LocalFetch.resolveLocation(from, "?y=2"))
        assertEquals("https://o.example/z", LocalFetch.resolveLocation(from, "//o.example/z"))
        assertEquals("https://o.example/z", LocalFetch.resolveLocation(from, "https://o.example/z"))
    }
}
