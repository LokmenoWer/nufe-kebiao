package wiki.nufe.kebiao.assist

import java.io.IOException
import java.net.InetSocketAddress
import java.net.Socket
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.LinkedBlockingQueue
import java.util.Base64
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicLong
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject

/**
 * 协助刷新的“盲转发”中继：照服务器的指令开 TCP 连接、把字节原样搬过去搬回来。
 *
 * 服务器与教务之间是端到端 TLS，经过这里的只有密文：看不到也改不了任何人的 cookie 或课表。
 * 即便服务器被人冒充，本机也只肯连 [ALLOWED_HOSTS] 的 443 端口，不会变成任意地址的代理。
 */
class AssistRelay(
    private val http: OkHttpClient,
    private val site: String,
    private val session: String,
    private val token: String,
    private val connect: (String, Int) -> Socket = ::openSocket,
) {
    private val sockets = ConcurrentHashMap<Int, Socket>()
    private val inbound = LinkedBlockingQueue<JSONObject>()
    private val counted = AtomicLong()
    val bytes: Long get() = counted.get()

    /** 跑到服务器说完成为止；返回服务器给的结果（"ok" 等），中途放弃返回 null。 */
    fun run(deadlineMs: Long, maxBytes: Long): String? {
        try {
            var events = mutableListOf<JSONObject>()
            while (System.currentTimeMillis() < deadlineMs && bytes <= maxBytes) {
                // 有连接开着时稍等学校的回包再交换，攒一攒少发几个请求；没连接就直接去问下一步
                val first = if (sockets.isEmpty()) inbound.poll() else inbound.poll(WAIT_MS, TimeUnit.MILLISECONDS)
                first?.let(events::add)
                inbound.drainTo(events)
                val reply = exchange(events) ?: return null
                events = mutableListOf()
                val ops = reply.optJSONArray("events") ?: JSONArray()
                for (i in 0 until ops.length()) handle(ops.optJSONObject(i) ?: continue)
                if (reply.optBoolean("done")) return reply.optString("outcome").ifEmpty { null }
            }
            return null
        } finally {
            sockets.values.forEach { runCatching { it.close() } }
            sockets.clear()
        }
    }

    private fun handle(op: JSONObject) {
        val conn = op.optInt("conn", -1)
        if (conn < 1) return
        when (op.optString("op")) {
            "open" -> {
                val host = op.optString("host")
                val port = op.optInt("port")
                if (host !in ALLOWED_HOSTS || port != 443) {
                    inbound += JSONObject().put("conn", conn).put("error", "refused")
                    return
                }
                val socket = try {
                    connect(host, port)
                } catch (_: IOException) {
                    inbound += JSONObject().put("conn", conn).put("error", "connect")
                    return
                }
                sockets[conn] = socket
                Thread({ pump(conn, socket) }, "assist-read-$conn").apply { isDaemon = true }.start()
            }
            "data" -> {
                val socket = sockets[conn] ?: return
                val raw = try {
                    Base64.getDecoder().decode(op.optString("data"))
                } catch (_: IllegalArgumentException) {
                    return
                }
                counted.addAndGet(raw.size.toLong())
                try {
                    socket.getOutputStream().apply { write(raw); flush() }
                } catch (_: IOException) {
                    sockets.remove(conn)?.let { runCatching { it.close() } }
                    inbound += JSONObject().put("conn", conn).put("error", "write")
                }
            }
            "close" -> sockets.remove(conn)?.let { runCatching { it.close() } }
        }
    }

    private fun pump(conn: Int, socket: Socket) {
        val buffer = ByteArray(32 * 1024)
        try {
            val input = socket.getInputStream()
            while (true) {
                val n = input.read(buffer)
                if (n < 0) break
                counted.addAndGet(n.toLong())
                inbound += JSONObject().put("conn", conn).put("data", Base64.getEncoder().encodeToString(buffer.copyOf(n)))
            }
        } catch (_: IOException) {
            // 我们自己关的（服务器说 close）就不必再报
        }
        if (sockets.remove(conn) != null) inbound += JSONObject().put("conn", conn).put("closed", true)
    }

    private fun exchange(events: List<JSONObject>): JSONObject? {
        val body = JSONObject().put("session", session).put("token", token).put("events", JSONArray(events))
        val request = Request.Builder().url("$site/api/assist/exchange")
            .header("User-Agent", "nufe-kebiao-android/2")
            .post(body.toString().toRequestBody(Community.JSON)).build()
        return try {
            http.newCall(request).execute().use { response ->
                if (!response.isSuccessful) null else JSONObject(response.body?.string().orEmpty())
            }
        } catch (_: Exception) {
            null
        }
    }

    companion object {
        val ALLOWED_HOSTS = setOf("jwxt.nufe.edu.cn")
        private const val WAIT_MS = 150L

        fun openSocket(host: String, port: Int): Socket = Socket().apply {
            tcpNoDelay = true
            connect(InetSocketAddress(host, port), 8_000)
            soTimeout = 20_000
        }
    }
}
