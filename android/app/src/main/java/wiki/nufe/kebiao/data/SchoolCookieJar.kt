package wiki.nufe.kebiao.data

import okhttp3.Cookie
import okhttp3.CookieJar
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import org.json.JSONArray
import org.json.JSONObject

class SchoolCookieJar(
    stored: JSONArray = JSONArray(),
    private val persist: (JSONArray) -> Unit = {},
    private val clock: () -> Long = System::currentTimeMillis,
) : CookieJar {
    private val cookies = mutableListOf<Cookie>()

    init {
        for (i in 0 until stored.length()) {
            val o = stored.getJSONObject(i)
            val builder = Cookie.Builder().name(o.getString("name")).value(o.getString("value"))
                .path(o.getString("path"))
            if (o.getBoolean("hostOnly")) builder.hostOnlyDomain(o.getString("domain"))
            else builder.domain(o.getString("domain"))
            if (o.optBoolean("persistent")) builder.expiresAt(o.getLong("expiresAt"))
            if (o.optBoolean("secure")) builder.secure()
            if (o.optBoolean("httpOnly")) builder.httpOnly()
            builder.build().takeIf { it.expiresAt > clock() && accepted(it) }?.let(cookies::add)
        }
    }

    private fun accepted(cookie: Cookie) = cookie.domain in setOf(SchoolUrls.AUTH_HOST, SchoolUrls.JW_HOST) &&
        (cookie.name != "CASTGC" || cookie.domain == SchoolUrls.AUTH_HOST)

    @Synchronized
    override fun saveFromResponse(url: HttpUrl, cookies: List<Cookie>) {
        for (cookie in cookies) {
            if (!accepted(cookie)) continue
            this.cookies.removeAll { it.name == cookie.name && it.domain == cookie.domain && it.path == cookie.path }
            if (cookie.expiresAt > clock()) this.cookies += cookie
        }
        persist(serialize())
    }

    @Synchronized
    override fun loadForRequest(url: HttpUrl): List<Cookie> = cookies
        .filter { it.expiresAt > clock() && it.matches(url) }
        .sortedByDescending { it.path.length }

    @Synchronized
    fun capture(url: String, header: String) {
        val origin = url.toHttpUrl()
        require(SchoolUrls.allowed(url))
        val path = if (origin.host == SchoolUrls.AUTH_HOST) "/authserver" else "/student"
        // CookieManager.getCookie 不返回 Expires，无法区分会话级 / 持久 CASTGC；
        // 统一按持久 CASTGC 的约半年寿命盖一个保守到期时间，避免凭据无限期留在本机。
        val expiresAt = clock() + IMPORTED_TTL_MS
        val captured = header.split(';').mapNotNull { part ->
            val split = part.indexOf('=')
            if (split <= 0) return@mapNotNull null
            val name = part.substring(0, split).trim()
            if (name == "CASTGC" && origin.host != SchoolUrls.AUTH_HOST) return@mapNotNull null
            Cookie.Builder().name(name).value(part.substring(split + 1).trim())
                .hostOnlyDomain(origin.host).path(path).expiresAt(expiresAt).secure().httpOnly().build()
        }
        saveFromResponse(origin, captured)
    }

    @Synchronized
    fun serialize() = JSONArray(cookies.filter { it.expiresAt > clock() }.map {
        JSONObject().put("name", it.name).put("value", it.value).put("domain", it.domain)
            .put("path", it.path).put("hostOnly", it.hostOnly).put("secure", it.secure)
            .put("httpOnly", it.httpOnly).put("persistent", it.persistent).put("expiresAt", it.expiresAt)
    })

    val hasTgc: Boolean get() = loadForRequest(SchoolUrls.AUTH.toHttpUrl()).any { it.name == "CASTGC" }

    /** 本机是否还有任一未过期的学校凭据可用于刷新。 */
    val hasSession: Boolean get() = synchronized(this) { cookies.any { it.expiresAt > clock() } }

    companion object {
        const val IMPORTED_TTL_MS = 180L * 24 * 60 * 60 * 1000
    }
}
