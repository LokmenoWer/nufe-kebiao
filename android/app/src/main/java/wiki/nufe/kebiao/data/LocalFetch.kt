package wiki.nufe.kebiao.data

import java.io.IOException
import java.net.HttpCookie
import java.net.HttpURLConnection
import java.net.URL
import org.json.JSONArray
import org.json.JSONObject

/**
 * 手机代抓：用这台手机的网络向教务要课表，交给服务器解析（移植自 Scriptable 脚本的 delegatedFetch）。
 * 服务器到期时给绑定过的设备发任务（只有教务域名的 cookie，统一身份认证的凭据不下发）。
 * 这样学校看到的是手机自己的 IP，本站服务器少访问学校；任何一步不顺就如实上报，由服务器兜底。
 * 任务含 cookie：不写缓存、不打日志。
 */
class LocalFetch(
    private val userAgent: String,
    private val allowed: (String) -> Boolean = ::isJwxtUrl,
    private val clock: () -> Long = System::currentTimeMillis,
) {
    private class Failed(val error: String) : Exception()

    /** 返回要 POST 给 `/upload` 的内容；失败时只有 id 和 error。 */
    fun run(task: JSONObject): JSONObject {
        val id = task.optString("id")
        return try {
            collect(task).put("id", id)
        } catch (e: Failed) {
            JSONObject().put("id", id).put("error", e.error)
        } catch (_: IOException) {
            JSONObject().put("id", id).put("error", "network")
        }
    }

    private fun collect(task: JSONObject): JSONObject {
        val started = clock()
        val out = JSONObject()
        val finalUrls = JSONArray()
        val setCookies = JSONArray()
        val fresh = linkedMapOf<String, String>() // 这次学校新发的 cookie，后面的请求接着带
        val loginHosts = task.optJSONArray("login_hosts").strings()
        val steps = task.optJSONArray("steps") ?: JSONArray()
        for (i in 0 until steps.length()) {
            val step = steps.getJSONObject(i)
            var url = step.getString("url")
            var hop = 0
            // 跳转自己跟：每一跳先查目的地、再带上最新的 cookie
            while (true) {
                if (isLoginUrl(url, loginHosts)) throw Failed("expired")
                if (!allowed(url)) throw Failed("blocked")
                if (hop > MAX_HOPS) throw Failed("redirect")
                val left = BUDGET_MS - (clock() - started)
                if (left <= 0) throw Failed("timeout")
                val conn = URL(url).openConnection() as HttpURLConnection
                try {
                    conn.instanceFollowRedirects = false
                    conn.connectTimeout = minOf(left, STEP_TIMEOUT_MS).toInt()
                    conn.readTimeout = minOf(left, STEP_TIMEOUT_MS).toInt()
                    conn.setRequestProperty("User-Agent", userAgent)
                    task.optJSONObject("headers")?.let { headers ->
                        headers.keys().forEach { conn.setRequestProperty(it, headers.getString(it)) }
                    }
                    val jar = cookieMap(step.optString("cookie")).apply { putAll(fresh) }
                    conn.setRequestProperty("Cookie", jar.entries.joinToString("; ") { "${it.key}=${it.value}" })
                    val code = conn.responseCode
                    for (cookie in responseCookies(conn)) {
                        val domain = cookie.domain.orEmpty().removePrefix(".").lowercase()
                        if (domain.isNotEmpty() && domain != JWXT_HOST) continue
                        fresh[cookie.name] = cookie.value.orEmpty()
                        setCookies.put(JSONObject().put("name", cookie.name).put("value", cookie.value.orEmpty()))
                    }
                    if (code in 300..399) {
                        val location = conn.getHeaderField("Location") ?: throw Failed("http_$code")
                        url = resolveLocation(url, location)
                        hop++
                        continue
                    }
                    val kind = step.optString("kind")
                    if (code >= 400) {
                        if (kind == "detail") break // 学期详情只是校历兜底，拿不到不要紧
                        throw Failed("http_$code")
                    }
                    val text = readCapped(conn, started)
                    finalUrls.put(url)
                    when (kind) {
                        "course" -> out.put("course_html", text)
                        "print" -> out.put("print_data", text)
                        "detail" -> out.put("detail", text)
                    }
                    break
                } finally {
                    conn.disconnect()
                }
            }
        }
        return out.put("final_urls", finalUrls).put("set_cookies", setCookies)
    }

    /** 连接超时只管网络空闲，学校慢慢吐数据时能一直拖下去：按总预算和大小上限截断。 */
    private fun readCapped(conn: HttpURLConnection, started: Long): String {
        val buffer = ByteArray(16 * 1024)
        val out = java.io.ByteArrayOutputStream()
        conn.inputStream.use { input ->
            while (true) {
                val n = input.read(buffer)
                if (n < 0) break
                out.write(buffer, 0, n)
                if (out.size() > MAX_BODY_BYTES) throw Failed("too_large")
                if (clock() - started > BUDGET_MS) throw Failed("timeout")
            }
        }
        return out.toString("UTF-8")
    }

    private fun responseCookies(conn: HttpURLConnection): List<HttpCookie> =
        conn.headerFields.orEmpty()
            .filterKeys { it != null && it.equals("Set-Cookie", ignoreCase = true) }
            .values.flatten()
            .flatMap { runCatching { HttpCookie.parse(it) }.getOrDefault(emptyList()) }

    companion object {
        const val BUDGET_MS = 20_000L
        private const val STEP_TIMEOUT_MS = 8_000L
        private const val MAX_HOPS = 5
        private const val MAX_BODY_BYTES = 2 * 1024 * 1024
        private const val JWXT_HOST = "jwxt.nufe.edu.cn"
        private val JWXT_URL = Regex("""^https://jwxt\.nufe\.edu\.cn(?:[/?][^\s\\#]*)?$""", RegexOption.IGNORE_CASE)
        private val URL_PARTS = Regex("""^https?://([^/:?#]+)(?::\d+)?([^?#]*)""", RegexOption.IGNORE_CASE)
        private val SCHEME = Regex("""^[a-z][a-z0-9+.-]*:""", RegexOption.IGNORE_CASE)
        private val ORIGIN = Regex("""^https?://[^/?#]+""", RegexOption.IGNORE_CASE)

        /** 只往教务发：任务地址和每一跳都得是 https://jwxt.nufe.edu.cn，cookie 不会被带到别处。 */
        fun isJwxtUrl(url: String) = JWXT_URL.matches(url)

        /** 被踢回统一身份认证或教务自己的登录页：教务会话过期了，续期只能服务器做。 */
        fun isLoginUrl(url: String, hosts: List<String>): Boolean {
            val match = URL_PARTS.find(url) ?: return false
            val host = match.groupValues[1].lowercase()
            val path = match.groupValues[2].trimEnd('/')
            return host in hosts || (host == JWXT_HOST && path == "/student/login")
        }

        /** 把 Location 解析成绝对地址（学校常回相对路径）。 */
        fun resolveLocation(base: String, location: String): String {
            val s = location.trim()
            if (SCHEME.containsMatchIn(s)) return s
            if (s.startsWith("//")) return "https:$s"
            val origin = ORIGIN.find(base)?.value.orEmpty()
            val path = base.substring(origin.length).split('?', '#')[0].ifEmpty { "/" }
            if (s.startsWith("/")) return origin + s
            if (s.startsWith("?")) return origin + path + s
            return origin + path.substringBeforeLast('/', "") + "/" + s
        }

        private fun cookieMap(header: String): MutableMap<String, String> {
            val jar = linkedMapOf<String, String>()
            for (part in header.split(';')) {
                val i = part.indexOf('=')
                if (i > 0) jar[part.substring(0, i).trim()] = part.substring(i + 1).trim()
            }
            return jar
        }

        private fun JSONArray?.strings(): List<String> =
            if (this == null) emptyList() else (0 until length()).map { optString(it).lowercase() }
    }
}
