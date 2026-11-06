package wiki.nufe.kebiao.data

import android.content.Context
import android.util.AtomicFile
import android.webkit.CookieManager
import java.io.File
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import okhttp3.HttpUrl.Companion.toHttpUrl
import org.json.JSONArray
import org.json.JSONObject
import wiki.nufe.kebiao.diag.Diag
import kotlin.coroutines.resume

class CasSessionManager(context: Context) {
    private val file = AtomicFile(File(context.applicationContext.noBackupFilesDir, "school-session.enc"))
    val exists: Boolean get() = file.baseFile.exists()

    fun load(): SchoolCookieJar = synchronized(lock) {
        val stored = if (exists) JSONObject(String(DeviceKeys.decrypt(file.readFully(), PURPOSE), Charsets.UTF_8)) else JSONObject()
        val openedAt = revision
        SchoolCookieJar(stored.optJSONArray("cookies") ?: JSONArray(), persist = {
            synchronized(lock) { if (revision == openedAt) save(it) }
        })
    }

    fun save(cookies: JSONArray) = synchronized(lock) {
        val bytes = DeviceKeys.encrypt(JSONObject().put("cookies", cookies).toString().toByteArray(Charsets.UTF_8), PURPOSE)
        val output = file.startWrite()
        try {
            output.write(bytes)
            file.finishWrite(output)
        } catch (error: Exception) {
            file.failWrite(output)
            throw error
        }
    }

    fun clear() = synchronized(lock) { revision++; file.delete() }

    suspend fun restoreToWebView() {
        val jar = withContext(Dispatchers.IO) { runCatching { load() }.getOrNull() } ?: return
        val manager = CookieManager.getInstance()
        for (origin in SchoolUrls.cookieOrigins) {
            for (cookie in jar.loadForRequest(origin.toHttpUrl())) {
                val ok = suspendCancellableCoroutine { continuation ->
                    manager.setCookie(origin, webViewCookieString(cookie)) { accepted ->
                        if (continuation.isActive) continuation.resume(accepted == true)
                    }
                }
                // 被拒说明本机存的这条学校 cookie WebView 不认（属性/域名不符），别当已恢复。
                if (!ok) Diag.w("webview cookie restore rejected for ${cookie.name}")
            }
        }
        manager.flush()
    }

    /** OkHttp 的 Cookie.toString 带 Indirect/HostOnly 等非标准串，CookieManager 未必认；这里只拼标准属性。 */
    private fun webViewCookieString(cookie: okhttp3.Cookie): String = buildString {
        append(cookie.name).append('=').append(cookie.value)
        append("; Path=").append(cookie.path)
        if (cookie.secure) append("; Secure")
        if (cookie.httpOnly) append("; HttpOnly")
    }

    companion object {
        private const val PURPOSE = "school-session-v1"
        private val lock = Any()
        private var revision = 0L

        fun captureWebView(): SchoolCookieJar {
            val jar = SchoolCookieJar()
            val cookies = CookieManager.getInstance()
            SchoolUrls.cookieOrigins.forEach { jar.capture(it, cookies.getCookie(it).orEmpty()) }
            return jar
        }

        suspend fun clearWebViewCookies() {
            suspendCancellableCoroutine { continuation ->
                CookieManager.getInstance().removeAllCookies {
                    if (continuation.isActive) continuation.resume(Unit)
                }
            }
            CookieManager.getInstance().flush()
        }
    }
}
