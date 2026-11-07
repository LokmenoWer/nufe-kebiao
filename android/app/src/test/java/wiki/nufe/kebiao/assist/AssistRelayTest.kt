package wiki.nufe.kebiao.assist

import java.net.ServerSocket
import java.net.Socket
import java.util.Base64
import java.util.concurrent.CopyOnWriteArrayList
import kotlin.concurrent.thread
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import okio.Buffer
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class AssistRelayTest {
    /** 假的项目服务器：按脚本依次回复交换请求，并记下手机交上来的事件。 */
    private class FakeServer(private val replies: List<(List<JSONObject>) -> JSONObject>) {
        val received = CopyOnWriteArrayList<JSONObject>()
        private var step = 0
        val client: OkHttpClient = OkHttpClient.Builder().addInterceptor { chain ->
            val buffer = Buffer().also { chain.request().body!!.writeTo(it) }
            val events = JSONObject(buffer.readUtf8()).getJSONArray("events")
            val list = (0 until events.length()).map { events.getJSONObject(it) }
            received += list
            val reply = synchronized(this) { replies[minOf(step++, replies.size - 1)](list) }
            Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1).code(200).message("OK")
                .body(reply.toString().toResponseBody("application/json".toMediaType())).build()
        }.build()
    }

    private fun ops(vararg op: JSONObject) = JSONObject().put("events", JSONArray(op.toList())).put("done", false)
    private fun done(outcome: String) = JSONObject().put("events", JSONArray()).put("done", true).put("outcome", outcome)
    private fun b64(s: String) = Base64.getEncoder().encodeToString(s.toByteArray())

    @Test
    fun refusesHostsOutsideTheAllowList() {
        val connected = mutableListOf<String>()
        val server = FakeServer(listOf(
            { ops(JSONObject().put("op", "open").put("conn", 1).put("host", "evil.example").put("port", 443)) },
            { ops(JSONObject().put("op", "open").put("conn", 2).put("host", "jwxt.nufe.edu.cn").put("port", 80)) },
            { done("relay_failed") },
        ))
        val relay = AssistRelay(server.client, "https://site", "s", "t") { host, port ->
            connected += "$host:$port"
            Socket()
        }
        assertEquals("relay_failed", relay.run(System.currentTimeMillis() + 5_000, 1_000_000))
        assertTrue(connected.isEmpty())
        val errors = server.received.filter { it.has("error") }.map { it.getInt("conn") to it.getString("error") }
        assertEquals(listOf(1 to "refused", 2 to "refused"), errors)
    }

    @Test
    fun carriesBytesBothWaysUnchanged() {
        // 本地“学校”：把收到的字节反转后发回再断开，验证往返的字节一个不差
        val school = ServerSocket(0)
        thread(isDaemon = true) {
            school.accept().use { s ->
                val got = ByteArray(5).also { s.getInputStream().read(it) }
                s.getOutputStream().write(got.reversedArray())
            }
        }
        val server = FakeServer(listOf(
            { ops(JSONObject().put("op", "open").put("conn", 1).put("host", "jwxt.nufe.edu.cn").put("port", 443),
                JSONObject().put("op", "data").put("conn", 1).put("data", b64("hello"))) },
            { events -> if (events.any { it.optBoolean("closed") }) done("ok") else ops() },
        ))
        val relay = AssistRelay(server.client, "https://site", "s", "t") { _, _ -> Socket("127.0.0.1", school.localPort) }
        assertEquals("ok", relay.run(System.currentTimeMillis() + 5_000, 1_000_000))
        val back = server.received.filter { it.has("data") }.joinToString("") {
            String(Base64.getDecoder().decode(it.getString("data")))
        }
        assertEquals("olleh", back)
        assertTrue(relay.bytes >= 10)
        school.close()
    }

    @Test
    fun stopsAtTheByteCap() {
        val server = FakeServer(listOf({ ops() }))
        val relay = AssistRelay(server.client, "https://site", "s", "t")
        // 没有字节也不会超额；服务器一直不说完成时，到期就放弃
        assertEquals(null, relay.run(System.currentTimeMillis() + 300, 0))
    }
}
