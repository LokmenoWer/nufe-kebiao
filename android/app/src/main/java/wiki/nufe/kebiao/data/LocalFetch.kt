package wiki.nufe.kebiao.data

import java.io.ByteArrayOutputStream
import java.io.IOException
import java.util.concurrent.TimeUnit
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject

object SchoolUrls {
    const val AUTH_HOST = "authserver.nufe.edu.cn"
    const val JW_HOST = "jwxt.nufe.edu.cn"
    const val AUTH = "https://authserver.nufe.edu.cn/authserver/login"
    const val SSO = "https://jwxt.nufe.edu.cn/student/sso/login"
    const val COURSE = "https://jwxt.nufe.edu.cn/student/for-std/course-table"
    val cookieOrigins = listOf(AUTH, COURSE)

    fun allowed(url: String): Boolean = url.toHttpUrlOrNull()?.let {
        it.isHttps && it.port == 443 && it.username.isEmpty() && it.password.isEmpty() &&
            it.host in setOf(AUTH_HOST, JW_HOST)
    } == true

    fun loginPage(url: HttpUrl): Boolean = url.host == AUTH_HOST ||
        (url.host == JW_HOST && url.encodedPath.trimEnd('/') == "/student/login")

    fun printData(id: Int) = "https://$JW_HOST/student/for-std/course-table/semester/$id/print-data?semesterId=$id&hasExperiment=true"
    fun semesterDetail(id: Int) = "https://$JW_HOST/student/ws/semester/get/$id"
}

class SchoolSessionExpired : IOException("学校登录已失效，请在 App 中重新登录")
class SchoolPageException(message: String) : IOException(message)

class LocalFetch(
    private val jar: SchoolCookieJar,
    userAgent: String,
    private val allowed: (String) -> Boolean = SchoolUrls::allowed,
) {
    private val client = OkHttpClient.Builder()
        .cookieJar(jar)
        .followRedirects(false)
        .followSslRedirects(false)
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS)
        .callTimeout(25, TimeUnit.SECONDS)
        .addInterceptor { chain ->
            val request = chain.request()
            if (!allowed(request.url.toString())) throw IOException("已阻止非学校地址")
            chain.proceed(request.newBuilder().header("User-Agent", userAgent).build())
        }.build()

    data class Page(val url: HttpUrl, val code: Int, val body: String, val location: String?)

    fun get(url: String, follow: Boolean = true, ajax: Boolean = false): Page {
        var target = url.toHttpUrl()
        val started = System.nanoTime()
        repeat(9) {
            if (!allowed(target.toString())) throw IOException("已阻止非学校跳转")
            if (TimeUnit.NANOSECONDS.toSeconds(System.nanoTime() - started) >= 45) throw IOException("学校请求超时")
            val request = Request.Builder().url(target).header("Accept", if (ajax) "application/json" else "text/html")
            if (ajax) request.header("X-Requested-With", "XMLHttpRequest")
            client.newCall(request.build()).execute().use { response ->
                val location = response.header("Location")
                if (follow && response.code in 300..399 && location != null) {
                    target = target.resolve(location) ?: throw IOException("无效学校跳转")
                } else {
                    if (response.code >= 500) throw IOException("学校服务暂时不可用（${response.code}）")
                    if (response.code in listOf(401, 403)) throw SchoolSessionExpired()
                    if (response.code >= 400) throw IOException("学校请求失败（${response.code}）")
                    val body = response.body ?: throw IOException("学校返回空响应")
                    if (body.contentLength() > MAX_BODY_BYTES) throw SchoolPageException("课表数据过大")
                    val output = ByteArrayOutputStream()
                    body.byteStream().use { input ->
                        val buffer = ByteArray(16 * 1024)
                        while (true) {
                            val size = input.read(buffer)
                            if (size < 0) break
                            if (output.size() + size > MAX_BODY_BYTES) throw SchoolPageException("课表数据过大")
                            output.write(buffer, 0, size)
                        }
                    }
                    return Page(target, response.code, output.toString("UTF-8"), location)
                }
            }
        }
        throw IOException("学校跳转次数过多")
    }

    fun coursePage(): String {
        val first = get(SchoolUrls.COURSE)
        if (!SchoolUrls.loginPage(first.url) && hasCourseData(first.body)) return first.body
        if (!SchoolUrls.loginPage(first.url)) throw SchoolPageException("学校课表页面结构异常，请稍后重试")
        return renewWithTgc()
    }

    private var renewed = false

    /** 用 CASTGC 换一次教务会话。同一次刷新最多换一次，避免反复打认证中心触发风控。 */
    fun renewWithTgc(): String {
        if (renewed || !jar.hasTgc) throw SchoolSessionExpired()
        renewed = true
        val renewed = get(SchoolUrls.SSO)
        if (SchoolUrls.loginPage(renewed.url)) throw SchoolSessionExpired()
        val course = get(SchoolUrls.COURSE)
        if (SchoolUrls.loginPage(course.url)) throw SchoolSessionExpired()
        if (!hasCourseData(course.body)) throw SchoolPageException("学校课表页面结构异常，请稍后重试")
        return course.body
    }

    fun collect(courseHtml: String? = null, previous: JSONObject? = null): JSONObject {
        val html = courseHtml ?: coursePage()
        val semester = TimetableParser.semesterId(html)
        val print = get(SchoolUrls.printData(semester), ajax = true)
        if (SchoolUrls.loginPage(print.url)) throw SchoolSessionExpired()
        // 半失效时接口可能 200 返回登录中间页 HTML；当成登录失效走重登，别落到解析错误而漏置 needs_login。
        if (!print.body.trimStart().startsWith("{")) throw SchoolSessionExpired()
        val detail = try {
            get(SchoolUrls.semesterDetail(semester), ajax = true).also {
                if (SchoolUrls.loginPage(it.url)) throw SchoolSessionExpired()
            }.body
        } catch (expired: SchoolSessionExpired) {
            throw expired
        } catch (_: IOException) {
            null
        }
        return TimetableParser.parse(html, print.body, detail, previous)
    }

    fun touchSso(): Boolean {
        if (!jar.hasTgc) return false
        val target = SchoolUrls.AUTH.toHttpUrl().newBuilder().addQueryParameter("service", SchoolUrls.SSO).build()
        val response = get(target.toString(), follow = false)
        val redirect = response.location?.let(response.url::resolve) ?: return false
        return response.code in 300..399 && redirect.host == SchoolUrls.JW_HOST &&
            redirect.encodedPath == "/student/sso/login" && redirect.queryParameter("ticket") != null
    }

    companion object {
        const val MAX_BODY_BYTES = 2 * 1024 * 1024
        fun hasCourseData(html: String) = Regex("""\bpersonId\s*=\s*\d+""").containsMatchIn(html) &&
            Regex("""\bcurrentSemester\s*=\s*\{""").containsMatchIn(html)
    }
}
