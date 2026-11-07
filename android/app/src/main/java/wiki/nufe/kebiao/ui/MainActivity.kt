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
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import wiki.nufe.kebiao.diag.Diag
import wiki.nufe.kebiao.remind.LiveUpdates
import wiki.nufe.kebiao.remind.Reminders
import wiki.nufe.kebiao.sync.Sync
import wiki.nufe.kebiao.widget.TodayReceiver
import wiki.nufe.kebiao.widget.WeekReceiver

class MainActivity : ComponentActivity() {
    private var loginRequested by mutableStateOf(false)
    private var widgetView by mutableStateOf<String?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        Diag.i("app start")
        loginRequested = intent?.getBooleanExtra(EXTRA_LOGIN, false) == true
        intent?.removeExtra(EXTRA_LOGIN)
        widgetView = intent?.getStringExtra(EXTRA_VIEW)
        intent?.removeExtra(EXTRA_VIEW)
        setContent {
            KebiaoTheme {
                AppRoot(
                    loginRequested = loginRequested,
                    widgetView = widgetView,
                    consumeLoginRequest = { loginRequested = false },
                    consumeWidgetView = { widgetView = null },
                )
            }
        }
        // 强制停止、覆盖安装都会清掉 AlarmManager 队列；每次打开都按缓存补回来。
        lifecycleScope.launch(Dispatchers.IO) {
            Reminders.reschedule(this@MainActivity)
            Sync.armBoundary(this@MainActivity)
            Sync.schedule(this@MainActivity)
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

    override fun onResume() {
        super.onResume()
        // 当前正在展示流体云时补上前台守护，清理后台时不会连通知一起被结束。
        LiveUpdates.ensureGuard(this)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        if (intent.getBooleanExtra(EXTRA_LOGIN, false)) loginRequested = true
        intent.removeExtra(EXTRA_LOGIN)
        intent.getStringExtra(EXTRA_VIEW)?.let { widgetView = it }
        intent.removeExtra(EXTRA_VIEW)
        setIntent(intent)
    }

    companion object {
        const val EXTRA_VIEW = "wiki.nufe.kebiao.extra.VIEW"
        const val EXTRA_LOGIN = "login"
    }
}
