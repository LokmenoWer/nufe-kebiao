package wiki.nufe.kebiao.data

import android.content.Context
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray

/** GitHub Releases 上比当前版本新的版本；url 是发布页，在浏览器里打开下载，App 自己不装包。 */
data class AppUpdate(val version: String, val url: String)

object UpdateCheck {
    const val RELEASES_API = "https://api.github.com/repos/LokmenoWer/nufe-kebiao/releases?per_page=20"
    private const val RELEASE_PAGE = "https://github.com/LokmenoWer/nufe-kebiao/releases/"
    private val VERSION = Regex("""^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$""")

    /** 按语义化版本比大小：正式版比同号的预发布版新。认不出的版本（旧的 android-v… 标签）返回 null。 */
    fun compare(a: String, b: String): Int? {
        val x = VERSION.find(a) ?: return null
        val y = VERSION.find(b) ?: return null
        for (i in 1..3) {
            val diff = (x.groupValues[i].toIntOrNull() ?: return null)
                .compareTo(y.groupValues[i].toIntOrNull() ?: return null)
            if (diff != 0) return diff
        }
        return comparePre(x.groupValues[4], y.groupValues[4])
    }

    private fun comparePre(a: String, b: String): Int {
        if (a.isEmpty() || b.isEmpty()) return b.length.coerceAtMost(1) - a.length.coerceAtMost(1)
        val xs = a.split('.')
        val ys = b.split('.')
        for (i in 0 until minOf(xs.size, ys.size)) {
            val n = xs[i].toIntOrNull()
            val m = ys[i].toIntOrNull()
            val diff = when {
                n != null && m != null -> n.compareTo(m)
                n != null -> -1
                m != null -> 1
                else -> xs[i].compareTo(ys[i])
            }
            if (diff != 0) return diff
        }
        return xs.size.compareTo(ys.size)
    }

    fun isPrerelease(version: String) = VERSION.find(version)?.groupValues?.get(4)?.isNotEmpty() == true

    /**
     * 从 Releases 列表里挑最新的一个。正式版用户只看正式版，预发布版用户两种都看。
     * 没带 apk 的、草稿、地址不在本仓库发布页下的都不算。
     */
    fun pick(json: String, current: String): AppUpdate? {
        val releases = JSONArray(json)
        val allowPre = isPrerelease(current)
        var best: AppUpdate? = null
        for (i in 0 until releases.length()) {
            val r = releases.getJSONObject(i)
            val tag = r.optString("tag_name")
            val url = r.optString("html_url")
            if (r.optBoolean("draft") || !url.startsWith(RELEASE_PAGE)) continue
            if (r.optBoolean("prerelease") && !allowPre) continue
            val assets = r.optJSONArray("assets") ?: continue
            if ((0 until assets.length()).none { assets.getJSONObject(it).optString("name").endsWith(".apk") }) continue
            if ((compare(tag, current) ?: continue) <= 0) continue
            if (best == null || (compare(tag, best.version) ?: 0) > 0) best = AppUpdate(tag, url)
        }
        return best
    }
}

/** 检查更新：打开 App 时一天最多查一次，“关于”里可以手动查。只请求 GitHub，不带课表链接等任何个人信息。 */
class AppUpdater(context: Context) {
    private val app = context.applicationContext
    private val prefs = app.getSharedPreferences("app_update", Context.MODE_PRIVATE)

    val currentVersion: String = runCatching {
        app.packageManager.getPackageInfo(app.packageName, 0).versionName
    }.getOrNull().orEmpty().removeSuffix("-debug")

    /** 上次查到的新版本；已经升上去了就不算。 */
    fun cached(): AppUpdate? {
        val version = prefs.getString(KEY_VERSION, null) ?: return null
        val url = prefs.getString(KEY_URL, null) ?: return null
        return AppUpdate(version, url).takeIf { (UpdateCheck.compare(version, currentVersion) ?: 0) > 0 }
    }

    val checkDue get() = System.currentTimeMillis() - prefs.getLong(KEY_LAST, 0) >= CHECK_EVERY_MS

    /** 查一次；网络或解析失败抛异常，上次的结果不动。 */
    suspend fun check(): AppUpdate? = withContext(Dispatchers.IO) {
        val conn = URL(UpdateCheck.RELEASES_API).openConnection() as HttpURLConnection
        try {
            conn.connectTimeout = 10_000
            conn.readTimeout = 15_000
            conn.setRequestProperty("Accept", "application/vnd.github+json")
            conn.setRequestProperty("User-Agent", "nufe-kebiao-android/1")
            val status = conn.responseCode
            if (status !in 200..299) throw IOException("HTTP $status")
            val update = UpdateCheck.pick(conn.inputStream.bufferedReader().use { it.readText() }, currentVersion)
            prefs.edit()
                .putLong(KEY_LAST, System.currentTimeMillis())
                .putString(KEY_VERSION, update?.version)
                .putString(KEY_URL, update?.url)
                .apply()
            update
        } finally {
            conn.disconnect()
        }
    }

    fun isDismissed(update: AppUpdate) = prefs.getString(KEY_DISMISSED, null) == update.version

    fun dismiss(update: AppUpdate) = prefs.edit().putString(KEY_DISMISSED, update.version).apply()

    private companion object {
        const val CHECK_EVERY_MS = 24 * 60 * 60_000L
        const val KEY_LAST = "last_check"
        const val KEY_VERSION = "version"
        const val KEY_URL = "url"
        const val KEY_DISMISSED = "dismissed"
    }
}
