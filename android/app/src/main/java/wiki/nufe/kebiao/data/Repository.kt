package wiki.nufe.kebiao.data

import android.content.Context
import android.net.Uri
import android.os.Build
import java.io.File
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import wiki.nufe.kebiao.diag.Diag

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
    private val profileFile = File(app.filesDir, "profile.enc")

    var widgetUrl: String?
        get() = prefs.getString(KEY_URL, null)
        set(value) {
            prefs.edit().putString(KEY_URL, value).apply()
            if (value == null) {
                synchronized(cacheLock) { cacheFile.delete() }
                forgetDevice()
            }
        }

    /** 配对后服务器给的设备 id；null 表示这台手机没绑定，读不到个人信息。 */
    val deviceId: String?
        get() = prefs.getString(KEY_DEVICE, null)

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

    /** 点小组件打开 App；默认 false 打开网站。 */
    var widgetOpensApp: Boolean
        get() = prefs.getBoolean(KEY_WIDGET_OPENS_APP, false)
        set(value) = prefs.edit().putBoolean(KEY_WIDGET_OPENS_APP, value).apply()

    /** 小组件上下课重画用闹钟时钟：部分 ROM 上更准时，但状态栏可能显示闹钟图标，所以默认关闭。 */
    var widgetPunctual: Boolean
        get() = prefs.getBoolean(KEY_WIDGET_PUNCTUAL, false)
        set(value) = prefs.edit().putBoolean(KEY_WIDGET_PUNCTUAL, value).apply()

    /** 本机缓存，没有就是 null。 */
    fun cached(): Snapshot? = synchronized(cacheLock) {
        if (!cacheFile.exists()) return null
        try {
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
        // 绑定过的设备走 /mobile（旧的 /widget 链接也换过来），带签名才领得到代抓任务
        val url = (if (deviceId != null) deviceBase()?.let { "$it.json" } else null) ?: widgetUrl ?: return@withContext null
        try {
            val text = withLocalFetch(download(if (refresh) "$url${if ('?' in url) '&' else '?'}refresh=1" else url))
            val payload = Payload.parse(text)
            val nowMs = System.currentTimeMillis()
            synchronized(cacheLock) {
                val tmp = File(app.filesDir, "widget.json.tmp")
                tmp.writeText(text)
                if (!tmp.renameTo(cacheFile)) {
                    cacheFile.writeText(text)
                    tmp.delete()
                }
            }
            Snapshot(payload, nowMs, offline = false)
        } catch (e: LinkInvalidException) {
            throw e
        } catch (_: Exception) {
            // 反向代理错误页、断网、超时：按离线处理，用缓存
            cached()?.copy(offline = true)
        }
    }

    /**
     * 有代抓任务就做，换成服务器入库后的最新数据；任务本身含 cookie，绝不写进缓存。
     * 抓不到或交不回去都不影响显示，服务器到期会兜底。
     */
    private fun withLocalFetch(text: String): String {
        val data = JSONObject(text)
        val task = data.optJSONObject("fetch") ?: return text
        data.remove("fetch")
        val plain = data.toString()
        val base = deviceBase() ?: return plain
        return try {
            val agent = "Mozilla/5.0 (Linux; Android ${Build.VERSION.RELEASE}) AppleWebKit/537.36 Mobile"
            val result = LocalFetch(agent).run(task)
            Diag.i("local fetch: ${result.optString("error").ifEmpty { "ok" }}")
            uploadFetch("$base/upload", result) ?: plain
        } catch (e: Exception) {
            Diag.w("local fetch failed: ${e.javaClass.simpleName}")
            plain
        }
    }

    private fun uploadFetch(url: String, result: JSONObject): String? {
        val conn = open(url)
        try {
            conn.requestMethod = "POST"
            conn.doOutput = true
            conn.readTimeout = 30_000
            conn.setRequestProperty("Content-Type", "application/json")
            conn.outputStream.use { it.write(result.toString().toByteArray()) }
            if (conn.responseCode !in 200..299) {
                Diag.w("local fetch upload: HTTP ${conn.responseCode}")
                return null
            }
            val reply = JSONObject(conn.inputStream.bufferedReader().use { it.readText() })
            return reply.optJSONObject("widget")?.toString()
        } finally {
            conn.disconnect()
        }
    }

    private fun download(url: String): String {
        val conn = open(url)
        runCatching { signRequest(conn) }
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

    /** 用一次性配对码登记本机公钥；成功返回 null，失败返回给用户看的原因。 */
    suspend fun pair(code: String): String? = withContext(Dispatchers.IO) {
        val base = deviceBase() ?: return@withContext "请先连接课表。"
        try {
            val body = JSONObject()
                .put("code", code)
                .put("public_key", DeviceKeys.publicKey())
                .put("name", "${Build.MANUFACTURER} ${Build.MODEL}")
                .toString()
            val conn = open("$base/pair")
            try {
                conn.requestMethod = "POST"
                conn.doOutput = true
                conn.setRequestProperty("Content-Type", "application/json")
                conn.outputStream.use { it.write(body.toByteArray()) }
                val status = conn.responseCode
                if (status !in 200..299) {
                    return@withContext detail(conn) ?: "绑定失败（$status），请稍后再试。"
                }
                val id = JSONObject(conn.inputStream.bufferedReader().use { it.readText() }).getString("device_id")
                prefs.edit().putString(KEY_DEVICE, id).remove(KEY_CLOCK).apply()
                null
            } finally {
                conn.disconnect()
            }
        } catch (_: Exception) {
            "连不上服务器，绑定失败；请检查网络后重试。"
        }
    }

    /** 本机加密保存的个人信息，解不开就丢掉，等下次从服务器取。 */
    suspend fun cachedProfile(): Profile? = withContext(Dispatchers.IO) {
        if (!profileFile.exists()) return@withContext null
        try {
            Profile.parse(String(DeviceKeys.decrypt(profileFile.readBytes())))
        } catch (_: Exception) {
            profileFile.delete()
            null
        }
    }

    /**
     * 向服务器要个人信息（带设备签名）。连不上时退回本机缓存；
     * 设备被撤销或链接换了（401）就清掉本机的绑定和个人信息，返回 null。
     */
    suspend fun fetchProfile(): Profile? = withContext(Dispatchers.IO) {
        if (deviceId == null) return@withContext null
        val base = deviceBase() ?: return@withContext null
        try {
            var result = requestProfile(base)
            if (result.first == 401) {
                val serverTime = runCatching { JSONObject(result.second).optLong("server_time") }.getOrDefault(0L)
                if (serverTime > 0) {
                    // 手机时钟不准：按服务器时间校正，再试一次
                    prefs.edit().putLong(KEY_CLOCK, serverTime - System.currentTimeMillis() / 1000).apply()
                    result = requestProfile(base)
                }
            }
            when (result.first) {
                in 200..299 -> {
                    val profile = Profile.parse(result.second)
                    profileFile.writeBytes(DeviceKeys.encrypt(result.second.toByteArray()))
                    profile
                }
                401 -> {
                    forgetDevice()
                    null
                }
                else -> cachedProfile()
            }
        } catch (_: Exception) {
            cachedProfile()
        }
    }

    /** 设备签名：绑定过才加。 */
    private fun signRequest(conn: HttpURLConnection) {
        val id = deviceId ?: return
        val token = deviceBase()?.substringAfterLast('/') ?: return
        val time = (System.currentTimeMillis() / 1000 + prefs.getLong(KEY_CLOCK, 0)).toString()
        val message = "nufe-kebiao-device-v1\n$token\n$id\n$time".toByteArray()
        conn.setRequestProperty("X-Device-Id", id)
        conn.setRequestProperty("X-Device-Time", time)
        conn.setRequestProperty("X-Device-Signature", DeviceKeys.sign(message))
    }

    private fun requestProfile(base: String): Pair<Int, String> {
        val conn = open("$base/profile")
        signRequest(conn)
        try {
            val status = conn.responseCode
            val stream = if (status in 200..299) conn.inputStream else conn.errorStream
            return status to (stream?.bufferedReader()?.use { it.readText() }.orEmpty())
        } finally {
            conn.disconnect()
        }
    }

    private fun open(url: String) = (URL(url).openConnection() as HttpURLConnection).apply {
        connectTimeout = 10_000
        readTimeout = 15_000
        setRequestProperty("Accept", "application/json")
        setRequestProperty("User-Agent", "nufe-kebiao-android/1")
    }

    private fun detail(conn: HttpURLConnection): String? = runCatching {
        JSONObject(conn.errorStream.bufferedReader().use { it.readText() }).optString("detail").ifEmpty { null }
    }.getOrNull()

    private fun forgetDevice() {
        profileFile.delete()
        prefs.edit().remove(KEY_DEVICE).remove(KEY_CLOCK).apply()
        runCatching { DeviceKeys.reset() }
    }

    /** `.../mobile/<token>`：设备接口都在这下面，旧的 /widget/ 链接也走这里。 */
    private fun deviceBase(): String? = widgetUrl?.let {
        Regex("""/(?:mobile|widget)/([A-Za-z0-9_-]+)\.json$""").find(it)?.let { match ->
            it.substring(0, match.range.first) + "/mobile/" + match.groupValues[1]
        }
    }

    /** 网站首页：链接去掉 /mobile/<token>.json（或旧的 /widget/<token>.json）。 */
    fun siteUrl(): String? = widgetUrl?.let { siteOf(it) }

    companion object {
        private val cacheLock = Any()
        private const val KEY_URL = "widget_url"
        private const val KEY_REMIND = "remind_minutes"
        private const val KEY_ALARM = "alarm_enabled"
        private const val KEY_LIVE_UPDATE = "live_update_enabled"
        private const val KEY_WIDGET_OPENS_APP = "widget_opens_app"
        private const val KEY_WIDGET_PUNCTUAL = "widget_punctual"
        private const val KEY_DEVICE = "device_id"
        private const val KEY_CLOCK = "device_clock_offset"
        const val DEFAULT_REMIND_MINUTES = 10

        private val LINK = Regex("""^https?://[^\s]+/(?:mobile|widget)/[A-Za-z0-9_-]{16,128}\.json$""")

        private val DEEP_LINK = Regex("""nufekebiao://\S+""")
        private val PAIR_CODE = Regex("""^[A-Za-z0-9_-]{16,128}$""")

        /** 从粘贴 / 分享来的文字里找出小组件链接；也认 nufekebiao://setup?url=… 。 */
        fun extractLink(text: String?): String? {
            if (text.isNullOrBlank()) return null
            val raw = text.trim()
            DEEP_LINK.find(raw)?.let { return extractLink(Uri.parse(it.value).getQueryParameter("url")) }
            return Regex("""https?://\S+?/(?:mobile|widget)/[A-Za-z0-9_-]+\.json""").find(raw)?.value?.takeIf { LINK.matches(it) }
        }

        /** 配对链接 nufekebiao://setup?url=…&pair=… 里的一次性配对码。 */
        fun extractPair(text: String?): String? {
            val deep = DEEP_LINK.find(text.orEmpty())?.value ?: return null
            return Uri.parse(deep).getQueryParameter("pair")?.takeIf { PAIR_CODE.matches(it) }
        }

        fun siteOf(widgetUrl: String) = widgetUrl.replace(Regex("""/(?:mobile|widget)/[^/]+$"""), "/")
    }
}

class LinkInvalidException : IOException("widget link no longer valid")
