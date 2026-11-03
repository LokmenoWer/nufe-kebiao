package wiki.nufe.kebiao.ui

import android.Manifest
import android.app.AlarmManager
import android.app.NotificationManager
import android.content.ClipboardManager
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProviderInfo
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.SystemClock
import android.provider.Settings
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.collection.intSetOf
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.animation.slideInHorizontally
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutHorizontally
import androidx.compose.animation.togetherWith
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.pager.PageSize
import androidx.compose.foundation.pager.VerticalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.blur
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.TransformOrigin
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.glance.appwidget.GlanceAppWidgetManager
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlin.math.abs
import wiki.nufe.kebiao.R
import wiki.nufe.kebiao.data.DayPlan
import wiki.nufe.kebiao.data.LinkInvalidException
import wiki.nufe.kebiao.data.Note
import wiki.nufe.kebiao.data.Now
import wiki.nufe.kebiao.data.Profile
import wiki.nufe.kebiao.data.Repository
import wiki.nufe.kebiao.data.Slot
import wiki.nufe.kebiao.data.Snapshot
import wiki.nufe.kebiao.data.State
import wiki.nufe.kebiao.data.Tone
import wiki.nufe.kebiao.data.WEEKDAY
import wiki.nufe.kebiao.data.annotate
import wiki.nufe.kebiao.data.buildTodayPlan
import wiki.nufe.kebiao.data.headerNote
import wiki.nufe.kebiao.data.lessonMs
import wiki.nufe.kebiao.data.shortDate
import wiki.nufe.kebiao.diag.Diag
import wiki.nufe.kebiao.diag.Diagnostics
import wiki.nufe.kebiao.remind.LiveUpdates
import wiki.nufe.kebiao.remind.Reminders
import wiki.nufe.kebiao.sync.LinkState
import wiki.nufe.kebiao.sync.Sync
import wiki.nufe.kebiao.widget.TodayReceiver
import wiki.nufe.kebiao.widget.WeekReceiver
import wiki.nufe.kebiao.widget.parseColor

private const val SITE_URL = "https://kebiao.nufe.wiki"
private const val REPO_URL = "https://github.com/LokmenoWer/nufe-kebiao"
private const val PIN_COOLDOWN_MS = 2_000L
private const val PIN_CHECK_DELAY_MS = 1_000L
private const val MANUAL_ADD =
    "长按桌面空白处 → 小组件（OPPO / 一加是“卡片” → 拉到底“插件”）→ 找到“nufe课表”。"

private val LiveRed = Color(0xFFE03131)
private val WarmOrange = Color(0xFFE8590C)

private val LightColors = lightColorScheme(
    primary = Color(0xFF111111),
    onPrimary = Color.White,
    primaryContainer = Color(0xFFF0F0F2),
    onPrimaryContainer = Color(0xFF111111),
    background = Color(0xFFF7F7F8),
    onBackground = Color(0xFF111111),
    surface = Color.White,
    onSurface = Color(0xFF111111),
    surfaceVariant = Color(0xFFF2F2F4),
    onSurfaceVariant = Color(0xFF6B6B70),
    outline = Color(0xFFE6E6E9),
    error = Color(0xFFD64545),
)

private val DarkColors = darkColorScheme(
    primary = Color(0xFFF5F5F7),
    onPrimary = Color(0xFF111111),
    background = Color(0xFF0F0F11),
    onBackground = Color(0xFFF5F5F7),
    surface = Color(0xFF1A1A1D),
    onSurface = Color(0xFFF5F5F7),
    surfaceVariant = Color(0xFF252529),
    onSurfaceVariant = Color(0xFFA6A6AC),
    outline = Color(0xFF35353A),
    error = Color(0xFFFF8A8A),
)

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

@Composable
private fun KebiaoTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = if (isSystemInDarkTheme()) DarkColors else LightColors,
        content = content,
    )
}

@Composable
private fun AppBackground(content: @Composable BoxScope.() -> Unit) {
    val glow = if (isSystemInDarkTheme()) Color(0xFF24335A) else Color(0xFFDCE4FF)
    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.background),
    ) {
        Box(
            modifier = Modifier
                .align(Alignment.TopEnd)
                .offset(x = 90.dp, y = (-130).dp)
                .size(300.dp)
                .blur(100.dp)
                .background(glow.copy(alpha = 0.65f), CircleShape),
        )
        content()
    }
}

@Composable
private fun AppRoot(incoming: String?, consumeIncoming: () -> Unit) {
    val context = LocalContext.current
    val repo = remember { Repository(context) }
    val scope = rememberCoroutineScope()
    val notificationManager = remember { context.getSystemService(NotificationManager::class.java) }
    val exactAlarms = remember { context.getSystemService(AlarmManager::class.java) }
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    val permissionPrefs = remember {
        context.getSharedPreferences("permission_intro", Context.MODE_PRIVATE)
    }

    var url by remember { mutableStateOf(repo.widgetUrl) }
    var snapshot by remember { mutableStateOf(repo.cached()) }
    var profile by remember { mutableStateOf<Profile?>(null) }
    var bound by remember { mutableStateOf(repo.deviceId != null) }
    var invalid by remember { mutableStateOf(LinkState.isInvalid(context)) }
    var busy by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf<String?>(null) }
    var tab by remember { mutableStateOf(Tab.Timetable) }
    var pagePulse by remember { mutableIntStateOf(0) }
    var notificationsOk by remember { mutableStateOf(Reminders.canNotify(context)) }
    var exactOk by remember {
        mutableStateOf(Build.VERSION.SDK_INT < 31 || exactAlarms?.canScheduleExactAlarms() == true)
    }
    var promotedOk by remember {
        mutableStateOf(
            Build.VERSION.SDK_INT < 36 || notificationManager?.canPostPromotedNotifications() == true,
        )
    }
    var showPermissions by remember {
        mutableStateOf(
            !permissionPrefs.getBoolean("seen", false) &&
                (!notificationsOk || !exactOk || !promotedOk),
        )
    }

    val notificationPermission = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        notificationsOk = granted
        Reminders.reschedule(context)
    }

    DisposableEffect(lifecycle) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) {
                notificationsOk = Reminders.canNotify(context)
                exactOk = Build.VERSION.SDK_INT < 31 ||
                    exactAlarms?.canScheduleExactAlarms() == true
                promotedOk = Build.VERSION.SDK_INT < 36 ||
                    notificationManager?.canPostPromotedNotifications() == true
            }
        }
        lifecycle.addObserver(observer)
        onDispose { lifecycle.removeObserver(observer) }
    }

    fun markPermissionsSeen() {
        showPermissions = false
        permissionPrefs.edit().putBoolean("seen", true).apply()
    }

    LaunchedEffect(notificationsOk, exactOk, promotedOk) {
        if (showPermissions && notificationsOk && exactOk && promotedOk) {
            markPermissionsSeen()
        }
    }

    fun openSite() {
        val site = repo.siteUrl() ?: SITE_URL
        context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(site)))
    }

    fun save(raw: String) {
        val link = Repository.extractLink(raw)
        if (link == null) {
            Diag.w("link format invalid")
            message = "链接格式不对：应是以 /mobile/<token>.json 结尾的链接。"
            return
        }
        busy = true
        message = null
        scope.launch {
            val previous = repo.widgetUrl
            repo.widgetUrl = link
            try {
                val snap = repo.fetch()
                if (snap == null || snap.offline) {
                    Diag.w("link validation offline")
                    repo.widgetUrl = previous
                    message = "连不上服务器，无法校验链接；请检查网络后重试。"
                    return@launch
                }
                snapshot = snap
                url = link
                invalid = false
                tab = Tab.Timetable
                LinkState.markInvalid(context, false)
                Reminders.reschedule(context)
                Sync.schedule(context)
                Sync.redraw(context)
                Diag.i("link saved")
                Repository.extractPair(raw)?.let { code ->
                    val failure = repo.pair(code)
                    Diag.i(if (failure == null) "device paired" else "device pair failed")
                    message = failure ?: "已绑定这台设备。"
                }
                profile = repo.fetchProfile()
                bound = repo.deviceId != null
            } catch (_: LinkInvalidException) {
                Diag.w("link invalid")
                repo.widgetUrl = previous
                invalid = true
                message = "这条链接已失效，请在网站设置里重新生成。"
            } catch (error: Exception) {
                Diag.w("link validation failed: ${error.javaClass.simpleName}")
                repo.widgetUrl = previous
                message = "连接服务器失败，请稍后再试。"
            } finally {
                busy = false
            }
        }
    }

    fun refresh() {
        if (busy) return
        busy = true
        message = null
        scope.launch {
            try {
                val snap = repo.fetch(refresh = true)
                if (snap != null) snapshot = snap
                if (snap?.offline == true) {
                    Diag.w("refresh offline")
                    message = "连不上服务器，显示的是缓存。"
                } else {
                    Diag.i("refresh ok")
                }
                Reminders.reschedule(context)
                Sync.redraw(context)
            } catch (_: LinkInvalidException) {
                Diag.w("refresh invalid link")
                invalid = true
                LinkState.markInvalid(context, true)
                message = "链接已失效，请重新生成。"
            } catch (error: Exception) {
                Diag.w("refresh failed: ${error.javaClass.simpleName}")
                message = "刷新失败，请稍后再试。"
            } finally {
                busy = false
            }
        }
    }

    fun removeLink() {
        Diag.i("link removed")
        repo.widgetUrl = null
        Sync.cancelAll(context)
        LiveUpdates.cancel(context)
        Reminders.reschedule(context)
        scope.launch { Sync.redraw(context) }
        url = null
        snapshot = null
        profile = null
        bound = false
        invalid = false
        message = null
        LinkState.markInvalid(context, false)
    }

    LaunchedEffect(Unit) {
        profile = repo.cachedProfile()
    }

    // 打开“我的”时取一次最新个人信息；设备被撤销时这里会清掉本机绑定
    LaunchedEffect(tab, url) {
        if (tab == Tab.Mine) {
            profile = repo.fetchProfile() ?: if (repo.deviceId != null) profile else null
            bound = repo.deviceId != null
        }
    }

    LaunchedEffect(incoming) {
        val link = incoming ?: return@LaunchedEffect
        consumeIncoming()
        save(link)
    }

    AppBackground {
        if (url == null || invalid) {
            SetupScreen(
                busy = busy,
                message = message,
                invalid = invalid,
                onConfirm = ::save,
                onOpenSite = ::openSite,
            )
        } else {
            Box(Modifier.fillMaxSize()) {
                AnimatedContent(
                    targetState = tab to pagePulse,
                    modifier = Modifier.fillMaxSize(),
                    transitionSpec = {
                        val forward = targetState.first.ordinal > initialState.first.ordinal
                        val springSpec = spring<IntOffset>(dampingRatio = 0.72f, stiffness = 360f)
                        (slideInHorizontally(springSpec) { if (forward) -it / 18 else it / 18 } +
                            slideInVertically(springSpec) { it / 16 } +
                            fadeIn(tween(160))) togetherWith fadeOut(tween(120))
                    },
                    label = "tab-page",
                ) { (target, _) ->
                    when (target) {
                        Tab.Timetable -> HomeScreen(
                            repo = repo,
                            snapshot = snapshot,
                            busy = busy,
                            message = message,
                            onRefresh = ::refresh,
                            onOpenSite = ::openSite,
                        )
                        Tab.Mine -> MineScreen(
                            repo = repo,
                            snapshot = snapshot,
                            profile = profile,
                            bound = bound,
                            onOpenSite = ::openSite,
                            onPair = ::save,
                            onRemove = ::removeLink,
                        )
                        Tab.About -> AboutScreen()
                    }
                }
                GlassNav(
                    tab,
                    { next ->
                        if (next != tab) {
                            pagePulse++
                            tab = next
                        }
                    },
                    Modifier.align(Alignment.BottomCenter),
                )
            }
        }
    }

    if (showPermissions) {
        PermissionSetupDialog(
            liveName = liveUpdateName(),
            notificationsOk = notificationsOk,
            exactOk = exactOk,
            promotedOk = promotedOk,
            onAllowNotifications = {
                notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
            },
            onOpenExactAlarm = {
                context.startActivity(
                    Intent(
                        Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM,
                        Uri.parse("package:${context.packageName}"),
                    ),
                )
            },
            onOpenPromoted = {
                context.startActivity(
                    Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                        .putExtra(Settings.EXTRA_APP_PACKAGE, context.packageName),
                )
            },
            onDismiss = { markPermissionsSeen() },
        )
    }
}

@Composable
private fun PermissionSetupDialog(
    liveName: String,
    notificationsOk: Boolean,
    exactOk: Boolean,
    promotedOk: Boolean,
    onAllowNotifications: () -> Unit,
    onOpenExactAlarm: () -> Unit,
    onOpenPromoted: () -> Unit,
    onDismiss: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("先开启提醒权限") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(
                    "开启后，课前提醒、桌面小组件和${liveName}才能准时工作。系统会直接打开对应授权页。",
                    fontSize = 13.sp,
                    lineHeight = 20.sp,
                )
                if (Build.VERSION.SDK_INT >= 33) {
                    PermissionDialogRow(
                        title = "通知",
                        detail = "上课提醒和实时提醒",
                        allowed = notificationsOk,
                        action = "允许",
                        onClick = onAllowNotifications,
                    )
                }
                if (Build.VERSION.SDK_INT >= 31) {
                    PermissionDialogRow(
                        title = "准时提醒",
                        detail = "避免系统晚几分钟",
                        allowed = exactOk,
                        action = "去设置",
                        onClick = onOpenExactAlarm,
                    )
                }
                if (Build.VERSION.SDK_INT >= 36) {
                    PermissionDialogRow(
                        title = "${liveName}提醒",
                        detail = "课前显示倒计时卡片",
                        allowed = promotedOk,
                        action = "去设置",
                        onClick = onOpenPromoted,
                    )
                }
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("知道了") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("稍后") } },
    )
}

@Composable
private fun PermissionDialogRow(
    title: String,
    detail: String,
    allowed: Boolean,
    action: String,
    onClick: () -> Unit,
) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(title, fontSize = 14.sp, fontWeight = FontWeight.Medium)
            Text(detail, fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        if (allowed) {
            Text("已允许", fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        } else {
            TextButton(onClick = onClick, contentPadding = PaddingValues(horizontal = 6.dp)) {
                Text(action, fontSize = 12.sp)
            }
        }
    }
}

@Composable
private fun SetupScreen(
    busy: Boolean,
    message: String?,
    invalid: Boolean,
    onConfirm: (String) -> Unit,
    onOpenSite: () -> Unit,
) {
    var text by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .safeDrawingPadding()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 24.dp, vertical = 28.dp),
    ) {
        HeaderBlock()
        Spacer(Modifier.height(36.dp))
        SectionCard {
            Text("连接课表", fontSize = 18.sp, fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.height(6.dp))
            Hint("在网站 → 账号与设置 → 桌面小组件生成只读链接，再粘贴到这里。")
            Spacer(Modifier.height(16.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(
                    value = text,
                    onValueChange = {
                        text = it
                        error = null
                    },
                    modifier = Modifier.weight(1f),
                    placeholder = { Text("https://…/mobile/….json", fontSize = 13.sp) },
                    singleLine = true,
                    shape = RoundedCornerShape(16.dp),
                    keyboardOptions = KeyboardOptions(
                        keyboardType = KeyboardType.Uri,
                        imeAction = ImeAction.Done,
                    ),
                    keyboardActions = KeyboardActions(
                        onDone = {
                            if (!busy && text.isNotBlank()) {
                                error = null
                                onConfirm(text)
                            }
                        },
                    ),
                )
                Spacer(Modifier.width(10.dp))
                Button(
                    onClick = {
                        error = null
                        onConfirm(text)
                    },
                    enabled = !busy && text.isNotBlank(),
                    modifier = Modifier.height(56.dp),
                    shape = RoundedCornerShape(16.dp),
                    contentPadding = PaddingValues(horizontal = 18.dp),
                ) { Text(if (busy) "校验中" else "确认", fontSize = 14.sp) }
            }
            if (error != null || message != null) {
                Spacer(Modifier.height(10.dp))
                Text(
                    error ?: message.orEmpty(),
                    color = MaterialTheme.colorScheme.error,
                    fontSize = 12.sp,
                    lineHeight = 18.sp,
                )
            } else if (invalid) {
                Spacer(Modifier.height(10.dp))
                Text("链接已失效，请重新生成。", color = MaterialTheme.colorScheme.error, fontSize = 12.sp)
            }
            Spacer(Modifier.height(12.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                TextButton(onClick = onOpenSite, contentPadding = PaddingValues(0.dp)) {
                    Text("打开网站生成链接", fontSize = 13.sp)
                }
                Spacer(Modifier.weight(1f))
                Text("链接是只读凭证", fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        Spacer(Modifier.height(20.dp))
        Hint("课堂提醒、桌面小组件和实时动态都从这条链接读取课表。链接只读，不要发给别人。")
        Spacer(Modifier.height(28.dp))
        Text(
            "非官方工具，与南京财经大学无隶属关系。",
            fontSize = 11.sp,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun HomeScreen(
    repo: Repository,
    snapshot: Snapshot?,
    busy: Boolean,
    message: String?,
    onRefresh: () -> Unit,
    onOpenSite: () -> Unit,
) {
    val context = LocalContext.current
    var now by remember { mutableStateOf(Now.current()) }
    var liveUpdate by remember { mutableStateOf(repo.liveUpdateEnabled) }
    var alarmEnabled by remember { mutableStateOf(repo.alarmEnabled) }
    var minutes by remember { mutableIntStateOf(repo.remindMinutes) }
    var showWheel by remember { mutableStateOf(false) }

    LaunchedEffect(Unit) {
        while (true) {
            delay(30_000)
            now = Now.current()
        }
    }

    val plan = snapshot?.let { buildTodayPlan(it.payload, now) }
    val changed = plan?.slots?.any { it.lesson.changed } == true || plan?.cancelled?.isNotEmpty() == true
    val note = snapshot?.let { headerNote(it, plan?.day, now, compact = false, changed = changed) }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .safeDrawingPadding()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 24.dp, vertical = 24.dp),
    ) {
        HeaderBlock()
        Spacer(Modifier.height(18.dp))
        StatusPill(plan, now)
        if (plan?.slots?.isNotEmpty() == true) Spacer(Modifier.height(18.dp))
        if (message != null) {
            Text(message, color = MaterialTheme.colorScheme.error, fontSize = 12.sp)
            Spacer(Modifier.height(10.dp))
        }
        TodayCardView(snapshot, plan, note, now, busy, onRefresh, onOpenSite)
        Spacer(Modifier.height(14.dp))
        WidgetSection()
        Spacer(Modifier.height(14.dp))
        ReminderSection(
            repo = repo,
            minutes = minutes,
            alarmEnabled = alarmEnabled,
            onPickTime = { showWheel = true },
            onAlarmToggle = { enabled ->
                Diag.i("alarm enabled=$enabled")
                alarmEnabled = enabled
                repo.alarmEnabled = enabled
                if (enabled && minutes == 0) {
                    minutes = 10
                    repo.remindMinutes = 10
                }
                if (!enabled) LiveUpdates.cancel(context)
                Reminders.reschedule(context)
            },
            liveUpdate = liveUpdate,
            onLiveUpdateChange = { checked ->
                Diag.i("live update enabled=$checked")
                liveUpdate = checked
                repo.liveUpdateEnabled = checked
                if (!checked) LiveUpdates.cancel(context)
                Reminders.reschedule(context)
            },
        )
        Spacer(Modifier.height(110.dp))
    }

    if (showWheel) {
        TimeWheelDialog(
            initial = minutes,
            onDismiss = { showWheel = false },
            onConfirm = { selected ->
                showWheel = false
                minutes = selected
                repo.remindMinutes = selected
                Reminders.reschedule(context)
            },
        )
    }
}

@Composable
private fun HeaderBlock() {
    Text(
        "nufe课表",
        fontSize = 34.sp,
        fontWeight = FontWeight.Bold,
        letterSpacing = (-0.5).sp,
        color = MaterialTheme.colorScheme.onBackground,
    )
    Spacer(Modifier.height(4.dp))
    Text(
        "把课程放在桌面。",
        fontSize = 15.sp,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

@Composable
private fun StatusPill(plan: DayPlan?, now: Now) {
    val slot = plan?.slots?.firstOrNull() ?: return
    val date = plan.day?.date ?: return
    val target = when (slot.state) {
        State.LIVE -> lessonMs(date, slot.lesson.end)
        State.NEXT -> lessonMs(date, slot.lesson.start)
        else -> return
    }
    val remain = target - now.ms
    if (remain <= 0) return
    val text = when (slot.state) {
        State.LIVE -> "正在上课 · 剩 ${remainText(remain)}"
        State.NEXT -> "准备上课 · ${remainText(remain)}后"
        else -> return
    }
    val dark = isSystemInDarkTheme()
    Surface(
        color = if (dark) Color(0xFF2B2B30) else MaterialTheme.colorScheme.onBackground,
        contentColor = if (dark) Color(0xFFDADAE0) else MaterialTheme.colorScheme.background,
        shape = RoundedCornerShape(50),
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 16.dp, vertical = 9.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(Modifier.size(6.dp).clip(CircleShape).background(Color(0xFF7BD88F)))
            Spacer(Modifier.width(8.dp))
            Text(text, fontSize = 13.sp, fontWeight = FontWeight.Medium)
        }
    }
}

@Composable
private fun TodayCardView(
    snapshot: Snapshot?,
    plan: DayPlan?,
    note: Note?,
    now: Now,
    busy: Boolean,
    onRefresh: () -> Unit,
    onOpenSite: () -> Unit,
) {
    val tomorrow = plan?.title == "明天"
    var showAll by remember(plan?.day?.date) { mutableStateOf(false) }
    val fullSlots = plan?.day
        ?.let { if (tomorrow) annotate(it.lessons, -1, false) else annotate(it.lessons, now.minutes) }
        .orEmpty()
    val hiddenPast = fullSlots.count { it.state == State.PAST }

    SectionCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(plan?.title ?: "今天", fontSize = 18.sp, fontWeight = FontWeight.SemiBold)
            plan?.day?.let {
                Spacer(Modifier.width(8.dp))
                Text(
                    "周${WEEKDAY[it.weekday]} ${shortDate(it.date)}",
                    fontSize = 12.sp,
                    maxLines = 1,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Spacer(Modifier.weight(1f))
            note?.let {
                Text(it.text, fontSize = 11.sp, color = noteColor(it.tone))
                Spacer(Modifier.width(8.dp))
            }
            TextButton(
                onClick = onRefresh,
                enabled = !busy,
                contentPadding = PaddingValues(horizontal = 4.dp),
            ) { Text(if (busy) "刷新中" else "刷新", fontSize = 12.sp) }
        }
        if (plan != null && plan.slots.isNotEmpty()) {
            Spacer(Modifier.height(4.dp))
            Text(
                if (tomorrow) "明天还有 ${plan.slots.size} 门课" else "还有 ${plan.slots.size} 门课要上",
                fontSize = 12.sp,
                fontWeight = FontWeight.Medium,
                color = MaterialTheme.colorScheme.primary,
            )
        }
        Spacer(Modifier.height(12.dp))
        if (snapshot == null || plan == null) {
            Text("正在获取课表…", fontSize = 14.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            return@SectionCard
        }
        AnimatedContent(
            targetState = showAll,
            transitionSpec = {
                (expandVertically(
                    expandFrom = Alignment.Bottom,
                    animationSpec = tween(320, easing = FastOutSlowInEasing),
                ) + fadeIn(tween(200))) togetherWith
                    (shrinkVertically(
                        shrinkTowards = Alignment.Bottom,
                        animationSpec = tween(240, easing = FastOutSlowInEasing),
                    ) + fadeOut(tween(120)))
            },
            label = "today-expand",
        ) { expanded ->
            val shown = if (expanded && fullSlots.isNotEmpty()) fullSlots else plan.slots
            if (shown.isEmpty()) {
                Text(plan.empty, fontSize = 14.sp)
                val preview = plan.preview
                if (preview != null) {
                    Spacer(Modifier.height(12.dp))
                    Text(preview.title, fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Spacer(Modifier.height(8.dp))
                    preview.slots.firstOrNull()?.let { LessonRow(it) }
                }
            } else {
                Column {
                    shown.forEachIndexed { index, slot ->
                        LessonRow(slot)
                        if (index != shown.lastIndex) Spacer(Modifier.height(12.dp))
                    }
                }
            }
        }
        if (plan.cancelled.isNotEmpty()) {
            Spacer(Modifier.height(10.dp))
            Text(
                "停课：" + plan.cancelled.joinToString("、") { "${it.name} ${it.start}" },
                fontSize = 11.sp,
                color = WarmOrange,
            )
        }
        HorizontalDivider(
            modifier = Modifier.padding(top = 14.dp),
            color = MaterialTheme.colorScheme.outline.copy(alpha = 0.7f),
        )
        Row(verticalAlignment = Alignment.CenterVertically) {
            if (hiddenPast > 0) {
                TextButton(
                    onClick = { showAll = !showAll },
                    contentPadding = PaddingValues(0.dp),
                ) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(if (showAll) "收起" else "查看全天", fontSize = 12.sp)
                        Spacer(Modifier.width(2.dp))
                        Icon(
                            painter = painterResource(R.drawable.ic_chevron_down),
                            contentDescription = null,
                            tint = MaterialTheme.colorScheme.primary,
                            modifier = Modifier
                                .size(14.dp)
                                .rotate(if (showAll) 180f else 0f),
                        )
                    }
                }
            }
            Spacer(Modifier.weight(1f))
            TextButton(onClick = onOpenSite, contentPadding = PaddingValues(0.dp)) {
                Text("前往网站查看完整课表", fontSize = 12.sp)
                Spacer(Modifier.width(2.dp))
                Icon(
                    painter = painterResource(R.drawable.ic_chevron_right),
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.size(14.dp),
                )
            }
        }
    }
}

@Composable
private fun LessonRow(slot: Slot) {
    val lesson = slot.lesson
    Row(
        modifier = Modifier.alpha(if (slot.state == State.PAST) 0.45f else 1f),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            Modifier
                .width(3.dp)
                .height(38.dp)
                .clip(RoundedCornerShape(2.dp))
                .background(parseColor(lesson.color)),
        )
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    lesson.name,
                    fontSize = 14.sp,
                    fontWeight = FontWeight.Medium,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f, fill = false),
                )
                if (lesson.changed) {
                    Spacer(Modifier.width(6.dp))
                    Tag("调课", WarmOrange)
                }
            }
            Spacer(Modifier.height(2.dp))
            Text(
                "${lesson.start}–${lesson.end} · ${lesson.room.ifEmpty { "地点待定" }}",
                fontSize = 12.sp,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
        when (slot.state) {
            State.LIVE -> Tag("上课中", LiveRed)
            State.NEXT -> Tag("下一节", MaterialTheme.colorScheme.primary)
            State.PAST -> Tag("已结束", MaterialTheme.colorScheme.onSurfaceVariant)
            else -> Unit
        }
    }
}

@Composable
private fun Tag(text: String, color: Color) {
    Surface(
        color = color.copy(alpha = 0.12f),
        contentColor = color,
        shape = RoundedCornerShape(50),
    ) {
        Text(
            text,
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 2.dp),
            fontSize = 10.sp,
            fontWeight = FontWeight.Medium,
        )
    }
}

@Composable
private fun WidgetSection() {
    val context = LocalContext.current
    val manager = remember { AppWidgetManager.getInstance(context) }
    val scope = rememberCoroutineScope()
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    var showPicker by remember { mutableStateOf(false) }
    var lastPinAt by remember { mutableLongStateOf(0L) }
    var showGuide by remember { mutableStateOf(false) }

    fun pin(receiver: Class<*>) {
        val now = SystemClock.elapsedRealtime()
        if (now - lastPinAt < PIN_COOLDOWN_MS) return
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

    SectionCard {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .clickable { showPicker = true },
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                "添加课表小组件到桌面",
                fontSize = 16.sp,
                fontWeight = FontWeight.SemiBold,
                modifier = Modifier.weight(1f),
            )
            Icon(
                painter = painterResource(R.drawable.ic_chevron_right),
                contentDescription = null,
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.size(18.dp),
            )
        }
    }

    if (showPicker) {
        WidgetPickerDialog(
            onDismiss = { showPicker = false },
            onPick = { receiver ->
                showPicker = false
                pin(receiver)
            },
        )
    }

    if (showGuide) {
        AlertDialog(
            onDismissRequest = { showGuide = false },
            title = { Text("没加到桌面？") },
            text = { Text("有的桌面（比如 OPPO、一加、vivo、华为）不让 App 直接添加小组件。请手动添加：$MANUAL_ADD") },
            confirmButton = { TextButton(onClick = { showGuide = false }) { Text("知道了") } },
        )
    }
}

@Composable
private fun WidgetPickerDialog(onDismiss: () -> Unit, onPick: (Class<*>) -> Unit) {
    PopupCard(onDismissRequest = onDismiss) {
        Text("添加课表小组件", fontSize = 18.sp, fontWeight = FontWeight.SemiBold)
        Spacer(Modifier.height(4.dp))
        Hint("选择一种放到桌面。")
        Spacer(Modifier.height(10.dp))
        WidgetOption("今天", "正在上课 / 下一节，课后自动切到明天", TodayReceiver::class.java, onPick)
        HorizontalDivider(
            modifier = Modifier.padding(vertical = 4.dp),
            color = MaterialTheme.colorScheme.outline.copy(alpha = 0.7f),
        )
        WidgetOption("本周", "按节次排，写准确起止时间", WeekReceiver::class.java, onPick)
        Spacer(Modifier.height(4.dp))
        TextButton(
            onClick = onDismiss,
            contentPadding = PaddingValues(horizontal = 8.dp),
            modifier = Modifier.align(Alignment.End),
        ) { Text("取消", fontSize = 12.sp) }
    }
}

@Composable
private fun WidgetOption(
    title: String,
    description: String,
    receiver: Class<*>,
    onPin: (Class<*>) -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable { onPin(receiver) }
            .padding(vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(title, fontSize = 14.sp, fontWeight = FontWeight.Medium)
            Spacer(Modifier.height(2.dp))
            Text(description, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Text("添加", fontSize = 12.sp, color = MaterialTheme.colorScheme.primary)
    }
}

@Composable
private fun ReminderSection(
    repo: Repository,
    minutes: Int,
    alarmEnabled: Boolean,
    onPickTime: () -> Unit,
    onAlarmToggle: (Boolean) -> Unit,
    liveUpdate: Boolean,
    onLiveUpdateChange: (Boolean) -> Unit,
) {
    val context = LocalContext.current
    val view = LocalView.current
    val manager = context.getSystemService(NotificationManager::class.java)
    var canNotify by remember { mutableStateOf(Reminders.canNotify(context)) }
    val permission = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        canNotify = granted
        Reminders.reschedule(context)
    }
    val liveName = liveUpdateName()

    SectionCard {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .clickable(onClick = onPickTime),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f)) {
                Text("上课前提示时间", fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
                Spacer(Modifier.height(3.dp))
                Text(
                    if (minutes > 0) "提前 $minutes 分钟" else "未设置",
                    fontSize = 13.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Icon(
                painter = painterResource(R.drawable.ic_chevron_right),
                contentDescription = null,
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.size(18.dp),
            )
        }

        HorizontalDivider(
            modifier = Modifier.padding(vertical = 12.dp),
            color = MaterialTheme.colorScheme.outline.copy(alpha = 0.7f),
        )
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("闹钟上课提示", fontSize = 14.sp, fontWeight = FontWeight.Medium)
                Spacer(Modifier.height(2.dp))
                Hint("开启后会设置本地闹钟，在设定的时间响起。")
            }
            Switch(
                checked = alarmEnabled,
                onCheckedChange = {
                    view.performHapticFeedback(android.view.HapticFeedbackConstants.VIRTUAL_KEY)
                    onAlarmToggle(it)
                },
            )
        }

        HorizontalDivider(
            modifier = Modifier.padding(vertical = 12.dp),
            color = MaterialTheme.colorScheme.outline.copy(alpha = 0.7f),
        )
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("流体云显示", fontSize = 14.sp, fontWeight = FontWeight.Medium)
                Spacer(Modifier.height(2.dp))
                Hint(
                    if (Build.VERSION.SDK_INT >= 36) {
                        "仅 ColorOS 16+ 使用流体云，其他平台回退普通通知；开课 5 分钟后收起。"
                    } else {
                        "需要 Android 16 / ColorOS 16 或 HyperOS 3。"
                    },
                )
                if (!alarmEnabled) {
                    Spacer(Modifier.height(2.dp))
                    Hint("开启“闹钟上课提示”后生效。")
                }
            }
            Switch(
                checked = liveUpdate,
                onCheckedChange = {
                    view.performHapticFeedback(android.view.HapticFeedbackConstants.VIRTUAL_KEY)
                    onLiveUpdateChange(it)
                },
                enabled = Build.VERSION.SDK_INT >= 36,
            )
        }
        if (Build.VERSION.SDK_INT >= 36 && liveUpdate && alarmEnabled &&
            manager?.canPostPromotedNotifications() != true
        ) {
            Spacer(Modifier.height(6.dp))
            Hint("系统还没允许本应用的${liveName}提醒；到系统设置 → 通知与控制中心 → $liveName 里允许。")
            TextButton(
                onClick = {
                    context.startActivity(
                        Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                            .putExtra(Settings.EXTRA_APP_PACKAGE, context.packageName),
                    )
                },
                contentPadding = PaddingValues(0.dp),
            ) { Text("去通知设置", fontSize = 12.sp) }
        }
        if (alarmEnabled && minutes > 0 && !canNotify && Build.VERSION.SDK_INT >= 33) {
            TextButton(
                onClick = { permission.launch(Manifest.permission.POST_NOTIFICATIONS) },
                contentPadding = PaddingValues(0.dp),
            ) { Text("允许通知", fontSize = 12.sp) }
        }
        if (alarmEnabled && minutes > 0 && Build.VERSION.SDK_INT >= 31 &&
            context.getSystemService(AlarmManager::class.java).canScheduleExactAlarms() == false
        ) {
            TextButton(
                onClick = {
                    context.startActivity(
                        Intent(
                            Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM,
                            Uri.parse("package:${context.packageName}"),
                        ),
                    )
                },
                contentPadding = PaddingValues(0.dp),
            ) { Text("允许准时提醒", fontSize = 12.sp) }
        }
    }
}

@Composable
private fun MineScreen(
    repo: Repository,
    snapshot: Snapshot?,
    profile: Profile?,
    bound: Boolean,
    onOpenSite: () -> Unit,
    onPair: (String) -> Unit,
    onRemove: () -> Unit,
) {
    val context = LocalContext.current
    val alarms = context.getSystemService(AlarmManager::class.java)
    val manager = context.getSystemService(NotificationManager::class.java)
    val liveName = liveUpdateName()
    var confirmRemove by remember { mutableStateOf(false) }
    val host = repo.widgetUrl?.let { runCatching { Uri.parse(it).host }.getOrNull() } ?: "未连接"
    val student = profile?.student
    val mail = profile?.mail
    val scope = rememberCoroutineScope()
    var exporting by remember { mutableStateOf(false) }

    fun exportDiagnostics() {
        if (exporting) return
        exporting = true
        scope.launch {
            val file = withContext(Dispatchers.IO) {
                runCatching { Diagnostics.export(context, repo, snapshot) }.getOrNull()
            }
            exporting = false
            if (file == null) {
                Toast.makeText(context, "导出失败，请稍后重试", Toast.LENGTH_SHORT).show()
                return@launch
            }
            val shared = runCatching { Diagnostics.share(context, file) }.isSuccess
            if (!shared) Toast.makeText(context, "没有可用的分享应用", Toast.LENGTH_SHORT).show()
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .safeDrawingPadding()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 24.dp, vertical = 24.dp),
    ) {
        Text(
            "我的",
            fontSize = 34.sp,
            fontWeight = FontWeight.Bold,
            letterSpacing = (-0.5).sp,
            color = MaterialTheme.colorScheme.onBackground,
        )
        Spacer(Modifier.height(4.dp))
        Text("个人信息、链接和提醒。", fontSize = 15.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.height(20.dp))

        SectionCard {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Surface(
                    color = if (isSystemInDarkTheme()) Color(0xFF2B2B30) else MaterialTheme.colorScheme.onBackground,
                    contentColor = if (isSystemInDarkTheme()) Color(0xFFDADAE0) else MaterialTheme.colorScheme.background,
                    shape = CircleShape,
                    modifier = Modifier.size(48.dp),
                ) {
                    Box(contentAlignment = Alignment.Center) {
                        Text(
                            student?.name?.take(1) ?: "南",
                            fontSize = 18.sp,
                            fontWeight = FontWeight.SemiBold,
                        )
                    }
                }
                Spacer(Modifier.width(14.dp))
                Column(Modifier.weight(1f)) {
                    Text(
                        student?.name ?: "个人信息",
                        fontSize = 18.sp,
                        fontWeight = FontWeight.SemiBold,
                    )
                    Spacer(Modifier.height(3.dp))
                    Text(
                        student?.code?.let { "学号 $it" } ?: if (bound) "正在同步…" else "这台设备还没绑定",
                        fontSize = 12.sp,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            Spacer(Modifier.height(14.dp))
            HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.7f))
            Spacer(Modifier.height(4.dp))
            if (!bound) {
                Spacer(Modifier.height(6.dp))
                Hint("姓名、学号等个人信息不随课表链接传输，只发给绑定过的设备。到网站“账号与设置 → 安卓 App”点“绑定新设备”，再回到这里。")
                Spacer(Modifier.height(6.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    TextButton(onClick = onOpenSite, contentPadding = PaddingValues(horizontal = 4.dp)) {
                        Text("打开网站", fontSize = 12.sp)
                    }
                    TextButton(
                        onClick = {
                            val text = context.getSystemService(ClipboardManager::class.java)
                                ?.primaryClip?.takeIf { it.itemCount > 0 }?.getItemAt(0)
                                ?.coerceToText(context)?.toString()
                            if (Repository.extractPair(text) != null && text != null) {
                                onPair(text)
                            } else {
                                Toast.makeText(context, "剪贴板里没有配对链接", Toast.LENGTH_SHORT).show()
                            }
                        },
                        contentPadding = PaddingValues(horizontal = 4.dp),
                    ) { Text("粘贴配对链接", fontSize = 12.sp) }
                }
            }
            if (student != null) {
                ProfileRow("学院", student.department)
                ProfileRow("专业", student.major)
                ProfileRow("班级", student.adminclass)
                ProfileRow("年级", student.grade)
            }
            if (profile != null) Row(
                modifier = Modifier.fillMaxWidth().padding(vertical = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    "提醒邮箱",
                    fontSize = 13.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.width(72.dp),
                )
                Text(
                    if (mail?.bound == true) mail.email ?: "已绑定" else "未绑定",
                    fontSize = 13.sp,
                    modifier = Modifier.weight(1f),
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                if (mail?.bound != true) {
                    TextButton(onClick = onOpenSite, contentPadding = PaddingValues(horizontal = 4.dp)) {
                        Text("去绑定", fontSize = 12.sp)
                    }
                }
            }
            if (profile != null) Hint(
                if (mail?.bound == true) "这里只显示打码邮箱，完整地址请到网站账号设置查看。"
                else "绑定后，自动刷新暂停或连续失败时会发邮件提醒。",
            )
        }

        Spacer(Modifier.height(14.dp))
        SectionCard {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("当前链接", fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
                Spacer(Modifier.weight(1f))
                Tag(
                    if (snapshot?.offline == true) "离线" else "已连接",
                    if (snapshot?.offline == true) WarmOrange else MaterialTheme.colorScheme.primary,
                )
            }
            Spacer(Modifier.height(4.dp))
            Hint(host)
            Spacer(Modifier.height(10.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                TextButton(onClick = onOpenSite, contentPadding = PaddingValues(horizontal = 4.dp)) {
                    Text("打开网站", fontSize = 12.sp)
                }
                TextButton(
                    onClick = {
                        context.startActivity(
                            Intent(
                                Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                                Uri.parse("package:${context.packageName}"),
                            ),
                        )
                    },
                    contentPadding = PaddingValues(horizontal = 4.dp),
                ) { Text("应用设置", fontSize = 12.sp) }
                TextButton(
                    onClick = { confirmRemove = true },
                    contentPadding = PaddingValues(horizontal = 4.dp),
                ) { Text("移除链接", fontSize = 12.sp, color = MaterialTheme.colorScheme.error) }
            }
        }

        Spacer(Modifier.height(14.dp))
        SectionCard {
            Text("权限", fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.height(4.dp))
            PermissionRow("通知", Reminders.canNotify(context)) {
                context.startActivity(
                    Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                        .putExtra(Settings.EXTRA_APP_PACKAGE, context.packageName),
                )
            }
            PermissionRow(
                "准时提醒",
                Build.VERSION.SDK_INT < 31 || alarms?.canScheduleExactAlarms() == true,
            ) {
                context.startActivity(
                    Intent(
                        Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM,
                        Uri.parse("package:${context.packageName}"),
                    ),
                )
            }
            if (Build.VERSION.SDK_INT >= 36) {
                PermissionRow(
                    "系统实时提醒（$liveName）",
                    manager?.canPostPromotedNotifications() == true,
                ) {
                    context.startActivity(
                        Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                            .putExtra(Settings.EXTRA_APP_PACKAGE, context.packageName),
                    )
                }
            }
        }

        Spacer(Modifier.height(14.dp))
        SectionCard {
            Text("帮助开发者优化 App", fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.height(4.dp))
            Hint("导出诊断文件，包含版本、设备、权限、链接状态和最近日志；不含小组件链接、密码或邮箱。")
            Spacer(Modifier.height(4.dp))
            Hint("遇到问题时先复现，再立即导出，日志更有用。")
            Spacer(Modifier.height(8.dp))
            TextButton(
                onClick = ::exportDiagnostics,
                enabled = !exporting,
                contentPadding = PaddingValues(0.dp),
            ) { Text(if (exporting) "正在导出…" else "导出诊断日志", fontSize = 12.sp) }
        }

        Spacer(Modifier.height(14.dp))
        SectionCard {
            Text("数据", fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.height(4.dp))
            Hint("桌面小组件、实时提醒和日历订阅都读取同一条只读链接。链接泄露后请在网站重新生成，或在这里移除。")
        }
        Spacer(Modifier.height(110.dp))
    }

    if (confirmRemove) {
        AlertDialog(
            onDismissRequest = { confirmRemove = false },
            title = { Text("移除小组件链接？") },
            text = { Text("App 里的课表缓存、提醒和桌面小组件会停止更新；网站账号不受影响。") },
            confirmButton = {
                TextButton(onClick = {
                    confirmRemove = false
                    onRemove()
                }) { Text("移除", color = MaterialTheme.colorScheme.error) }
            },
            dismissButton = { TextButton(onClick = { confirmRemove = false }) { Text("取消") } },
        )
    }
}

@Composable
private fun AboutScreen() {
    val context = LocalContext.current
    val version = remember {
        runCatching {
            context.packageManager.getPackageInfo(context.packageName, 0).versionName
        }.getOrNull().orEmpty()
    }
    Column(
        modifier = Modifier
            .fillMaxSize()
            .safeDrawingPadding()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 24.dp, vertical = 24.dp),
    ) {
        Text(
            "关于",
            fontSize = 34.sp,
            fontWeight = FontWeight.Bold,
            letterSpacing = (-0.5).sp,
            color = MaterialTheme.colorScheme.onBackground,
        )
        Spacer(Modifier.height(4.dp))
        Text("nufe课表 · 非官方工具", fontSize = 15.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.height(20.dp))

        SectionCard {
            Text("nufe课表", fontSize = 22.sp, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(3.dp))
            Text("把课程放在桌面。", fontSize = 14.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(12.dp))
            Text(
                "给南京财经大学同学用的非官方课表工具：统一身份认证登录后同步教务课表，" +
                    "提供网页、桌面小组件、实时提醒和日历订阅。",
                fontSize = 13.sp,
                lineHeight = 20.sp,
            )
            Spacer(Modifier.height(10.dp))
            Text("与南京财经大学无隶属关系。", fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }

        Spacer(Modifier.height(14.dp))
        SectionCard {
            AboutRow("官方网站", SITE_URL) {
                context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(SITE_URL)))
            }
            AboutRow("GitHub 仓库", "LokmenoWer/nufe-kebiao") {
                context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(REPO_URL)))
            }
            AboutRow("许可证", "GPL-3.0-or-later")
            AboutRow("版本", version.ifBlank { "1.1.1" })
            AboutRow("图标", "IconPark · Apache-2.0") {
                context.startActivity(
                    Intent(Intent.ACTION_VIEW, Uri.parse("https://github.com/bytedance/IconPark")),
                )
            }
            AboutRow("数据来源", "/mobile/<token>.json")
        }
        Spacer(Modifier.height(110.dp))
    }
}

@Composable
private fun ProfileRow(label: String, value: String?) {
    if (value.isNullOrBlank()) return
    Row(
        modifier = Modifier.fillMaxWidth().padding(vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            label,
            fontSize = 13.sp,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.width(72.dp),
        )
        Text(value, fontSize = 13.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}

@Composable
private fun PermissionRow(title: String, allowed: Boolean, action: () -> Unit) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(title, fontSize = 14.sp, modifier = Modifier.weight(1f))
        Text(
            if (allowed) "已允许" else "未允许",
            fontSize = 12.sp,
            color = if (allowed) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.error,
        )
        if (!allowed) {
            TextButton(onClick = action, contentPadding = PaddingValues(horizontal = 8.dp)) {
                Text("去设置", fontSize = 12.sp)
            }
        }
    }
}

@Composable
private fun AboutRow(title: String, value: String, onClick: (() -> Unit)? = null) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(enabled = onClick != null) { onClick?.invoke() }
            .padding(vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(title, fontSize = 14.sp, modifier = Modifier.weight(1f))
        Text(value, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        if (onClick != null) {
            Spacer(Modifier.width(4.dp))
            Icon(
                painter = painterResource(R.drawable.ic_chevron_right),
                contentDescription = null,
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.size(16.dp),
            )
        }
    }
}

@Composable
private fun GlassNav(selected: Tab, onSelect: (Tab) -> Unit, modifier: Modifier = Modifier) {
    val dark = isSystemInDarkTheme()
    val glass = if (dark) Color(0xFF1B1B1F).copy(alpha = 0.72f) else Color.White.copy(alpha = 0.72f)
    val halo = if (dark) Color(0xFF2A355C) else Color(0xFFE3EAFF)
    val border = Brush.linearGradient(
        listOf(
            Color.White.copy(alpha = 0.9f),
            Color.White.copy(alpha = 0.12f),
            Color.White.copy(alpha = 0.65f),
        ),
    )
    val scope = rememberCoroutineScope()
    val view = LocalView.current
    val scale = remember { Animatable(1f) }
    var direction by remember { mutableIntStateOf(1) }

    fun select(item: Tab) {
        view.performHapticFeedback(android.view.HapticFeedbackConstants.VIRTUAL_KEY)
        if (item != selected) {
            direction = if (item.ordinal > selected.ordinal) 1 else -1
        }
        scope.launch {
            scale.snapTo(1f)
            scale.animateTo(1.04f, tween(80))
            scale.animateTo(1f, spring(dampingRatio = 0.55f, stiffness = 420f))
        }
        onSelect(item)
    }

    Box(
        modifier
            .padding(horizontal = 24.dp)
            .navigationBarsPadding()
            .padding(bottom = 14.dp)
            .graphicsLayer {
                scaleX = scale.value
                scaleY = scale.value
            },
    ) {
        Box(
            Modifier
                .matchParentSize()
                .blur(24.dp)
                .background(
                    Brush.linearGradient(listOf(halo.copy(alpha = 0.9f), halo.copy(alpha = 0.15f))),
                    RoundedCornerShape(50),
                ),
        )
        Surface(
            modifier = Modifier
                .fillMaxWidth()
                .border(1.dp, border, RoundedCornerShape(50)),
            shape = RoundedCornerShape(50),
            color = glass,
            shadowElevation = 18.dp,
        ) {
            Row(
                modifier = Modifier.fillMaxWidth().padding(6.dp),
                horizontalArrangement = Arrangement.SpaceEvenly,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Tab.entries.forEach { item ->
                    NavItem(item, item == selected, direction) { select(item) }
                }
            }
        }
    }
}

@Composable
private fun RowScope.NavItem(tab: Tab, selected: Boolean, direction: Int, onClick: () -> Unit) {
    val color = if (selected) MaterialTheme.colorScheme.onBackground else MaterialTheme.colorScheme.onSurfaceVariant
    Column(
        modifier = Modifier
            .weight(1f)
            .clip(RoundedCornerShape(50))
            .background(
                if (selected) MaterialTheme.colorScheme.onBackground.copy(alpha = 0.08f) else Color.Transparent,
            )
            .clickable(onClick = onClick)
            .padding(vertical = 7.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            NavIcon(tab, color)
            Spacer(Modifier.width(6.dp))
            Text(
                tab.label,
                fontSize = 12.sp,
                fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
                color = color,
            )
        }
        Spacer(Modifier.height(3.dp))
        Box(Modifier.height(2.dp).width(18.dp)) {
            NavIndicator(selected, direction)
        }
    }
}

@Composable
private fun NavIndicator(visible: Boolean, direction: Int) {
    AnimatedVisibility(
        visible = visible,
        enter = fadeIn(tween(160)) +
            slideInHorizontally(tween(200)) { if (direction > 0) -it else it },
        exit = fadeOut(tween(120)) +
            slideOutHorizontally(tween(160)) { if (direction > 0) it else -it },
    ) {
        Box(
            Modifier
                .fillMaxSize()
                .background(MaterialTheme.colorScheme.onBackground, RoundedCornerShape(50)),
        )
    }
}

@Composable
private fun NavIcon(tab: Tab, color: Color) {
    val icon = when (tab) {
        Tab.Timetable -> R.drawable.ic_nav_calendar
        Tab.Mine -> R.drawable.ic_nav_user
        Tab.About -> R.drawable.ic_nav_info
    }
    Icon(
        painter = painterResource(icon),
        contentDescription = null,
        tint = color,
        modifier = Modifier.size(20.dp),
    )
}

@Composable
private fun TimeWheelDialog(
    initial: Int,
    onDismiss: () -> Unit,
    onConfirm: (Int) -> Unit,
) {
    val start = initial.coerceIn(1, 60)
    var selected by remember { mutableIntStateOf(start) }

    PopupCard(onDismissRequest = onDismiss) {
        Text("上课前提示时间", fontSize = 18.sp, fontWeight = FontWeight.SemiBold)
        Spacer(Modifier.height(2.dp))
        Text("提前多久提醒你", fontSize = 12.sp, color = Color(0xFF77777D))
        Spacer(Modifier.height(8.dp))
        MinuteWheel(initial = start, onSelected = { selected = it })
        Spacer(Modifier.height(8.dp))
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp, Alignment.End),
        ) {
            TextButton(
                onClick = onDismiss,
                colors = ButtonDefaults.textButtonColors(contentColor = Color(0xFF111111)),
            ) { Text("取消") }
            Button(
                onClick = { onConfirm(selected) },
                colors = ButtonDefaults.buttonColors(
                    containerColor = Color(0xFF111111),
                    contentColor = Color.White,
                ),
                shape = RoundedCornerShape(14.dp),
            ) { Text("确定") }
        }
    }
}

/** 数字单独滚动，“提前 / 分钟”固定；上下相邻数字按距离缩小、变淡并透视。 */
@Composable
private fun MinuteWheel(initial: Int, onSelected: (Int) -> Unit) {
    val pager = rememberPagerState(initialPage = initial.coerceIn(1, 60) - 1, pageCount = { 60 })
    val view = LocalView.current
    var lastPage by remember { mutableIntStateOf(pager.currentPage) }
    LaunchedEffect(pager) {
        snapshotFlow { pager.currentPage }.collect { page ->
            onSelected(page + 1)
            if (page != lastPage) {
                lastPage = page
                view.performHapticFeedback(android.view.HapticFeedbackConstants.CLOCK_TICK)
            }
        }
    }

    Row(
        modifier = Modifier.fillMaxWidth().height(220.dp),
        horizontalArrangement = Arrangement.Center,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text("提前", fontSize = 17.sp, color = Color(0xFF77777D))
        Spacer(Modifier.width(14.dp))
        Box(Modifier.width(84.dp).height(220.dp)) {
            VerticalPager(
                state = pager,
                modifier = Modifier.fillMaxSize(),
                pageSize = PageSize.Fixed(44.dp),
                contentPadding = PaddingValues(vertical = 88.dp),
                pageSpacing = 0.dp,
            ) { page ->
                val current = pager.currentPage + pager.currentPageOffsetFraction
                val distance = abs(page - current)
                Box(
                    modifier = Modifier.fillMaxWidth().height(44.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    Text(
                        text = (page + 1).toString(),
                        fontSize = 30.sp,
                        fontWeight = if (page == pager.currentPage) FontWeight.SemiBold else FontWeight.Normal,
                        color = if (page == pager.currentPage) Color(0xFF111111) else Color(0xFF77777D),
                        textAlign = TextAlign.Center,
                        modifier = Modifier.graphicsLayer {
                            val p = distance.coerceAtMost(3f)
                            alpha = (1f - p * 0.28f).coerceIn(0.15f, 1f)
                            val scale = (1f - p * 0.16f).coerceIn(0.55f, 1f)
                            scaleX = scale
                            scaleY = scale
                            rotationX = (page - current) * 12f
                            transformOrigin = TransformOrigin(0.5f, 0.5f)
                            cameraDistance = 24f * density
                        },
                    )
                }
            }
        }
        Spacer(Modifier.width(14.dp))
        Text("分钟", fontSize = 17.sp, color = Color(0xFF77777D))
    }
}

/** 页面弹窗统一从下向上展开；慢-快-慢的缓动，和页面内的展开动画一致。 */
@Composable
private fun PopupCard(
    onDismissRequest: () -> Unit,
    content: @Composable ColumnScope.() -> Unit,
) {
    Dialog(onDismissRequest = onDismissRequest) {
        val progress = remember { Animatable(0f) }
        LaunchedEffect(Unit) {
            progress.animateTo(1f, tween(300, easing = FastOutSlowInEasing))
        }
        Surface(
            shape = RoundedCornerShape(28.dp),
            color = Color.White,
            contentColor = Color(0xFF111111),
            modifier = Modifier
                .fillMaxWidth()
                .graphicsLayer {
                    val p = progress.value
                    translationY = (1f - p) * 42.dp.toPx()
                    scaleX = 0.98f + 0.02f * p
                    scaleY = 0.92f + 0.08f * p
                    transformOrigin = TransformOrigin(0.5f, 1f)
                    alpha = 0.5f + 0.5f * p
                },
        ) {
            Column(Modifier.padding(22.dp), content = content)
        }
    }
}

@Composable
private fun SectionCard(content: @Composable ColumnScope.() -> Unit) {
    Surface(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(26.dp),
        color = MaterialTheme.colorScheme.surface,
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.75f)),
    ) {
        Column(Modifier.padding(20.dp), content = content)
    }
}

@Composable
private fun Hint(text: String) {
    Text(
        text,
        fontSize = 12.sp,
        lineHeight = 18.sp,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

@Composable
private fun noteColor(tone: Tone): Color = when (tone) {
    Tone.WARN -> MaterialTheme.colorScheme.error
    Tone.ACCENT -> MaterialTheme.colorScheme.primary
    Tone.FAINT -> MaterialTheme.colorScheme.onSurfaceVariant
}

private enum class Tab(val label: String) {
    Timetable("课表"),
    Mine("我的"),
    About("关于"),
}

private fun liveUpdateName(): String {
    val name = (Build.MANUFACTURER + " " + Build.BRAND).lowercase()
    return when {
        "oppo" in name || "oneplus" in name || "realme" in name -> "流体云"
        "xiaomi" in name || "redmi" in name || "poco" in name -> "实时动态"
        else -> "系统实时提醒"
    }
}

private fun remainText(ms: Long): String {
    val total = (ms / 1000).coerceAtLeast(0)
    val hours = total / 3600
    val minutes = total % 3600 / 60
    return when {
        hours >= 1 && minutes >= 1 -> "$hours 小时 $minutes 分钟"
        hours >= 1 -> "$hours 小时"
        minutes >= 1 -> "$minutes 分钟"
        else -> "$total 秒"
    }
}
