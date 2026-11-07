package wiki.nufe.kebiao.widget

import android.content.Context
import android.content.Intent
import android.os.SystemClock
import android.widget.RemoteViews
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.glance.ColorFilter
import androidx.glance.GlanceId
import androidx.glance.GlanceModifier
import androidx.glance.GlanceTheme
import androidx.glance.Image
import androidx.glance.ImageProvider
import androidx.glance.color.colorProviders
import androidx.glance.action.Action
import androidx.glance.action.ActionParameters
import androidx.glance.action.clickable
import androidx.glance.appwidget.AndroidRemoteViews
import androidx.glance.appwidget.action.ActionCallback
import androidx.glance.appwidget.action.actionRunCallback
import androidx.glance.appwidget.action.actionStartActivity
import androidx.glance.appwidget.appWidgetBackground
import androidx.glance.appwidget.cornerRadius
import androidx.glance.background
import androidx.glance.color.ColorProvider
import androidx.glance.layout.Alignment
import androidx.glance.layout.Box
import androidx.glance.layout.Column
import androidx.glance.layout.Row
import androidx.glance.layout.Spacer
import androidx.glance.layout.fillMaxHeight
import androidx.glance.layout.fillMaxSize
import androidx.glance.layout.fillMaxWidth
import androidx.glance.layout.height
import androidx.glance.layout.padding
import androidx.glance.layout.size
import androidx.glance.layout.width
import androidx.glance.text.FontWeight
import androidx.glance.text.Text
import androidx.glance.text.TextStyle
import androidx.glance.unit.ColorProvider
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.update
import wiki.nufe.kebiao.R
import wiki.nufe.kebiao.data.Now
import wiki.nufe.kebiao.data.Note
import wiki.nufe.kebiao.data.Repository
import wiki.nufe.kebiao.data.Snapshot
import wiki.nufe.kebiao.data.Tone
import wiki.nufe.kebiao.sync.Sync
import wiki.nufe.kebiao.ui.MainActivity

/** 只读本机课表与登录状态；会话过期不妨碍绘制缓存。 */
data class WidgetData(
    val snapshot: Snapshot?,
    val hasTimetable: Boolean,
    val needsLogin: Boolean,
)

fun loadData(context: Context): WidgetData {
    val repo = Repository(context)
    val snapshot = repo.cached()
    return WidgetData(snapshot, repo.hasTimetable, repo.needsLogin)
}

/**
 * Glance 会把小组件的会话留着一阵子，期间 updateAll 只让已有会话重组、不会重跑 provideGlance。
 * 所以数据不能在 provideGlance 里读一次就攥着（重新导入后也还画旧课表）：[Sync.redraw] 先把版本号加一，
 * 会话里按版本号重读缓存。
 */
object WidgetVersion {
    val flow = MutableStateFlow(0)
    fun bump() = flow.update { it + 1 }
}

@Composable
fun rememberWidgetData(context: Context): WidgetData {
    val version by WidgetVersion.flow.collectAsState()
    return remember(version) { loadData(context) }
}

val PADDING = 12.dp

// 和 Scriptable 脚本同一套强调色：正在上课用红色，警告用橙色
val LIVE = Color(0xFFE03131)
val WARN = Color(0xFFE8590C)
val ACCENT = Color(0xFF4B63E0)
val REST = Color(0xFF2F9E44)   // 没课、可以休息时的绿色

fun parseColor(hex: String): Color = try {
    Color(android.graphics.Color.parseColor(hex))
} catch (_: IllegalArgumentException) {
    ACCENT
}

fun mix(a: Color, b: Color, t: Float) = Color(
    red = a.red + (b.red - a.red) * t,
    green = a.green + (b.green - a.green) * t,
    blue = a.blue + (b.blue - a.blue) * t,
)

private fun widgetColor(day: Long, night: Long) =
    ColorProvider(day = Color(day.toInt()), night = Color(night.toInt()))

private val DAY_BG = Color(0xFFF6F5F2)   // 不是纯白，避免桌面太刺眼
private val NIGHT_BG = Color(0xFF1C1C1E) // 浅黑，不跟随壁纸偏蓝

/** 小组件固定配色：不跟随系统动态取色，避免 HyperOS / MIUI 用壁纸颜色把卡片染绿。 */
internal val WidgetColors = colorProviders(
    primary = widgetColor(0xFF111111, 0xFFF5F5F7),
    onPrimary = widgetColor(0xFFFFFFFF, 0xFF111111),
    primaryContainer = widgetColor(0xFFF0F0F2, 0xFF2A2A2E),
    onPrimaryContainer = widgetColor(0xFF111111, 0xFFF5F5F7),
    secondary = widgetColor(0xFF4B63E0, 0xFFB9C3FF),
    onSecondary = widgetColor(0xFFFFFFFF, 0xFF111111),
    secondaryContainer = widgetColor(0xFFE8ECFF, 0xFF2B3550),
    onSecondaryContainer = widgetColor(0xFF111111, 0xFFE8ECFF),
    tertiary = widgetColor(0xFF4B63E0, 0xFFB9C3FF),
    onTertiary = widgetColor(0xFFFFFFFF, 0xFF111111),
    tertiaryContainer = widgetColor(0xFFE8ECFF, 0xFF2B3550),
    onTertiaryContainer = widgetColor(0xFF111111, 0xFFE8ECFF),
    error = widgetColor(0xFFD64545, 0xFFFF8A8A),
    errorContainer = widgetColor(0xFFFFE3E3, 0xFF4A1D1D),
    onError = widgetColor(0xFFFFFFFF, 0xFF111111),
    onErrorContainer = widgetColor(0xFF5F1A1A, 0xFFFFDAD6),
    background = widgetColor(0xFFF6F5F2, 0xFF1C1C1E),
    onBackground = widgetColor(0xFF111111, 0xFFF5F5F7),
    surface = widgetColor(0xFFF6F5F2, 0xFF1C1C1E),
    onSurface = widgetColor(0xFF111111, 0xFFF5F5F7),
    surfaceVariant = widgetColor(0xFFF2F2F4, 0xFF2A2A2E),
    onSurfaceVariant = widgetColor(0xFF6B6B70, 0xFFA6A6AC),
    outline = widgetColor(0xFFE6E6E9, 0xFF35353A),
    inverseOnSurface = widgetColor(0xFFF5F5F7, 0xFF111111),
    inverseSurface = widgetColor(0xFF1A1A1D, 0xFFF5F5F7),
    inversePrimary = widgetColor(0xFFB9C3FF, 0xFF4B63E0),
    widgetBackground = widgetColor(0xFFF6F5F2, 0xFF1C1C1E),
)

/** 课程卡片底色：浅色模式淡淡一层课程色，深色模式压暗。 */
fun chipBackground(hex: String): ColorProvider {
    val c = parseColor(hex)
    return ColorProvider(day = mix(c, Color.White, 0.84f), night = mix(c, NIGHT_BG, 0.7f))
}

fun chipText(hex: String): ColorProvider {
    val c = parseColor(hex)
    return ColorProvider(day = mix(c, Color.Black, 0.4f), night = mix(c, Color.White, 0.55f))
}

fun openApp(context: Context, view: String? = null): Action {
    val intent = Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    view?.let { intent.putExtra(MainActivity.EXTRA_VIEW, it) }
    return actionStartActivity(intent)
}

fun solid(color: Color): ColorProvider = ColorProvider(day = color, night = color)
class RefreshAction : ActionCallback {
    override suspend fun onAction(context: Context, glanceId: GlanceId, parameters: ActionParameters) {
        Sync.now(context, refresh = true)
    }
}

@Composable
fun WidgetRoot(onClick: Action, content: @Composable () -> Unit) {
    Box(
        modifier = GlanceModifier.fillMaxSize().appWidgetBackground().cornerRadius(20.dp)
            .background(GlanceTheme.colors.widgetBackground).padding(PADDING).clickable(onClick),
        contentAlignment = Alignment.TopStart,
    ) { content() }
}

@Composable
fun Label(
    text: String,
    size: TextUnit = 12.sp,
    color: ColorProvider = GlanceTheme.colors.onSurface,
    bold: Boolean = false,
    lines: Int = 1,
    modifier: GlanceModifier = GlanceModifier,
) {
    Text(
        text = text,
        modifier = modifier,
        maxLines = lines,
        style = TextStyle(color = color, fontSize = size, fontWeight = if (bold) FontWeight.Bold else FontWeight.Normal),
    )
}

@Composable
fun noteColor(tone: Tone): ColorProvider = when (tone) {
    Tone.WARN -> solid(WARN)
    Tone.ACCENT -> GlanceTheme.colors.primary
    Tone.FAINT -> GlanceTheme.colors.onSurfaceVariant
}

/** 标题栏：粗体标题、副标题（可自定义颜色/加粗），右边一句提示和刷新按钮。 */
@Composable
fun Header(title: String, subtitle: String, note: Note?, compact: Boolean = false,
           subtitleColor: ColorProvider = GlanceTheme.colors.onSurfaceVariant, subtitleBold: Boolean = false) {
    Row(modifier = GlanceModifier.fillMaxWidth().height(22.dp), verticalAlignment = Alignment.CenterVertically) {
        Label(title, size = if (compact) 13.sp else 14.sp, bold = true, color = GlanceTheme.colors.primary)
        if (subtitle.isNotEmpty()) {
            Spacer(GlanceModifier.width(6.dp))
            Label(subtitle, size = 12.sp, bold = subtitleBold, color = subtitleColor, lines = 1)
        }
        Spacer(GlanceModifier.defaultWeight())
        if (note != null) Label(note.text, size = 11.sp, color = noteColor(note.tone))
        if (!compact) {
            Spacer(GlanceModifier.width(6.dp))
            Image(
                provider = ImageProvider(R.drawable.ic_refresh),
                contentDescription = "刷新课表",
                colorFilter = ColorFilter.tint(GlanceTheme.colors.onSurfaceVariant),
                modifier = GlanceModifier.size(18.dp).clickable(actionRunCallback<RefreshAction>()),
            )
        }
    }
}

/** 细分隔线：把"没课休息"和"下次上课"两块内容隔开，避免误读。 */
@Composable
fun WidgetDivider(modifier: GlanceModifier = GlanceModifier.fillMaxWidth()) {
    Spacer(modifier.height(1.dp).background(GlanceTheme.colors.outline))
}

/** 系统自己走的倒计时（Chronometer），实时刷新，不需要每秒重画小组件。 */
@Composable
fun Countdown(context: Context, targetMs: Long, color: Color) {
    val views = RemoteViews(context.packageName, R.layout.countdown).apply {
        val base = SystemClock.elapsedRealtime() + (targetMs - Now.current().ms)
        setChronometer(R.id.chrono, base, null, true)
        setChronometerCountDown(R.id.chrono, true)
        setTextColor(R.id.chrono, android.graphics.Color.argb(255, (color.red * 255).toInt(), (color.green * 255).toInt(), (color.blue * 255).toInt()))
    }
    AndroidRemoteViews(views)
}

/** 只在没有本机课表时显示引导，离线或登录过期仍展示缓存。 */
fun placeholderMessage(data: WidgetData): String? =
    if (data.snapshot == null) "在App登录学校账号" else null

@Composable
fun Placeholder(context: Context, message: String) {
    WidgetRoot(onClick = openApp(context)) {
        Column(modifier = GlanceModifier.fillMaxSize(), verticalAlignment = Alignment.CenterVertically,
            horizontalAlignment = Alignment.CenterHorizontally) {
            Label("nufe课表", size = 14.sp, bold = true, color = GlanceTheme.colors.primary)
            Spacer(GlanceModifier.height(4.dp))
            Label(message, size = 12.sp, color = GlanceTheme.colors.onSurfaceVariant, lines = 2)
        }
    }
}

/** 竖条 + 内容的课程卡片骨架。 */
@Composable
fun Chip(color: String, height: Dp?, modifier: GlanceModifier = GlanceModifier, strong: Boolean = false,
         dim: Boolean = false, tight: Boolean = false, content: @Composable () -> Unit) {
    val bg = when {
        strong -> solid(parseColor(color))
        dim -> GlanceTheme.colors.surfaceVariant
        else -> chipBackground(color)
    }
    val sized = if (height != null) modifier.height(height) else modifier
    Row(modifier = sized.fillMaxWidth().cornerRadius(6.dp).background(bg)) {
        Spacer(GlanceModifier.width(3.dp).fillMaxHeight().background(if (dim) GlanceTheme.colors.outline else solid(parseColor(color))))
        // tight：格子矮得放不下上下边距时去掉它，至少写得下一行课名
        Box(modifier = GlanceModifier.padding(horizontal = 4.dp, vertical = if (tight) 0.dp else 2.dp)) { content() }
    }
}
