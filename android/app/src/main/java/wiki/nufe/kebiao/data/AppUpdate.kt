package wiki.nufe.kebiao.data

import android.content.Context
import android.content.Intent
import android.content.pm.PackageInfo
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.core.content.FileProvider
import androidx.core.content.pm.PackageInfoCompat
import java.io.File
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import org.json.JSONArray
import wiki.nufe.kebiao.diag.Diag

/** 比当前版本新的版本。url 是发布页（自动更新不成时在浏览器里打开），apkUrl 是安装包的下载地址。 */
data class AppUpdate(val version: String, val url: String, val apkUrl: String? = null)

/** 下载到的文件不是这个 App 的新版本（包名、版本或签名对不上）：重试也没用。 */
class UpdateRejectedException(message: String) : IOException(message)

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
     * 没带 apk 的、草稿、地址不在 [pagePrefix] 下的都不算；本站镜像传自己的下载地址前缀。
     */
    fun pick(json: String, current: String, pagePrefix: String = RELEASE_PAGE): AppUpdate? {
        val releases = JSONArray(json)
        val allowPre = isPrerelease(current)
        var best: AppUpdate? = null
        for (i in 0 until releases.length()) {
            val r = releases.getJSONObject(i)
            val tag = r.optString("tag_name")
            val url = r.optString("html_url")
            if (r.optBoolean("draft") || !url.startsWith(pagePrefix)) continue
            if (r.optBoolean("prerelease") && !allowPre) continue
            val assets = r.optJSONArray("assets") ?: continue
            val apk = (0 until assets.length()).map { assets.getJSONObject(it) }
                .firstOrNull { it.optString("name").endsWith(".apk") } ?: continue
            if ((compare(tag, current) ?: continue) <= 0) continue
            // 本站镜像的 html_url 就是安装包；GitHub 的只认本仓库 Releases 下的地址
            val apkUrl = if (url.endsWith(".apk")) url
            else apk.optString("browser_download_url").takeIf { it.startsWith(RELEASE_PAGE + "download/") }
            if (best == null || (compare(tag, best.version) ?: 0) > 0) best = AppUpdate(tag, url, apkUrl)
        }
        return best
    }

    /** 依次尝试的下载地址：查到的地址，再加本站镜像（有的网络连得上 GitHub 接口却下不动安装包）。 */
    fun downloadSources(update: AppUpdate, site: String): List<String> =
        listOfNotNull(update.apkUrl, "${site.trimEnd('/')}/android/apk/${update.version}.apk").distinct()
}

/**
 * 检查更新：打开 App 时一天最多查一次，“关于”里可以手动查。先问 GitHub，连不上再问本站镜像，
 * 这样下载地址也是用户连得上的。不带课表链接等任何个人信息。
 */
class AppUpdater(context: Context) {
    private val app = context.applicationContext
    private val prefs = app.getSharedPreferences("app_update", Context.MODE_PRIVATE)
    private val updatesDir = File(app.cacheDir, "updates")

    val currentVersion: String = runCatching {
        app.packageManager.getPackageInfo(app.packageName, 0).versionName
    }.getOrNull().orEmpty().removeSuffix("-debug")

    /** 上次查到的新版本；已经升上去了就不算。 */
    fun cached(): AppUpdate? {
        val version = prefs.getString(KEY_VERSION, null) ?: return null
        val url = prefs.getString(KEY_URL, null) ?: return null
        return AppUpdate(version, url, prefs.getString(KEY_APK, null)).takeIf { (UpdateCheck.compare(version, currentVersion) ?: 0) > 0 }
    }

    val checkDue get() = System.currentTimeMillis() - prefs.getLong(KEY_LAST, 0) >= CHECK_EVERY_MS

    /** 查一次；两边都失败才抛异常，上次的结果不动。 */
    suspend fun check(): AppUpdate? = withContext(Dispatchers.IO) {
        val update = try {
            UpdateCheck.pick(read(UpdateCheck.RELEASES_API), currentVersion)
        } catch (_: Exception) {
            val site = siteUrl()
            UpdateCheck.pick(read("$site/android/releases.json"), currentVersion, "$site/android/apk/")
        }
        prefs.edit()
            .putLong(KEY_LAST, System.currentTimeMillis())
            .putString(KEY_VERSION, update?.version)
            .putString(KEY_URL, update?.url)
            .putString(KEY_APK, update?.apkUrl)
            .apply()
        update
    }

    private fun siteUrl() = DEFAULT_SITE

    /**
     * 下载新版本并确认它确实是这个 App 的新版本，返回可以安装的文件。之前下载好的直接复用。
     * 校验不过的文件不留；所有地址都失败时抛最后一个异常。
     */
    suspend fun download(update: AppUpdate, onProgress: (Int?) -> Unit): File = withContext(Dispatchers.IO) {
        val target = File(updatesDir, "${update.version.replace(Regex("[^0-9A-Za-z.-]"), "_")}.apk")
        if (target.exists()) {
            if (runCatching { verify(target) }.isSuccess) return@withContext target
            target.delete()
        }
        updatesDir.mkdirs()
        updatesDir.listFiles()?.forEach { it.delete() }
        var failure: Exception? = null
        for (source in UpdateCheck.downloadSources(update, siteUrl())) {
            val part = File(updatesDir, "part-${target.name}")
            try {
                fetch(source, part) { ensureActive(); onProgress(it) }
                verify(part)
                if (!part.renameTo(target)) throw IOException("rename failed")
                Diag.i("update downloaded ${update.version}")
                return@withContext target
            } catch (e: UpdateRejectedException) {
                part.delete()
                Diag.w("update rejected: ${e.message}")
                failure = e
            } catch (e: IOException) {
                part.delete()
                Diag.w("update download failed: ${e.javaClass.simpleName}")
                if (failure !is UpdateRejectedException) failure = e
            } catch (e: Throwable) {
                part.delete()
                throw e
            }
        }
        throw failure ?: IOException("no download source")
    }

    private fun fetch(url: String, to: File, onProgress: (Int?) -> Unit) {
        val conn = URL(url).openConnection() as HttpURLConnection
        try {
            conn.connectTimeout = 8_000
            conn.readTimeout = 20_000
            conn.setRequestProperty("User-Agent", "nufe-kebiao-android/1")
            val status = conn.responseCode
            if (status !in 200..299) throw IOException("HTTP $status")
            val total = conn.contentLengthLong
            if (total > MAX_APK_BYTES) throw UpdateRejectedException("too large")
            var done = 0L
            onProgress(if (total > 0) 0 else null)
            conn.inputStream.use { input ->
                to.outputStream().use { output ->
                    val buffer = ByteArray(32 * 1024)
                    while (true) {
                        val n = input.read(buffer)
                        if (n < 0) break
                        output.write(buffer, 0, n)
                        done += n
                        if (done > MAX_APK_BYTES) throw UpdateRejectedException("too large")
                        onProgress(if (total > 0) (done * 100 / total).toInt().coerceAtMost(100) else null)
                    }
                }
            }
            if (total > 0 && done != total) throw IOException("truncated")
        } finally {
            conn.disconnect()
        }
    }

    /** 系统安装时也会查签名，这里先查一遍，免得把用户带到一个注定失败的安装界面。 */
    private fun verify(file: File) {
        val pm = app.packageManager
        val flags = if (Build.VERSION.SDK_INT >= 28) PackageManager.GET_SIGNING_CERTIFICATES
        else @Suppress("DEPRECATION") PackageManager.GET_SIGNATURES
        val apk = pm.getPackageArchiveInfo(file.path, flags) ?: throw UpdateRejectedException("not an apk")
        val installed = pm.getPackageInfo(app.packageName, flags)
        if (apk.packageName != app.packageName) throw UpdateRejectedException("package mismatch")
        if (PackageInfoCompat.getLongVersionCode(apk) <= PackageInfoCompat.getLongVersionCode(installed)) {
            throw UpdateRejectedException("not newer")
        }
        val signers = signersOf(apk)
        if (signers.isEmpty() || signers != signersOf(installed)) throw UpdateRejectedException("signature mismatch")
    }

    private fun signersOf(info: PackageInfo): Set<String> {
        val signatures = if (Build.VERSION.SDK_INT >= 28) info.signingInfo?.apkContentsSigners
        else @Suppress("DEPRECATION") info.signatures
        return signatures.orEmpty().map { it.toCharsString() }.toSet()
    }

    /** 去系统设置要权限前记一下：允许之后系统可能重启 App，内存里的进度就没了。 */
    fun markAwaitingPermission(update: AppUpdate) = prefs.edit().putString(KEY_AWAITING, update.version).apply()

    /** 取出并清掉“要权限前正在装的版本”，只认一次。 */
    fun takeAwaitingPermission(): String? =
        prefs.getString(KEY_AWAITING, null).also { prefs.edit().remove(KEY_AWAITING).apply() }

    /** 第一次更新要用户在系统设置里允许“安装未知应用”。 */
    fun canInstall() = app.packageManager.canRequestPackageInstalls()

    fun permissionIntent() = Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${app.packageName}"))

    fun installIntent(file: File): Intent = Intent(Intent.ACTION_VIEW)
        .setDataAndType(FileProvider.getUriForFile(app, "${app.packageName}.fileprovider", file), APK_MIME)
        .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)

    /** 已经升上去之后，留在缓存里的安装包就没用了。 */
    suspend fun cleanInstalled() = withContext(Dispatchers.IO) {
        if (cached() == null) updatesDir.deleteRecursively()
    }

    private fun read(url: String): String {
        val conn = URL(url).openConnection() as HttpURLConnection
        try {
            conn.connectTimeout = 6_000
            conn.readTimeout = 10_000
            conn.setRequestProperty("Accept", "application/json")
            conn.setRequestProperty("User-Agent", "nufe-kebiao-android/1")
            val status = conn.responseCode
            if (status !in 200..299) throw IOException("HTTP $status")
            return conn.inputStream.bufferedReader().use { it.readText() }
        } finally {
            conn.disconnect()
        }
    }

    fun isDismissed(update: AppUpdate) = prefs.getString(KEY_DISMISSED, null) == update.version

    fun dismiss(update: AppUpdate) = prefs.edit().putString(KEY_DISMISSED, update.version).apply()

    private companion object {
        const val CHECK_EVERY_MS = 24 * 60 * 60_000L
        const val DEFAULT_SITE = "https://kebiao.nufe.wiki"
        const val APK_MIME = "application/vnd.android.package-archive"
        const val MAX_APK_BYTES = 64 * 1024 * 1024L
        const val KEY_LAST = "last_check"
        const val KEY_VERSION = "version"
        const val KEY_URL = "url"
        const val KEY_APK = "apk_url"
        const val KEY_DISMISSED = "dismissed"
        const val KEY_AWAITING = "awaiting_permission"
    }
}
