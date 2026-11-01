package wiki.nufe.kebiao.widget

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.SystemClock
import android.widget.RemoteViews
import androidx.compose.runtime.Composable
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
import wiki.nufe.kebiao.R
import wiki.nufe.kebiao.data.Day
import wiki.nufe.kebiao.data.Now
import wiki.nufe.kebiao.data.Note
import wiki.nufe.kebiao.data.Repository
import wiki.nufe.kebiao.data.Snapshot
import wiki.nufe.kebiao.data.Tone
import wiki.nufe.kebiao.data.siteLink
import wiki.nufe.kebiao.sync.LinkState
import wiki.nufe.kebiao.sync.Sync
import wiki.nufe.kebiao.ui.MainActivity

/** 小组件画图需要的一切：缓存的课表、网站地址、链接是否失效。 */
data class WidgetData(val snapshot: Snapshot?, val site: String?, val hasLink: Boolean, val linkInvalid: Boolean)

fun loadData(context: Context): WidgetData {
    val repo = Repository(context)
    return WidgetData(repo.cached(), repo.siteUrl(), repo.widgetUrl != null, LinkState.isInvalid(context))
}

val PADDING = 12.dp

// 和 Scriptable 脚本同一套强调色：正在上课用红色，警告用橙色
val LIVE = Color(0xFFE03131)
val WARN = Color(0xFFE8590C)
val ACCENT = Color(0xFF4B63E0)

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

private val NIGHT_BG = Color(0xFF16181D)

/** 课程卡片底色：浅色模式淡淡一层课程色，深色模式压暗。 */
fun chipBackground(hex: String): ColorProvider {
    val c = parseColor(hex)
    return ColorProvider(day = mix(c, Color.White, 0.84f), night = mix(c, NIGHT_BG, 0.7f))
}

fun chipText(hex: String): ColorProvider {
    val c = parseColor(hex)
    return ColorProvider(day = mix(c, Color.Black, 0.4f), night = mix(c, Color.White, 0.55f))
}

fun solid(color: Color): ColorProvider = ColorProvider(day = color, night = color)

fun openUrl(url: String): Action =
    actionStartActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))

fun openApp(context: Context): Action =
    actionStartActivity(Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))

/** 点小组件：打开网站上对应那天 / 那一周；没有网站地址时打开 App。 */
fun openDay(context: Context, data: WidgetData, day: Day?, view: String = "day"): Action {
    val snap = data.snapshot
    val site = data.site
    return if (site != null && snap != null) openUrl(siteLink(site, snap.payload, day, view)) else openApp(context)
}

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

/** 标题栏：粗体标题、灰色副标题，右边一句提示和刷新按钮。 */
@Composable
fun Header(title: String, subtitle: String, note: Note?, compact: Boolean = false) {
    Row(modifier = GlanceModifier.fillMaxWidth().height(22.dp), verticalAlignment = Alignment.CenterVertically) {
        Label(title, size = if (compact) 13.sp else 14.sp, bold = true, color = GlanceTheme.colors.primary)
        if (subtitle.isNotEmpty()) {
            Spacer(GlanceModifier.width(6.dp))
            Label(subtitle, size = 12.sp, color = GlanceTheme.colors.onSurfaceVariant)
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

/** 系统自己走的倒计时（Chronometer），不需要每秒重画小组件。 */
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

/** 没有链接 / 链接失效 / 还没拿到数据时显示的话；能正常画课表时为 null。 */
fun placeholderMessage(data: WidgetData): String? = when {
    !data.hasLink -> "点按设置小组件链接"
    data.linkInvalid -> "链接已失效，点按重新粘贴"
    data.snapshot == null -> "正在获取课表…"
    else -> null
}

@Composable
fun Placeholder(context: Context, message: String) {
    WidgetRoot(onClick = openApp(context)) {
        Column(modifier = GlanceModifier.fillMaxSize(), verticalAlignment = Alignment.CenterVertically,
            horizontalAlignment = Alignment.CenterHorizontally) {
            Label("南财课表", size = 14.sp, bold = true, color = GlanceTheme.colors.primary)
            Spacer(GlanceModifier.height(4.dp))
            Label(message, size = 12.sp, color = GlanceTheme.colors.onSurfaceVariant, lines = 2)
        }
    }
}

/** 竖条 + 内容的课程卡片骨架。 */
@Composable
fun Chip(color: String, height: Dp?, modifier: GlanceModifier = GlanceModifier, strong: Boolean = false,
         dim: Boolean = false, content: @Composable () -> Unit) {
    val bg = when {
        strong -> solid(parseColor(color))
        dim -> GlanceTheme.colors.surfaceVariant
        else -> chipBackground(color)
    }
    val sized = if (height != null) modifier.height(height) else modifier
    Row(modifier = sized.fillMaxWidth().cornerRadius(6.dp).background(bg)) {
        Spacer(GlanceModifier.width(3.dp).fillMaxHeight().background(if (dim) GlanceTheme.colors.outline else solid(parseColor(color))))
        Box(modifier = GlanceModifier.padding(horizontal = 4.dp, vertical = 2.dp)) { content() }
    }
}
