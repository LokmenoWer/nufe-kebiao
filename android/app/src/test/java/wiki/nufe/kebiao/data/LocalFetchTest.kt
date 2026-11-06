package wiki.nufe.kebiao.data

import com.sun.net.httpserver.HttpExchange
import com.sun.net.httpserver.HttpServer
import java.io.IOException
import java.net.InetSocketAddress
import okhttp3.Cookie
import okhttp3.HttpUrl.Companion.toHttpUrl
import org.json.JSONArray
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

class LocalFetchTest {
    private lateinit var server: HttpServer
    private val base get() = "http://127.0.0.1:${server.address.port}"
    private fun HttpExchange.reply(code: Int, body: String = "", location: String? = null) {
        location?.let { responseHeaders.add("Location", it) }
        val bytes = body.toByteArray()
        sendResponseHeaders(code, if (bytes.isEmpty()) -1 else bytes.size.toLong())
        if (bytes.isNotEmpty()) responseBody.use { it.write(bytes) } else close()
    }

    @Before fun start() {
        server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/course") { it.reply(302, location = "/print") }
        server.createContext("/print") { it.reply(200, "{\"studentTableVms\":[]}") }
        server.createContext("/offsite") { it.reply(302, location = "https://other.example/steal") }
        server.createContext("/loop") { it.reply(302, location = "/loop") }
        server.createContext("/error") { it.reply(503, "maintenance") }
        server.createContext("/big") { it.reply(200, "x".repeat(LocalFetch.MAX_BODY_BYTES + 1)) }
        server.start()
    }

    @After fun stop() = server.stop(0)

    private fun local() = LocalFetch(SchoolCookieJar(), "test", allowed = { it.startsWith("$base/") })

    @Test fun followsRelativeRedirect() {
        val page = local().get("$base/course")
        assertEquals("/print", page.url.encodedPath)
        assertEquals("{\"studentTableVms\":[]}", page.body)
    }

    @Test fun canTouchWithoutRedeemingTicket() {
        val page = local().get("$base/course", follow = false)
        assertEquals(302, page.code)
        assertEquals("/print", page.location)
    }

    @Test fun rejectsRedirectsOutsideAllowlistAndLoops() {
        listOf("offsite", "loop", "error", "big").forEach { path ->
            assertThrows(IOException::class.java) { local().get("$base/$path") }
        }
    }

    @Test fun onlyAllowsSchoolHttpsOrigins() {
        assertTrue(SchoolUrls.allowed(SchoolUrls.COURSE))
        assertTrue(SchoolUrls.allowed(SchoolUrls.AUTH))
        listOf("http://jwxt.nufe.edu.cn/x", "https://jwxt.nufe.edu.cn.evil.example/x",
            "https://jwxt.nufe.edu.cn@evil.example/x", "https://jwxt.nufe.edu.cn:8443/x",
            "file:///data/x", "javascript:alert(1)", "https://evil@jwxt.nufe.edu.cn/x").forEach {
            assertFalse(it, SchoolUrls.allowed(it))
        }
    }

    @Test fun capturesHttpOnlyCookieHeaderWithoutLeakingBetweenHosts() {
        val jar = SchoolCookieJar()
        jar.capture(SchoolUrls.AUTH, "CASTGC=secret; MULTIFACTOR_BROWSER_FINGERPRINT=device; JSESSIONID=auth")
        jar.capture(SchoolUrls.COURSE, "JSESSIONID=jw; route=node")
        assertTrue(jar.hasTgc)
        val auth = jar.loadForRequest(SchoolUrls.AUTH.toHttpUrl()).associate { it.name to it.value }
        val jw = jar.loadForRequest(SchoolUrls.COURSE.toHttpUrl()).associate { it.name to it.value }
        assertEquals("auth", auth["JSESSIONID"])
        assertEquals("jw", jw["JSESSIONID"])
        assertFalse(jw.containsKey("CASTGC"))
        assertTrue(jar.loadForRequest("https://evil.example/".toHttpUrl()).isEmpty())
        assertTrue(jar.loadForRequest("http://authserver.nufe.edu.cn/authserver/login".toHttpUrl()).isEmpty())
    }

    @Test fun cookieRotationDeletionAndPersistenceKeepScope() {
        var persisted = JSONArray()
        var now = 1000L
        val jar = SchoolCookieJar(persist = { persisted = it }, clock = { now })
        val origin = SchoolUrls.AUTH.toHttpUrl()
        val cookie = Cookie.Builder().name("CASTGC").value("one").hostOnlyDomain(SchoolUrls.AUTH_HOST)
            .path("/authserver").secure().httpOnly().expiresAt(2000L).build()
        jar.saveFromResponse(origin, listOf(cookie))
        assertTrue(SchoolCookieJar(persisted, clock = { now }).hasTgc)
        assertTrue(jar.loadForRequest("https://authserver.nufe.edu.cn/other".toHttpUrl()).isEmpty())
        now = 3000
        assertFalse(jar.hasTgc)
        assertFalse(SchoolCookieJar(persisted, clock = { now }).hasTgc)
        jar.saveFromResponse(origin, listOf(cookie))
        assertEquals(0, persisted.length())
    }

    @Test fun doesNotAcceptOffsiteCookiesOrBroadTgc() {
        val jar = SchoolCookieJar()
        val url = SchoolUrls.AUTH.toHttpUrl()
        jar.saveFromResponse(url, listOf(
            Cookie.Builder().name("CASTGC").value("x").domain("nufe.edu.cn").build(),
            Cookie.Builder().name("SESSION").value("x").domain("evil.example").build(),
        ))
        assertEquals(0, jar.serialize().length())
    }

    @Test fun recognizesLoginAndCourseDataSeparately() {
        assertTrue(SchoolUrls.loginPage(SchoolUrls.AUTH.toHttpUrl()))
        assertTrue(SchoolUrls.loginPage("https://jwxt.nufe.edu.cn/student/login/".toHttpUrl()))
        assertFalse(SchoolUrls.loginPage(SchoolUrls.COURSE.toHttpUrl()))
        assertTrue(LocalFetch.hasCourseData("var personId = 1; var currentSemester = {'id': 2};"))
        assertFalse(LocalFetch.hasCourseData("<html>维护中</html>"))
    }

    @Test fun importedCookiesExpireAfterConservativeTtl() {
        var now = 1000L
        val jar = SchoolCookieJar(clock = { now })
        jar.capture(SchoolUrls.AUTH, "CASTGC=secret; JSESSIONID=auth")
        assertTrue(jar.hasTgc)
        assertTrue(jar.hasSession)
        now += SchoolCookieJar.IMPORTED_TTL_MS + 1
        assertFalse(jar.hasTgc)
        assertFalse(jar.hasSession)
    }

    @Test fun emptyJarHasNoSession() {
        assertFalse(SchoolCookieJar().hasSession)
        assertFalse(SchoolCookieJar().hasTgc)
    }

    @Test fun rejectsParentDomainCookiesAndKeepsPerHostScope() {
        val jar = SchoolCookieJar()
        // 教务响应试图下发父域 cookie：会同时命中 authserver 与 jwxt，属跨主机泄露，直接拒收。
        jar.saveFromResponse(SchoolUrls.COURSE.toHttpUrl(), listOf(
            Cookie.Builder().name("SSO_SHARED").value("x").domain("nufe.edu.cn").build(),
        ))
        assertEquals(0, jar.serialize().length())
        // 精确主机 cookie 保留，且只发给对应主机。
        jar.saveFromResponse(SchoolUrls.COURSE.toHttpUrl(), listOf(
            Cookie.Builder().name("JSESSIONID").value("jw").hostOnlyDomain(SchoolUrls.JW_HOST).path("/student").build(),
        ))
        assertEquals(1, jar.loadForRequest(SchoolUrls.COURSE.toHttpUrl()).size)
        assertTrue(jar.loadForRequest(SchoolUrls.AUTH.toHttpUrl()).isEmpty())
    }
}
