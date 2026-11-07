package wiki.nufe.kebiao.assist

import android.content.Context
import android.content.pm.ApplicationInfo
import android.os.Build
import androidx.core.content.pm.PackageInfoCompat
import java.security.SecureRandom
import java.util.Base64
import java.util.concurrent.TimeUnit
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import wiki.nufe.kebiao.data.Repository

/**
 * 和项目服务器之间仅有的两件事（都不带学号、密码、cookie 或课表）：
 * 1. 匿名心跳：随机安装 id + 版本号 + 系统版本 + 两个开关，服务器只存安装 id 的哈希，用来统计装机量；
 * 2. 协助刷新（见 [AssistRelay]）：用户自愿打开后，帮服务器把网页用户的课表请求转发到教务系统。
 */
object Community {
    private const val PREFS = "community"
    private const val KEY_INSTALL = "install_id"
    private const val KEY_ASSIST = "assist_enabled"
    private const val KEY_AVAILABLE = "assist_available"
    private const val KEY_HEARTBEAT_AT = "heartbeat_at"
    private const val KEY_HELPED = "assist_helped"
    private const val KEY_DEV_SITE = "dev_site"
    private val SITE = wiki.nufe.kebiao.BuildConfig.SITE

    internal val JSON = "application/json; charset=utf-8".toMediaType()

    internal val http: OkHttpClient by lazy {
        OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(15, TimeUnit.SECONDS)   // 交换接口最多挂起 2 秒，留足余量
            .callTimeout(25, TimeUnit.SECONDS)
            .followRedirects(false)
            .build()
    }

    private fun prefs(context: Context) = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    /** 调试包可以用 run-as 改 community.xml 里的 dev_site 指向本机服务器；正式包永远是官网。 */
    fun site(context: Context): String {
        val debuggable = (context.applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE) != 0
        return prefs(context).getString(KEY_DEV_SITE, null)?.takeIf { debuggable }?.trimEnd('/') ?: SITE
    }

    /** 随机生成、只存在本机；与学号无关，清除本机数据也不影响（它本来就不指向任何人）。 */
    fun installId(context: Context): String {
        val p = prefs(context)
        p.getString(KEY_INSTALL, null)?.let { return it }
        val bytes = ByteArray(24).also(SecureRandom()::nextBytes)
        val id = Base64.getUrlEncoder().withoutPadding().encodeToString(bytes)
        p.edit().putString(KEY_INSTALL, id).apply()
        return id
    }

    fun assistEnabled(context: Context): Boolean = prefs(context).getBoolean(KEY_ASSIST, false)

    fun setAssistEnabled(context: Context, enabled: Boolean) {
        prefs(context).edit().putBoolean(KEY_ASSIST, enabled).apply()
        AssistWorker.schedule(context)
        HeartbeatWorker.now(context)  // 让服务器尽快知道开关变了（派活要看这个）
    }

    /** 服务器上次是否说协助刷新已开放；null = 还没问过。 */
    fun assistAvailable(context: Context): Boolean? =
        prefs(context).takeIf { it.contains(KEY_AVAILABLE) }?.getBoolean(KEY_AVAILABLE, false)

    fun helpedCount(context: Context): Int = prefs(context).getInt(KEY_HELPED, 0)

    internal fun recordHelped(context: Context) {
        val p = prefs(context)
        p.edit().putInt(KEY_HELPED, p.getInt(KEY_HELPED, 0) + 1).apply()
    }

    fun heartbeatDue(context: Context, maxAgeMs: Long): Boolean =
        System.currentTimeMillis() - prefs(context).getLong(KEY_HEARTBEAT_AT, 0L) > maxAgeMs

    /** 阻塞调用，放在后台线程。失败返回 false，下个周期再试。 */
    fun heartbeat(context: Context): Boolean {
        val app = context.applicationContext
        val info = runCatching { app.packageManager.getPackageInfo(app.packageName, 0) }.getOrNull()
        val body = JSONObject()
            .put("install_id", installId(app))
            .put("version", info?.versionName?.substringBefore('-'))
            .put("version_code", info?.let(PackageInfoCompat::getLongVersionCode) ?: JSONObject.NULL)
            .put("sdk", Build.VERSION.SDK_INT)
            .put("has_timetable", Repository(app).hasTimetable)
            .put("assist", assistEnabled(app))
        val request = Request.Builder().url("${site(app)}/api/app/heartbeat")
            .header("User-Agent", "nufe-kebiao-android/2")
            .post(body.toString().toRequestBody(JSON)).build()
        return runCatching {
            http.newCall(request).execute().use { response ->
                if (!response.isSuccessful) return@use false
                val reply = JSONObject(response.body?.string().orEmpty())
                prefs(app).edit().putLong(KEY_HEARTBEAT_AT, System.currentTimeMillis())
                    .putBoolean(KEY_AVAILABLE, reply.optBoolean("assist_available")).apply()
                true
            }
        }.getOrDefault(false)
    }
}
