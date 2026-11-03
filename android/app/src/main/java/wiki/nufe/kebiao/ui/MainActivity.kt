package wiki.nufe.kebiao.ui

import android.appwidget.AppWidgetProviderInfo
import android.content.Intent
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.collection.intSetOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.glance.appwidget.GlanceAppWidgetManager
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch
import wiki.nufe.kebiao.data.Repository
import wiki.nufe.kebiao.diag.Diag
import wiki.nufe.kebiao.widget.TodayReceiver
import wiki.nufe.kebiao.widget.WeekReceiver

class MainActivity : ComponentActivity() {
    private var incoming by mutableStateOf<String?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        Diag.i("app start")
        incoming = linkFrom(intent)
        setContent {
            KebiaoTheme {
                AppRoot(incoming) { incoming = null }
            }
        }
        publishWidgetPreviews()
    }

    /** Android 15+ 的小组件选择器支持 App 生成的预览；每天最多发一次，避免系统限流。 */
    private fun publishWidgetPreviews() {
        if (Build.VERSION.SDK_INT < 35) return
        val prefs = getSharedPreferences("widget_previews", MODE_PRIVATE)
        if (System.currentTimeMillis() - prefs.getLong("at", 0L) < 86_400_000L) return
        lifecycleScope.launch {
            runCatching {
                val categories = intSetOf(AppWidgetProviderInfo.WIDGET_CATEGORY_HOME_SCREEN)
                val manager = GlanceAppWidgetManager(this@MainActivity)
                val todayOk = manager.setWidgetPreviews(TodayReceiver::class, categories) ==
                    GlanceAppWidgetManager.SET_WIDGET_PREVIEWS_RESULT_SUCCESS
                val weekOk = manager.setWidgetPreviews(WeekReceiver::class, categories) ==
                    GlanceAppWidgetManager.SET_WIDGET_PREVIEWS_RESULT_SUCCESS
                if (todayOk && weekOk) prefs.edit().putLong("at", System.currentTimeMillis()).apply()
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        linkFrom(intent)?.let { incoming = it }
    }

    /** 网站设置页的“在安卓 App 中打开”（nufekebiao://setup?url=…&pair=…），或从别的 App 分享来的文字。 */
    private fun linkFrom(intent: Intent?): String? = when (intent?.action) {
        Intent.ACTION_VIEW -> intent.dataString
        Intent.ACTION_SEND -> intent.getStringExtra(Intent.EXTRA_TEXT)
        else -> null
    }?.takeIf { Repository.extractLink(it) != null }
}