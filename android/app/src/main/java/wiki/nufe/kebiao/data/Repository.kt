package wiki.nufe.kebiao.data

import android.content.Context
import android.util.AtomicFile
import java.io.File
import java.io.IOException
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import org.json.JSONObject
import wiki.nufe.kebiao.diag.Diag
import wiki.nufe.kebiao.sync.ChangeAlert
import wiki.nufe.kebiao.sync.LoginAlert

data class Snapshot(val payload: Payload, val fetchedAtMs: Long, val offline: Boolean)

class Repository(context: Context) {
    private val app = context.applicationContext
    private val prefs = app.getSharedPreferences("kebiao", Context.MODE_PRIVATE)
    private val timetableFile = AtomicFile(File(app.filesDir, "timetable.enc"))
    private val legacyCache = File(app.filesDir, "widget.json")
    private val session = CasSessionManager(app)

    init {
        if (prefs.contains("widget_url") || prefs.contains("device_id")) {
            prefs.edit().remove("widget_url").remove("device_id").remove("device_clock_offset")
                .remove("link_invalid").remove("widget_opens_app").putBoolean("needs_login", true).apply()
            File(app.filesDir, "profile.enc").delete()
        }
    }

    val hasTimetable: Boolean get() = timetableFile.baseFile.exists() || legacyCache.exists()
    val needsLogin: Boolean get() = !session.exists || prefs.getBoolean("needs_login", false)
    val lastError: String? get() = prefs.getString("local_error", null)

    var remindMinutes: Int
        get() = prefs.getInt("remind_minutes", DEFAULT_REMIND_MINUTES)
        set(value) = prefs.edit().putInt("remind_minutes", value).apply()

    var alarmEnabled: Boolean
        get() = prefs.getBoolean("alarm_enabled", remindMinutes > 0)
        set(value) = prefs.edit().putBoolean("alarm_enabled", value).apply()

    var liveUpdateEnabled: Boolean
        get() = prefs.getBoolean("live_update_enabled", false)
        set(value) = prefs.edit().putBoolean("live_update_enabled", value).apply()

    /** 首页“今日节奏”表盘用 12 小时制（与真实钟表一致）；false 为 24 小时制整天一圈。 */
    var rhythmClock12h: Boolean
        get() = prefs.getBoolean(KEY_RHYTHM_12H, true)
        set(value) = prefs.edit().putBoolean(KEY_RHYTHM_12H, value).apply()

    /**
     * 小组件上下课重画用闹钟时钟：部分 ROM 上更准时，但状态栏可能显示闹钟图标。
     * 第一次读取时按当时的上课通知定初值并存下来：开着通知的本来就有闹钟图标，自动打开；
     * 之后两个开关互不影响，关掉上课通知不会连带关掉它。
     */
    var widgetPunctual: Boolean
        get() {
            if (prefs.contains("widget_punctual")) return prefs.getBoolean("widget_punctual", false)
            return alarmEnabled.also { widgetPunctual = it }
        }
        set(value) = prefs.edit().putBoolean("widget_punctual", value).apply()

    private fun stored(): JSONObject? = synchronized(cacheLock) {
        if (!timetableFile.baseFile.exists()) return null
        try {
            JSONObject(String(DeviceKeys.decrypt(timetableFile.readFully(), PURPOSE), Charsets.UTF_8))
        } catch (_: Exception) {
            prefs.edit().putBoolean("needs_login", true).putString("local_error", "本地课表无法读取，请重新登录导入").apply()
            null
        }
    }

    fun cached(): Snapshot? {
        val data = stored()
        if (data != null) return Snapshot(
            // 本机会话过期但本站托管那份还好：不催重新登录（双层兜底的意义）
            TimetableParser.payload(data, nowMs = Now.current().ms,
                status = if (needsLogin && !Hosting.coversForLocal(app)) "needs_rebind" else "ok"),
            data.optLong("updated_at") * 1000,
            offline = prefs.getBoolean("local_offline", false),
        )
        return synchronized(cacheLock) {
            if (!legacyCache.exists()) return null
            runCatching {
                Snapshot(Payload.parse(legacyCache.readText()).copy(status = "needs_rebind", delegate = false, pollAfterSeconds = null),
                    legacyCache.lastModified(), offline = true)
            }.getOrNull()
        }
    }

    suspend fun fetch(refresh: Boolean = false): Snapshot? = withContext(Dispatchers.IO) {
        syncMutex.withLock {
            if (needsLogin) {
                hostedFallback(generation.get())
                return@withLock cached().also { it?.let { LoginAlert.update(app, it.payload) } }
            }
            val current = cached()
            if (!refresh && current != null && !current.offline && System.currentTimeMillis() - current.fetchedAtMs < 2 * 60_000L) {
                return@withLock current
            }
            val epoch = generation.get()
            try {
                val jar = try { session.load() } catch (_: Exception) { throw SchoolSessionExpired() }
                val client = LocalFetch(jar, userAgent)
                val previous = stored()
                val timetable = try {
                    client.collect(previous = previous)
                } catch (_: SchoolSessionExpired) {
                    client.collect(client.renewWithTgc(), previous)
                }
                // 抓取期间用户可能已“清除本机数据”：代数变了就丢弃这次结果，不复活课表与登录态。
                if (generation.get() != epoch) return@withLock null
                // 后台自动刷新遇到同学期“非空→空”多半是教务未发布或会话半失效，宁可保留旧课表，
                // 让用户在“我的 → 重新登录/重新导入”里主动确认（WebView 导入仍允许显式空表）。
                if (TimetableParser.wouldWipeCurrentSemester(previous, timetable)) {
                    Diag.w("school returned empty timetable for current semester; keeping previous")
                    prefs.edit().putBoolean("local_offline", true)
                        .putString("local_error", "学校本次未返回课程，已保留原课表；如确已结课请重新登录导入。").apply()
                    return@withLock cached()?.copy(offline = true)
                }
                if (!saveTimetable(timetable, epoch)) return@withLock null
                // 保活探针：认证中心干净地拒绝签票（非网络错）说明 CASTGC 已失效，
                // 下次教务会话过期就无法静默续期，主动提示重登（网络/5xx 异常按未知处理，不误判）。
                val keepalive = runCatching { client.touchSso() }.getOrDefault(true)
                if (!keepalive) Diag.w("sso keepalive refused; CASTGC likely expired")
                prefs.edit().putBoolean("needs_login", !keepalive).putBoolean("local_offline", false).remove("local_error").apply()
                syncHosting(jar)
                cached().also { it?.let { snap ->
                    LoginAlert.update(app, snap.payload)
                    ChangeAlert.update(app, snap.payload)
                } }
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (_: SchoolSessionExpired) {
                prefs.edit().putBoolean("needs_login", true).putBoolean("local_offline", true)
                    .putString("local_error", "学校登录已失效，请重新登录；本地课表仍可离线查看").apply()
                hostedFallback(epoch)
                cached().also { it?.let { LoginAlert.update(app, it.payload) } }
            } catch (error: Exception) {
                Diag.w("local refresh failed: ${error.javaClass.simpleName}")
                val message = when (error) {
                    is SchoolPageException -> error.message
                    is IOException -> "暂时无法连接学校，已保留本地课表"
                    else -> "课表解析失败，已保留本地课表；请尝试重新登录导入"
                }
                prefs.edit().putBoolean("local_offline", true).putString("local_error", message).apply()
                if (hostedFallback(epoch)) cached() else cached()?.copy(offline = true)
            }
        }
    }

    /**
     * 双层兜底之一：本机刷新失败时，若已托管且本站那份比本机新，就用本站的。返回是否换上了本站的课表。
     * 只在 syncMutex 里调用。
     */
    private fun hostedFallback(epoch: Long): Boolean {
        if (!Hosting.active(app)) return false
        val server = try {
            Hosting.pull(app)
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (_: Exception) {
            return false
        }
        val previous = stored()
        val fetchedAt = server.optDouble("fetched_at", 0.0).toLong()
        if (fetchedAt <= 0 || (previous != null && fetchedAt <= previous.optLong("updated_at"))) return false
        val timetable = runCatching { TimetableParser.fromServer(server, previous) }.getOrNull() ?: return false
        if (TimetableParser.wouldWipeCurrentSemester(previous, timetable)) return false
        if (!saveTimetable(timetable, epoch)) return false
        Diag.i("timetable updated from hosted server copy")
        prefs.edit().putBoolean("local_offline", false)
            .putString("local_error", "本机未能直连学校，已用 nufe课表 服务器托管的课表更新").apply()
        cached()?.let { ChangeAlert.update(app, it.payload) }
        return true
    }

    /** 双层兜底之二：本机刚刷新成功、而本站那边的登录态坏了，就把本机的新会话再交一次。 */
    private fun syncHosting(jar: SchoolCookieJar) {
        if (!Hosting.active(app) || !Hosting.recheckDue(app)) return
        runCatching {
            val server = Hosting.pull(app)
            if (server.optString("status") != "ok") {
                Hosting.enroll(app, jar)
                Diag.i("hosting re-enrolled with fresh local session")
            }
        }.onFailure { Diag.w("hosting sync failed: ${it.javaClass.simpleName}") }
    }

    suspend fun importFromWebView(html: String, jar: SchoolCookieJar, agent: String): Snapshot = withContext(Dispatchers.IO) {
        syncMutex.withLock {
            val epoch = generation.get()
            commitImport(LocalFetch(jar, agent).collect(html), jar, agent, epoch)
        }
    }

    suspend fun importPageData(html: String, printData: String, detail: String?, jar: SchoolCookieJar, agent: String): Snapshot =
        withContext(Dispatchers.IO) {
            syncMutex.withLock {
                val epoch = generation.get()
                commitImport(TimetableParser.parse(html, printData, detail), jar, agent, epoch)
            }
        }

    private fun commitImport(timetable: JSONObject, jar: SchoolCookieJar, agent: String, epoch: Long): Snapshot {
        // 与 fetch 同理：解析/导入期间被清除就不写回，避免复活旧数据与登录态。
        if (!saveTimetable(timetable, epoch)) throw SchoolPageException("本机数据已清除，请重新登录后再导入")
        session.save(jar.serialize())
        // 只有真取到学校凭据才算登录成功；抓到空 jar 时保持需登录，别让 App 以为已连上。
        prefs.edit().putString("school_user_agent", agent).putBoolean("needs_login", !jar.hasSession)
            .putBoolean("local_offline", false).remove("local_error").remove("change_alert_at").apply()
        LoginAlert.clear(app)
        return requireNotNull(cached())
    }

    /** 在缓存锁内复核代数后落盘：clearLocalData 也持这把锁，二者不会交错写回已清除的数据。 */
    private fun saveTimetable(timetable: JSONObject, epoch: Long): Boolean = synchronized(cacheLock) {
        if (generation.get() != epoch) return false
        val bytes = DeviceKeys.encrypt(timetable.toString().toByteArray(Charsets.UTF_8), PURPOSE)
        val output = timetableFile.startWrite()
        try {
            output.write(bytes)
            timetableFile.finishWrite(output)
        } catch (error: Exception) {
            timetableFile.failWrite(output)
            throw error
        }
        legacyCache.delete()
        true
    }

    private val userAgent: String get() = prefs.getString("school_user_agent", null)
        ?: "Mozilla/5.0 (Linux; Android) AppleWebKit/537.36 Chrome/130.0.0.0 Mobile Safari/537.36"

    suspend fun cachedProfile(): Profile? = withContext(Dispatchers.IO) {
        stored()?.optJSONObject("student")?.let { Profile(StudentInfo.parse(it).takeIf { student -> student.hasData }, null) }
    }

    fun clearLocalData() = synchronized(cacheLock) {
        generation.incrementAndGet()
        timetableFile.delete()
        legacyCache.delete()
        session.clear()
        File(app.filesDir, "profile.enc").delete()
        prefs.edit().remove("school_user_agent").remove("local_error").remove("local_offline")
            .remove("change_alert_at").remove("change_seen").putBoolean("needs_login", true).apply()
        DeviceKeys.reset()
        if (Hosting.active(app)) {
            // 先签最后一次“取消托管”，再删签名密钥；放到后台线程，不卡清除按钮
            Thread({ Hosting.leave(app); DeviceKeys.resetSigning() }, "hosting-leave").start()
        } else {
            DeviceKeys.resetSigning()
        }
        LoginAlert.clear(app)
        ChangeAlert.clear(app)
    }

    companion object {
        private const val PURPOSE = "local-timetable-v1"
        private val cacheLock = Any()
        private const val KEY_RHYTHM_12H = "rhythm_clock_12h"
        private val syncMutex = Mutex()
        private val generation = java.util.concurrent.atomic.AtomicLong()
        const val DEFAULT_REMIND_MINUTES = 10
    }
}
