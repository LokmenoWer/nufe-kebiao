package wiki.nufe.kebiao.preview

import android.appwidget.AppWidgetHost
import android.appwidget.AppWidgetManager
import android.content.ComponentName
import android.os.Build
import android.os.Bundle
import android.util.SizeF
import android.util.TypedValue
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch
import wiki.nufe.kebiao.data.Now
import wiki.nufe.kebiao.data.toMinutes
import wiki.nufe.kebiao.sync.Sync
import wiki.nufe.kebiao.widget.FullWeekReceiver
import wiki.nufe.kebiao.widget.TodayReceiver
import wiki.nufe.kebiao.widget.TomorrowReceiver
import wiki.nufe.kebiao.widget.WeekReceiver

/**
 * 调试用：把三种小组件按常见尺寸（含拉高、拉窄）都画出来。先授权绑定，再打开：
 *
 *     adb shell appwidget grantbind --package wiki.nufe.kebiao
 *     adb shell am start -n wiki.nufe.kebiao/.preview.WidgetPreviewActivity
 *
 * 加 `--es at 09:30` 把时钟拨到今天（北京时间）这个时刻，看上课中 / 课前倒计时等状态；
 * 加 `--es only 本周` 只画标题以它开头的几种（今天 / 明天 / 本周）。
 */
class WidgetPreviewActivity : ComponentActivity() {
    private lateinit var host: AppWidgetHost

    private val cases = listOf(
        Triple("今天 · 2×2", TodayReceiver::class.java, 160 to 160),
        Triple("今天 · 4×2", TodayReceiver::class.java, 340 to 170),
        Triple("今天 · 4×3", TodayReceiver::class.java, 340 to 260),
        Triple("明天 · 4×2", TomorrowReceiver::class.java, 340 to 170),
        Triple("本周 · 4×2", WeekReceiver::class.java, 340 to 170),
        Triple("本周 · 4×3", WeekReceiver::class.java, 340 to 260),
        Triple("本周 · 4×4", WeekReceiver::class.java, 340 to 360),
        Triple("本周 · 4×5（拉高）", WeekReceiver::class.java, 340 to 480),
        Triple("本周 · 2×3（拉窄）", WeekReceiver::class.java, 170 to 260),
        Triple("本周 · 3×3", WeekReceiver::class.java, 250 to 260),
        Triple("按时长 · 4×3", FullWeekReceiver::class.java, 340 to 260),
        Triple("按时长 · 4×4", FullWeekReceiver::class.java, 340 to 360),
    )

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Now.offsetMs = intent.getStringExtra("at")?.let { at ->
            val now = Now(System.currentTimeMillis())
            (toMinutes(at) - now.minutes) * 60_000L - (System.currentTimeMillis() / 1000 % 60) * 1000
        } ?: 0L
        host = AppWidgetHost(this, HOST_ID)
        host.deleteHost() // 每次重新分配，不留下上次的 id
        val manager = AppWidgetManager.getInstance(this)
        val column = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(48), dp(16), dp(48))
            setBackgroundColor(0xFF3A4A6B.toInt())
        }
        val only = intent.getStringExtra("only")
        for ((title, receiver, size) in cases.filter { only == null || it.first.startsWith(only) }) {
            column.addView(TextView(this).apply {
                text = title
                setTextColor(0xFFFFFFFF.toInt())
                setPadding(0, dp(12), 0, dp(4))
            })
            val id = host.allocateAppWidgetId()
            val provider = ComponentName(this, receiver)
            val (w, h) = size
            val options = Bundle().apply {
                putInt(AppWidgetManager.OPTION_APPWIDGET_MIN_WIDTH, w)
                putInt(AppWidgetManager.OPTION_APPWIDGET_MAX_WIDTH, w)
                putInt(AppWidgetManager.OPTION_APPWIDGET_MIN_HEIGHT, h)
                putInt(AppWidgetManager.OPTION_APPWIDGET_MAX_HEIGHT, h)
                if (Build.VERSION.SDK_INT >= 31) {
                    putParcelableArrayList(AppWidgetManager.OPTION_APPWIDGET_SIZES,
                        arrayListOf(SizeF(w.toFloat(), h.toFloat())))
                }
            }
            if (!manager.bindAppWidgetIdIfAllowed(id, provider, options)) {
                column.addView(TextView(this).apply {
                    text = "没有绑定权限：adb shell appwidget grantbind --package $packageName"
                    setTextColor(0xFFFFC9C9.toInt())
                })
                continue
            }
            // 桌面给小组件报的尺寸就是内容区；宿主自带的内边距会让预览比真实的小一圈
            val view = host.createView(this, id, manager.getAppWidgetInfo(id)).apply { setPadding(0, 0, 0, 0) }
            column.addView(view, LinearLayout.LayoutParams(dp(w), dp(h)))
        }
        setContentView(ScrollView(this).apply { addView(column) })
        lifecycleScope.launch { Sync.redraw(this@WidgetPreviewActivity) }
    }

    override fun onStart() {
        super.onStart()
        host.startListening()
    }

    override fun onStop() {
        super.onStop()
        host.stopListening()
    }

    private fun dp(v: Int) = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v.toFloat(), resources.displayMetrics).toInt()

    companion object {
        private const val HOST_ID = 4242
    }
}
