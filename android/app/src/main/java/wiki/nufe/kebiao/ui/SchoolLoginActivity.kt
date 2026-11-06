package wiki.nufe.kebiao.ui

import android.annotation.SuppressLint
import android.net.http.SslError
import android.os.Bundle
import android.webkit.CookieManager
import android.webkit.SslErrorHandler
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.material3.Button
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.lifecycleScope
import java.io.ByteArrayInputStream
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import org.json.JSONObject
import org.json.JSONTokener
import wiki.nufe.kebiao.data.CasSessionManager
import wiki.nufe.kebiao.data.LocalFetch
import wiki.nufe.kebiao.data.Repository
import wiki.nufe.kebiao.data.SchoolUrls
import wiki.nufe.kebiao.data.TimetableParser
import wiki.nufe.kebiao.remind.Reminders
import wiki.nufe.kebiao.sync.Sync
import kotlin.coroutines.resume

class SchoolLoginActivity : ComponentActivity() {
    private lateinit var web: WebView
    private var status by mutableStateOf("请输入学号和密码，勾选“180天免登录”；验证码登录有效期较短，优先使用密码登录。")
    private var importing by mutableStateOf(false)
    private var pageLoading by mutableStateOf(true)
    private var captureJob: Job? = null
    private var navigationCount = 0
    private var completed = false

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(android.view.WindowManager.LayoutParams.FLAG_SECURE)
        web = WebView(this).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.allowFileAccess = false
            settings.allowContentAccess = false
            settings.mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            settings.setSupportMultipleWindows(false)
            settings.javaScriptCanOpenWindowsAutomatically = false
            @Suppress("DEPRECATION")
            settings.saveFormData = false
            settings.cacheMode = WebSettings.LOAD_NO_CACHE
            CookieManager.getInstance().setAcceptCookie(true)
            CookieManager.getInstance().setAcceptThirdPartyCookies(this, false)
            webChromeClient = WebChromeClient()
            webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                    if (SchoolUrls.allowed(request.url.toString())) return false
                    status = "已阻止学校域名以外的跳转。请使用学校账号密码登录。"
                    return true
                }

                override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? {
                    if (SchoolUrls.allowed(request.url.toString())) return null
                    return WebResourceResponse("text/plain", "UTF-8", 403, "Blocked", emptyMap(), ByteArrayInputStream(ByteArray(0)))
                }

                override fun onReceivedSslError(view: WebView, handler: SslErrorHandler, error: SslError) {
                    handler.cancel()
                    status = "学校证书验证失败，已停止连接；请勿继续输入密码。"
                    pageLoading = false
                }

                override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                    if (request.isForMainFrame) {
                        status = "学校页面暂时无法打开，请检查网络后重试。"
                        pageLoading = false
                    }
                }

                override fun onPageStarted(view: WebView, url: String, favicon: android.graphics.Bitmap?) {
                    pageLoading = true
                    captureJob?.cancel()
                }

                override fun onPageFinished(view: WebView, url: String) {
                    pageLoading = false
                    if (!SchoolUrls.allowed(url) || importing || completed) return
                    val uri = android.net.Uri.parse(url)
                    if (uri.host == SchoolUrls.AUTH_HOST) applyMfaLayoutFix(view)
                    if (uri.host != SchoolUrls.JW_HOST || uri.path.orEmpty().trimEnd('/') == "/student/login") return
                    if (uri.path.orEmpty().startsWith("/student/for-std/course-table")) {
                        captureWhenReady()
                    } else if (navigationCount++ < 3) {
                        view.loadUrl(SchoolUrls.COURSE)
                    }
                }
            }
        }
        setContent {
            KebiaoTheme {
                Surface(Modifier.fillMaxSize()) {
                    Column(Modifier.fillMaxSize().windowInsetsPadding(WindowInsets.safeDrawing)) {
                        Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp), horizontalArrangement = Arrangement.SpaceBetween) {
                            TextButton(onClick = { finish() }) { Text("返回") }
                            Button(onClick = { captureWhenReady() }, enabled = !importing) { Text("导入课表") }
                        }
                        Text(status, Modifier.padding(horizontal = 16.dp, vertical = 8.dp), style = MaterialTheme.typography.bodySmall)
                        if (importing || pageLoading) LinearProgressIndicator(Modifier.fillMaxWidth())
                        AndroidView(factory = { web }, modifier = Modifier.fillMaxWidth().weight(1f))
                    }
                }
            }
        }
        lifecycleScope.launch {
            CasSessionManager.clearWebViewCookies()
            CasSessionManager(this@SchoolLoginActivity).restoreToWebView()
            web.loadUrl(SchoolUrls.SSO)
        }
    }

    private fun courseUrl(): Boolean = web.url?.let { url ->
        SchoolUrls.allowed(url) && android.net.Uri.parse(url).host == SchoolUrls.JW_HOST &&
            android.net.Uri.parse(url).path.orEmpty().startsWith("/student/for-std/course-table")
    } == true

    private fun captureWhenReady() {
        if (importing || completed) return
        if (!courseUrl()) {
            status = "请先完成学校登录，进入教务课表页面后再导入。"
            return
        }
        captureJob?.cancel()
        captureJob = lifecycleScope.launch {
            status = "正在等待学校课表数据，登录信息只加密保存在本机。"
            repeat(45) {
                if (!courseUrl()) return@launch
                val raw = evaluate(PAGE_SCRIPT)
                val html = runCatching { JSONTokener(raw).nextValue() as? String }.getOrNull().orEmpty()
                if (LocalFetch.hasCourseData(html)) {
                    if (html.length > LocalFetch.MAX_BODY_BYTES) {
                        status = "学校课表页面过大，已停止导入。"
                        return@launch
                    }
                    importPage(html)
                    return@launch
                }
                delay(1_000)
            }
            status = "尚未找到课表数据；请等待表格出现后点“导入课表”，或重新加载。"
        }
    }

    private suspend fun importPage(html: String) {
        importing = true
        status = "已进入课表，正在本机解析并保存。"
        try {
            val jar = CasSessionManager.captureWebView()
            val repo = Repository(this)
            try {
                repo.importFromWebView(html, jar, web.settings.userAgentString)
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (_: Exception) {
                if (!courseUrl()) throw IllegalStateException("学校页面已跳转")
                val data = readInPage(TimetableParser.semesterId(html))
                repo.importPageData(html, data.getString("print"), data.optString("detail").ifEmpty { null },
                    CasSessionManager.captureWebView(), web.settings.userAgentString)
            }
            completed = true
            Reminders.rescheduleAsync(this)
            Sync.schedule(this)
            Sync.redraw(this)
            status = if (jar.hasTgc) "课表已保存，登录凭据已加密。" else "课表已保存；未取得 CAS 凭据，教务会话到期后需重新登录。"
            CasSessionManager.clearWebViewCookies()
            setResult(RESULT_OK)
            finish()
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (_: Exception) {
            status = "导入失败，原课表未清除。请确认学校课表已加载，或重新登录后再试。"
        } finally {
            importing = false
        }
    }

    private suspend fun readInPage(semester: Int): JSONObject {
        val print = JSONObject.quote(SchoolUrls.printData(semester))
        val detail = JSONObject.quote(SchoolUrls.semesterDetail(semester))
        evaluate("""
            (() => {
              if (location.origin !== 'https://jwxt.nufe.edu.cn' || !location.pathname.startsWith('/student/for-std/course-table')) return;
              window.__nufeLocalImport = null;
              const read = async url => {
                const r = await fetch(url, {credentials:'same-origin', headers:{'X-Requested-With':'XMLHttpRequest'}});
                if (!r.ok || new URL(r.url).origin !== location.origin) throw new Error('fetch');
                const text = await r.text();
                if (text.length > 2097152) throw new Error('size');
                JSON.parse(text);
                return text;
              };
              Promise.all([read($print), read($detail).catch(() => '')])
                .then(([print, detail]) => window.__nufeLocalImport = {print, detail})
                .catch(() => window.__nufeLocalImport = {error:true});
            })()
        """.trimIndent())
        repeat(30) {
            delay(1_000)
            if (!courseUrl()) throw IllegalStateException("学校页面已跳转")
            val value = evaluate("JSON.stringify(window.__nufeLocalImport || null)")
            val text = JSONTokener(value).nextValue() as? String ?: return@repeat
            if (text == "null") return@repeat
            evaluate("delete window.__nufeLocalImport")
            return JSONObject(text).also { check(!it.optBoolean("error")) }
        }
        evaluate("delete window.__nufeLocalImport")
        throw IllegalStateException("学校请求超时")
    }

    private suspend fun evaluate(script: String): String = suspendCancellableCoroutine { continuation ->
        web.evaluateJavascript(script) { if (continuation.isActive) continuation.resume(it ?: "null") }
    }

    /** 学校移动登录模板会把内容容器算成 height:0 + overflow:hidden，将已渲染好的多因子卡片整块裁掉；这里内联撑开并兜住 Vue 重渲染。 */
    private fun applyMfaLayoutFix(view: WebView) {
        view.evaluateJavascript(FIX_JS, null)
    }

    override fun onDestroy() {
        captureJob?.cancel()
        web.stopLoading()
        web.clearCache(true)
        web.clearHistory()
        web.removeAllViews()
        web.destroy()
        // 不清 WebStorage：页面生成的设备指纹存在 localStorage 里，保留它学校才认这台设备、
        // 信任后不再触发多因子；会话 cookie 已加密存入 school-session.enc，这里只清 WebView 里的 cookie。
        CookieManager.getInstance().removeAllCookies { CookieManager.getInstance().flush() }
        super.onDestroy()
    }

    companion object {
        private val FIX_JS = """
            (() => {
              const sel = '.mobile-page-center-box, .mobile-page-content-box';
              const apply = () => {
                document.querySelectorAll(sel).forEach(el => {
                  if (el.style.height !== 'auto') el.style.setProperty('height', 'auto', 'important');
                  if (el.style.minHeight !== '0px') el.style.setProperty('min-height', '0', 'important');
                  if (el.style.overflow !== 'visible') el.style.setProperty('overflow', 'visible', 'important');
                });
              };
              apply();
              if (!window.__nufeFixObs) {
                window.__nufeFixObs = new MutationObserver(apply);
                window.__nufeFixObs.observe(document.body, {childList: true, subtree: true, attributes: true, attributeFilter: ['style', 'class']});
              }
              return 'applied n=' + document.querySelectorAll(sel).length;
            })()
        """.trimIndent()

        private val PAGE_SCRIPT = """
            (() => {
              if (location.origin !== 'https://jwxt.nufe.edu.cn' || !location.pathname.startsWith('/student/for-std/course-table')) return '';
              const documents = [document];
              for (const frame of document.querySelectorAll('iframe')) {
                try { if (frame.contentWindow.location.origin === location.origin) documents.push(frame.contentDocument); } catch (_) {}
              }
              for (const doc of documents) {
                const html = doc?.documentElement?.outerHTML || '';
                if (/\bpersonId\s*=\s*\d+/.test(html) && /\bcurrentSemester\s*=\s*\{/.test(html)) return html;
              }
              return '';
            })()
        """.trimIndent()
    }
}
