package wiki.nufe.kebiao.data

import android.content.Context
import android.net.Uri
import java.io.File
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** 一次读到的课表：offline 表示这次没连上服务器，用的是本机缓存。 */
data class Snapshot(val payload: Payload, val fetchedAtMs: Long, val offline: Boolean)

/**
 * 小组件链接、提醒设置和课表缓存。链接本身就是凭证：只存在 App 私有目录，不打日志。
 * 缓存是服务器原样返回的 JSON，断网时小组件照样能画。
 */
class Repository(context: Context) {
    private val app = context.applicationContext
    private val prefs = app.getSharedPreferences("kebiao", Context.MODE_PRIVATE)
    private val cacheFile = File(app.filesDir, "widget.json")

    var widgetUrl: String?
        get() = prefs.getString(KEY_URL, null)
        set(value) {
            prefs.edit().putString(KEY_URL, value).apply()
            if (value == null) cacheFile.delete()
        }

    /** 上课提醒提前几分钟；0 关闭。 */
    var remindMinutes: Int
        get() = prefs.getInt(KEY_REMIND, DEFAULT_REMIND_MINUTES)
        set(value) = prefs.edit().putInt(KEY_REMIND, value).apply()

    /** 上课提醒总开关；关闭后不排本地闹钟，也不发课前通知。 */
    var alarmEnabled: Boolean
        get() = prefs.getBoolean(
            KEY_ALARM,
            prefs.getInt(KEY_REMIND, DEFAULT_REMIND_MINUTES) > 0,
        )
        set(value) = prefs.edit().putBoolean(KEY_ALARM, value).apply()

    /** 是否用 Android 16 Live Updates / ColorOS 流体云展示上课提醒。 */
    var liveUpdateEnabled: Boolean
        get() = prefs.getBoolean(KEY_LIVE_UPDATE, false)
        set(value) = prefs.edit().putBoolean(KEY_LIVE_UPDATE, value).apply()

    /** 本机缓存，没有就是 null。 */
    fun cached(): Snapshot? {
        if (!cacheFile.exists()) return null
        return try {
            Snapshot(Payload.parse(cacheFile.readText()), cacheFile.lastModified(), offline = false)
        } catch (_: Exception) {
            null
        }
    }

    /**
     * 向服务器要最新数据；refresh = true 时请服务器插队向学校拉一次（服务器自己限流）。
     * 连不上时退回缓存并标记 offline；链接失效（404）抛 [LinkInvalidException]。
     */
    suspend fun fetch(refresh: Boolean = false): Snapshot? = withContext(Dispatchers.IO) {
        val url = widgetUrl ?: return@withContext null
        try {
            val text = download(if (refresh) "$url${if ('?' in url) '&' else '?'}refresh=1" else url)
            val payload = Payload.parse(text)
            val tmp = File(app.filesDir, "widget.json.tmp")
            tmp.writeText(text)
            tmp.renameTo(cacheFile)
            Snapshot(payload, System.currentTimeMillis(), offline = false)
        } catch (e: LinkInvalidException) {
            throw e
        } catch (_: Exception) {
            // 反向代理错误页、断网、超时：按离线处理，用缓存
            cached()?.copy(offline = true)
        }
    }

    private fun download(url: String): String {
        val conn = URL(url).openConnection() as HttpURLConnection
        conn.connectTimeout = 10_000
        conn.readTimeout = 15_000
        conn.setRequestProperty("Accept", "application/json")
        conn.setRequestProperty("User-Agent", "nufe-kebiao-android/1")
        try {
            val code = conn.responseCode
            if (code == 404) throw LinkInvalidException()
            if (code !in 200..299) throw IOException("HTTP $code")
            val type = conn.contentType.orEmpty()
            if (!type.contains("json")) throw IOException("not json: $type")
            return conn.inputStream.bufferedReader().use { it.readText() }
        } finally {
            conn.disconnect()
        }
    }

    /** 网站首页：小组件链接去掉 /widget/<token>.json。 */
    fun siteUrl(): String? = widgetUrl?.let { siteOf(it) }

    companion object {
        private const val KEY_URL = "widget_url"
        private const val KEY_REMIND = "remind_minutes"
        private const val KEY_ALARM = "alarm_enabled"
        private const val KEY_LIVE_UPDATE = "live_update_enabled"
        const val DEFAULT_REMIND_MINUTES = 10

        private val LINK = Regex("""^https?://[^\s]+/widget/[A-Za-z0-9_-]{16,128}\.json$""")

        /** 从粘贴 / 分享来的文字里找出小组件链接；也认 nufekebiao://setup?url=… 。 */
        fun extractLink(text: String?): String? {
            if (text.isNullOrBlank()) return null
            val raw = text.trim()
            if (raw.startsWith("nufekebiao://")) return extractLink(Uri.parse(raw).getQueryParameter("url"))
            return Regex("""https?://\S+?/widget/[A-Za-z0-9_-]+\.json""").find(raw)?.value?.takeIf { LINK.matches(it) }
        }

        fun siteOf(widgetUrl: String) = widgetUrl.replace(Regex("""/widget/[^/]+$"""), "/")
    }
}

class LinkInvalidException : IOException("widget link no longer valid")
