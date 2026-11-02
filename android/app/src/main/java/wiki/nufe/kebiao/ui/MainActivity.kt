package wiki.nufe.kebiao.ui

import android.Manifest
import android.app.AlarmManager
import android.appwidget.AppWidgetManager
import android.content.ClipboardManager
import android.content.ComponentName
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.SystemClock
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import java.time.Instant
import java.time.format.DateTimeFormatter
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import wiki.nufe.kebiao.data.LinkInvalidException
import wiki.nufe.kebiao.data.Now
import wiki.nufe.kebiao.data.Repository
import wiki.nufe.kebiao.data.SHANGHAI
import wiki.nufe.kebiao.data.Snapshot
import wiki.nufe.kebiao.data.buildPlan
import wiki.nufe.kebiao.data.headerNote
import wiki.nufe.kebiao.remind.Reminders
import wiki.nufe.kebiao.sync.LinkState
import wiki.nufe.kebiao.sync.Sync
import wiki.nufe.kebiao.widget.FullWeekReceiver
import wiki.nufe.kebiao.widget.TodayReceiver
import wiki.nufe.kebiao.widget.TomorrowReceiver
import wiki.nufe.kebiao.widget.WeekReceiver
import wiki.nufe.kebiao.widget.parseColor

/**
 * 只做设置：粘贴小组件链接、预览今天的课、选提醒时间、把小组件放到桌面。
 * 看课表还是用网站和桌面小组件。
 */
class MainActivity : ComponentActivity() {
    private var incoming by mutableStateOf<String?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        incoming = linkFrom(intent)
        setContent { AppTheme { Screen(incoming) { incoming = null } } }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        linkFrom(intent)?.let { incoming = it }
    }

    /** 网站设置页的“在安卓 App 中打开”（nufekebiao://setup?url=…），或从别的 App 分享来的文字。 */
    private fun linkFrom(intent: Intent?): String? = when (intent?.action) {
        Intent.ACTION_VIEW -> Repository.extractLink(intent.dataString)
        Intent.ACTION_SEND -> Repository.extractLink(intent.getStringExtra(Intent.EXTRA_TEXT))
        else -> null
    }
}

@Composable
private fun AppTheme(content: @Composable () -> Unit) {
    val dark = isSystemInDarkTheme()
    val context = LocalContext.current
    val scheme = when {
        Build.VERSION.SDK_INT >= 31 -> if (dark) dynamicDarkColorScheme(context) else dynamicLightColorScheme(context)
        dark -> darkColorScheme()
        else -> lightColorScheme()
    }
    MaterialTheme(colorScheme = scheme, content = content)
}

@Composable
private fun Screen(incoming: String?, consumeIncoming: () -> Unit) {
    val context = LocalContext.current
    val repo = remember { Repository(context) }
    val scope = rememberCoroutineScope()
    var url by remember { mutableStateOf(repo.widgetUrl) }
    var snapshot by remember { mutableStateOf(repo.cached()) }
    var invalid by remember { mutableStateOf(LinkState.isInvalid(context)) }
    var busy by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf<String?>(null) }

    fun save(link: String) {
        busy = true
        message = null
        scope.launch {
            val previous = repo.widgetUrl
            repo.widgetUrl = link
            try {
                val snap = repo.fetch()
                if (snap == null || snap.offline) {
                    message = "连不上服务器，先保存了链接，联网后会自动更新。"
                }
                snapshot = snap
                url = link
                invalid = false
                LinkState.markInvalid(context, false)
                Reminders.reschedule(context)
                Sync.schedule(context)
                Sync.redraw(context)
            } catch (_: LinkInvalidException) {
                repo.widgetUrl = previous
                message = "这条链接已经失效，请在网站设置里重新生成后再复制。"
            }
            busy = false
        }
    }

    LaunchedEffect(incoming) {
        if (incoming == null) return@LaunchedEffect
        consumeIncoming()
        if (incoming != url) save(incoming)
    }

    Surface(modifier = Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
        Column(
            modifier = Modifier.fillMaxSize().safeDrawingPadding().verticalScroll(rememberScrollState()).padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text("南财课表", fontSize = 26.sp, fontWeight = FontWeight.Bold)
            Text("把今天、明天和本周的课放到安卓桌面。", color = MaterialTheme.colorScheme.onSurfaceVariant)
            message?.let { Text(it, color = MaterialTheme.colorScheme.error) }
            val current = url
            if (current == null || invalid) {
                SetupCard(busy, invalid, onSave = ::save)
            } else {
                TodayCard(repo, snapshot, busy) {
                    busy = true
                    scope.launch {
                        try {
                            snapshot = repo.fetch(refresh = true) ?: snapshot
                            Reminders.reschedule(context)
                            Sync.redraw(context)
                            message = if (snapshot?.offline == true) "连不上服务器，显示的是上次的数据。" else null
                        } catch (_: LinkInvalidException) {
                            LinkState.markInvalid(context, true)
                            invalid = true
                            Sync.redraw(context)
                        }
                        busy = false
                    }
                }
                WidgetCard()
                ReminderCard(repo)
                OtherCard(repo) {
                    repo.widgetUrl = null
                    Sync.cancelAll(context)
                    Reminders.reschedule(context)
                    scope.launch { Sync.redraw(context) }
                    url = null
                    snapshot = null
                }
            }
        }
    }
}

@Composable
private fun Section(title: String, content: @Composable () -> Unit) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(title, fontWeight = FontWeight.SemiBold, fontSize = 16.sp)
            content()
        }
    }
}

@Composable
private fun Hint(text: String) {
    Text(text, fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
}

@Composable
private fun SetupCard(busy: Boolean, invalid: Boolean, onSave: (String) -> Unit) {
    val context = LocalContext.current
    var text by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }
    Section(if (invalid) "链接已失效" else "第一步：粘贴小组件链接") {
        if (invalid) Hint("小组件链接被重新生成或停用了。请到网站重新复制一条。")
        Hint("在电脑或手机浏览器打开南财课表网站 → 账号与设置 → 桌面小组件 → 生成链接 → 点“在安卓 App 中打开”，或“复制链接”后粘贴到这里。")
        Hint("链接本身就是凭证（只读），不要发给别人。")
        OutlinedTextField(
            value = text,
            onValueChange = { text = it; error = null },
            modifier = Modifier.fillMaxWidth(),
            label = { Text("https://…/widget/….json") },
            singleLine = true,
            isError = error != null,
            supportingText = error?.let { { Text(it) } },
        )
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(onClick = {
                val clip = context.getSystemService(ClipboardManager::class.java).primaryClip
                text = clip?.takeIf { it.itemCount > 0 }?.getItemAt(0)?.coerceToText(context)?.toString().orEmpty()
                error = null
            }) { Text("粘贴") }
            Button(enabled = !busy && text.isNotBlank(), onClick = {
                val link = Repository.extractLink(text)
                if (link == null) error = "不像小组件链接：应以 /widget/<一串字符>.json 结尾" else onSave(link)
            }) { Text(if (busy) "正在验证…" else "保存") }
        }
    }
}

private val TIME = DateTimeFormatter.ofPattern("M/d HH:mm")

@Composable
private fun TodayCard(repo: Repository, snapshot: Snapshot?, busy: Boolean, onRefresh: () -> Unit) {
    val now = Now.current()
    Section("今天") {
        if (snapshot == null) {
            Hint("还没拿到课表。")
        } else {
            val plan = buildPlan(snapshot.payload, false, now)
            headerNote(snapshot, plan.day, now, compact = false)?.let { Hint(it.text) }
            if (plan.slots.isEmpty()) Text(plan.empty)
            for (slot in plan.slots) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.size(width = 4.dp, height = 32.dp).clip(RoundedCornerShape(2.dp))
                        .background(parseColor(slot.lesson.color)))
                    Spacer(Modifier.width(8.dp))
                    Column {
                        Text(slot.lesson.name, fontWeight = FontWeight.Medium)
                        Hint("${slot.lesson.start}–${slot.lesson.end}  ${slot.lesson.room.ifEmpty { "地点待定" }}")
                    }
                }
            }
            val updated = snapshot.payload.updatedAt?.let {
                TIME.format(Instant.ofEpochSecond(it).atZone(SHANGHAI))
            }
            Hint(listOfNotNull(snapshot.payload.semester, updated?.let { "课表更新于 $it" }).joinToString(" · "))
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(enabled = !busy, onClick = onRefresh) { Text(if (busy) "刷新中…" else "刷新课表") }
            repo.siteUrl()?.let { site ->
                val context = LocalContext.current
                OutlinedButton(onClick = {
                    context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(site)))
                }) { Text("打开网站") }
            }
        }
        Hint("“刷新课表”会请服务器马上向学校确认一次（服务器自己限流，两分钟内刚同步过就跳过）。")
    }
}

private const val PIN_COOLDOWN_MS = 2_000L
private const val PIN_CHECK_DELAY_MS = 1_000L
private const val MANUAL_ADD = "长按桌面空白处 → 小组件（OPPO / 一加是“卡片” → 拉到底“插件”）→ 找到“南财课表”。"

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun WidgetCard() {
    val context = LocalContext.current
    val manager = AppWidgetManager.getInstance(context)
    // 桌面弹出确认框要几百毫秒，期间再点一下就会排进第二个请求；勾过“自动添加”或国产桌面直接添加时
    // 根本没有确认框，每点一下就多一个。所以几个按钮共用一个冷却，冷却内的点击直接忽略。
    var lastPinAt by remember { mutableLongStateOf(0L) }
    // requestPinAppWidget 返回 true 只说明请求发出去了：ColorOS 等桌面会不声不响地拒掉。
    // 所以点完等桌面的确认框关掉、回到这页，再数一下这种小组件多没多；没多就告诉用户手动加。
    var showGuide by remember { mutableStateOf(false) }
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    val scope = rememberCoroutineScope()
    Section("添加到桌面") {
        Hint("今天：正在上课倒计时下课、下一节倒计时上课；课上完预告下次上课。明天：明天的全部课。本周：学校课表的样子，每节课写准确的起止时间；本周（按时长）：课越长格子越高。都可以随意拉伸，本周建议 4×3 以上。")
        if (manager.isRequestPinAppWidgetSupported) {
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                for ((label, receiver) in listOf(
                    "今天" to TodayReceiver::class.java,
                    "明天" to TomorrowReceiver::class.java,
                    "本周" to WeekReceiver::class.java,
                    "本周（按时长）" to FullWeekReceiver::class.java,
                )) {
                    OutlinedButton(onClick = {
                        val now = SystemClock.elapsedRealtime()
                        if (now - lastPinAt >= PIN_COOLDOWN_MS) {
                            lastPinAt = now
                            val provider = ComponentName(context, receiver)
                            val before = manager.getAppWidgetIds(provider).size
                            if (manager.requestPinAppWidget(provider, null, null)) {
                                scope.launch {
                                    delay(PIN_CHECK_DELAY_MS)
                                    lifecycle.currentStateFlow.first { it.isAtLeast(Lifecycle.State.RESUMED) }
                                    delay(PIN_CHECK_DELAY_MS)
                                    if (manager.getAppWidgetIds(provider).size <= before) showGuide = true
                                }
                            } else {
                                showGuide = true
                            }
                        }
                    }) { Text("添加“$label”") }
                }
            }
            Hint("点了没反应的话：$MANUAL_ADD")
        } else {
            Hint("这个桌面不支持一键添加：$MANUAL_ADD")
        }
    }
    if (showGuide) {
        AlertDialog(
            onDismissRequest = { showGuide = false },
            confirmButton = { TextButton(onClick = { showGuide = false }) { Text("知道了") } },
            title = { Text("没加到桌面？") },
            text = { Text("有的桌面（比如 OPPO、一加、vivo、华为）不让 App 直接添加小组件。请手动添加：$MANUAL_ADD") },
        )
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ReminderCard(repo: Repository) {
    val context = LocalContext.current
    var minutes by remember { mutableIntStateOf(repo.remindMinutes) }
    var canNotify by remember { mutableStateOf(Reminders.canNotify(context)) }
    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        canNotify = granted
        Reminders.reschedule(context)
    }
    Section("上课提醒") {
        Hint("按未来 7 天的课在本机定闹钟，课前发通知（课程、时间、教室，调过的课会注明）。拿到新课表会自动重排。")
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            for (m in listOf(0, 5, 10, 15, 20, 30)) {
                FilterChip(
                    selected = minutes == m,
                    onClick = {
                        minutes = m
                        repo.remindMinutes = m
                        Reminders.reschedule(context)
                        if (m > 0 && !canNotify && Build.VERSION.SDK_INT >= 33) {
                            permission.launch(Manifest.permission.POST_NOTIFICATIONS)
                        }
                    },
                    label = { Text(if (m == 0) "关闭" else "提前 $m 分钟") },
                )
            }
        }
        if (minutes > 0 && !canNotify && Build.VERSION.SDK_INT >= 33) {
            TextButton(onClick = { permission.launch(Manifest.permission.POST_NOTIFICATIONS) }) { Text("允许通知") }
        }
        if (minutes > 0 && Build.VERSION.SDK_INT >= 31 &&
            !context.getSystemService(AlarmManager::class.java).canScheduleExactAlarms()
        ) {
            Hint("没有“闹钟和提醒”权限时，系统可能晚几分钟才提醒。")
            TextButton(onClick = {
                context.startActivity(
                    Intent(Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM, Uri.parse("package:${context.packageName}")),
                )
            }) { Text("允许准时提醒") }
        }
    }
}

@Composable
private fun OtherCard(repo: Repository, onRemove: () -> Unit) {
    val context = LocalContext.current
    Section("其他") {
        Hint("国产系统（MIUI / HyperOS、ColorOS、OriginOS、鸿蒙等）请在电池设置里允许“南财课表”自启动、后台运行，否则小组件和提醒可能不准时。")
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(onClick = {
                context.startActivity(
                    Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:${context.packageName}")),
                )
            }) { Text("应用设置") }
            TextButton(onClick = onRemove) { Text("移除链接", color = MaterialTheme.colorScheme.error) }
        }
        Spacer(Modifier.height(2.dp))
        Hint("非官方工具，与南京财经大学无隶属关系。")
    }
}
