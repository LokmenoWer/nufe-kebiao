package wiki.nufe.kebiao.data

import android.content.Context
import android.os.Build
import java.io.IOException
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import wiki.nufe.kebiao.assist.Community

/**
 * 托管到本站（双层兜底，“人人为我”）：登录时勾选后，把这次学校登录状态加密交给 nufe课表 服务器，
 * 由服务器（以及开启了协助刷新的同学的手机）在后台继续刷新。
 *
 * - 本机刷新失败（会话过期、学校连不上）时，读本站那份课表顶上；
 * - 本站那边的登录态失效时，下次本机刷新成功就自动再交一次；
 * - 设备凭 Keystore 里不可导出的 P-256 私钥签名认证，只拿到链接的人读不到。
 *
 * 不勾选时什么都不发；随时可以在“我的”里取消（服务器删掉登录态、撤销这台设备）。
 */
object Hosting {
    private const val PREFS = "hosting"
    private const val KEY_WANTED = "wanted"
    private const val KEY_TOKEN = "token"
    private const val KEY_DEVICE = "device_id"
    private const val KEY_STATUS = "server_status"
    private const val KEY_CHECKED = "server_checked_at"
    private const val KEY_SERVER_OK = "server_ok_at"
    private const val KEY_ERROR = "server_error"
    const val RECHECK_MS = 6 * 3600_000L

    class HostingRejected(message: String) : IOException(message)

    data class State(val active: Boolean, val status: String?, val serverOkAtMs: Long, val error: String?)

    private fun prefs(context: Context) = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    /** 登录页上那个勾选框记住上次的选择；默认不勾。 */
    fun wanted(context: Context) = prefs(context).getBoolean(KEY_WANTED, false)
    fun setWanted(context: Context, value: Boolean) = prefs(context).edit().putBoolean(KEY_WANTED, value).apply()

    fun active(context: Context) = prefs(context).getString(KEY_TOKEN, null) != null &&
        prefs(context).getString(KEY_DEVICE, null) != null

    fun state(context: Context): State {
        val p = prefs(context)
        return State(active(context), p.getString(KEY_STATUS, null), p.getLong(KEY_SERVER_OK, 0L), p.getString(KEY_ERROR, null))
    }

    /** 本站那份最近 24 小时内还是好的：本机会话过期也不必催用户重新登录。 */
    fun coversForLocal(context: Context, nowMs: Long = System.currentTimeMillis()): Boolean {
        val s = state(context)
        return s.active && s.status == "ok" && nowMs - s.serverOkAtMs < 24 * 3600_000L
    }

    fun recheckDue(context: Context, nowMs: Long = System.currentTimeMillis()) =
        nowMs - prefs(context).getLong(KEY_CHECKED, 0L) > RECHECK_MS || prefs(context).getString(KEY_STATUS, null) != "ok"

    /** 把本机的学校 cookie 交给本站托管，并绑定这台设备。阻塞调用。 */
    fun enroll(context: Context, jar: SchoolCookieJar) {
        val app = context.applicationContext
        val cookies = JSONArray()
        val stored = jar.serialize()
        for (i in 0 until stored.length()) {
            val c = stored.getJSONObject(i)
            cookies.put(JSONObject().put("name", c.getString("name")).put("value", c.getString("value"))
                .put("domain", c.getString("domain")).put("path", c.getString("path"))
                .put("expires", if (c.optBoolean("persistent")) c.getLong("expiresAt") / 1000 else JSONObject.NULL))
        }
        val body = JSONObject().put("cookies", cookies).put("public_key", DeviceKeys.publicKey())
            .put("name", "${Build.MANUFACTURER} ${Build.MODEL}".trim().take(64))
        val request = Request.Builder().url("${Community.site(app)}/api/app/enroll")
            .header("User-Agent", "nufe-kebiao-android/2")
            .post(body.toString().toRequestBody(Community.JSON)).build()
        Community.http.newCall(request).execute().use { response ->
            val reply = runCatching { JSONObject(response.body?.string().orEmpty()) }.getOrDefault(JSONObject())
            if (!response.isSuccessful) throw HostingRejected(reply.optString("detail").ifEmpty { "托管失败（${response.code}）" })
            val now = System.currentTimeMillis()
            prefs(app).edit().putString(KEY_TOKEN, reply.getString("token")).putString(KEY_DEVICE, reply.getString("device_id"))
                .putString(KEY_STATUS, "ok").putLong(KEY_CHECKED, now).putLong(KEY_SERVER_OK, now)
                .remove(KEY_ERROR).putBoolean(KEY_WANTED, true).apply()
        }
    }

    /** 读本站那份课表与状态。设备已被撤销（401/404）时清掉本机的托管记录。 */
    fun pull(context: Context): JSONObject {
        val app = context.applicationContext
        val token = prefs(app).getString(KEY_TOKEN, null) ?: throw HostingRejected("未托管")
        val request = signed(app, Request.Builder().url("${Community.site(app)}/mobile/$token/timetable").get())
        Community.http.newCall(request.build()).execute().use { response ->
            if (response.code == 401 || response.code == 404) {
                forget(app, "服务器上的托管已失效，重新导入课表时勾选托管即可恢复")
                throw HostingRejected("托管已失效")
            }
            if (!response.isSuccessful) throw IOException("HTTP ${response.code}")
            val reply = JSONObject(response.body?.string().orEmpty())
            val now = System.currentTimeMillis()
            val status = reply.optString("status")
            prefs(app).edit().putString(KEY_STATUS, status).putLong(KEY_CHECKED, now)
                .putString(KEY_ERROR, reply.optString("last_error").ifEmpty { null })
                .apply { if (status == "ok") putLong(KEY_SERVER_OK, now) }.apply()
            return reply
        }
    }

    /** 取消托管：让服务器删掉登录态、撤销这台设备。网络失败也清本机记录，服务器那边的设备之后会因签名失效无人使用。 */
    fun leave(context: Context): Boolean {
        val app = context.applicationContext
        val token = prefs(app).getString(KEY_TOKEN, null) ?: return true
        val ok = runCatching {
            val request = signed(app, Request.Builder().url("${Community.site(app)}/mobile/$token/leave")
                .post(ByteArray(0).toRequestBody(null)))
            Community.http.newCall(request.build()).execute().use { it.isSuccessful }
        }.getOrDefault(false)
        forget(app, null)
        prefs(app).edit().putBoolean(KEY_WANTED, false).apply()
        return ok
    }

    private fun forget(context: Context, error: String?) {
        prefs(context).edit().remove(KEY_TOKEN).remove(KEY_DEVICE).remove(KEY_STATUS).remove(KEY_CHECKED)
            .remove(KEY_SERVER_OK).apply { if (error != null) putString(KEY_ERROR, error) else remove(KEY_ERROR) }.apply()
    }

    /** 和服务器 mobile_devices.signed_message 同一格式。 */
    private fun signed(context: Context, builder: Request.Builder): Request.Builder {
        val p = prefs(context)
        val token = p.getString(KEY_TOKEN, null).orEmpty()
        val device = p.getString(KEY_DEVICE, null).orEmpty()
        val ts = (System.currentTimeMillis() / 1000).toString()
        val sig = DeviceKeys.sign("nufe-kebiao-device-v1\n$token\n$device\n$ts".toByteArray())
        return builder.header("User-Agent", "nufe-kebiao-android/2")
            .header("X-Device-Id", device).header("X-Device-Time", ts).header("X-Device-Signature", sig)
    }
}
