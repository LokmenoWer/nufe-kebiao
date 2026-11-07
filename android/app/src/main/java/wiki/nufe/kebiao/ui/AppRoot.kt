package wiki.nufe.kebiao.ui

import android.Manifest
import android.app.Activity
import android.app.AlarmManager
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInHorizontally
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.togetherWith
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.IntOffset
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.repeatOnLifecycle
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.Dispatchers
import wiki.nufe.kebiao.data.Profile
import wiki.nufe.kebiao.data.AppUpdater
import wiki.nufe.kebiao.data.Repository
import wiki.nufe.kebiao.diag.Diag
import wiki.nufe.kebiao.remind.LiveUpdates
import wiki.nufe.kebiao.remind.Reminders
import wiki.nufe.kebiao.sync.LoginAlert
import wiki.nufe.kebiao.sync.Sync
import wiki.nufe.kebiao.widget.WidgetVersion

@Composable
internal fun AppRoot(
    loginRequested: Boolean,
    widgetView: String?,
    consumeLoginRequest: () -> Unit,
    consumeWidgetView: () -> Unit,
) {
    val context = LocalContext.current
    val repo = remember { Repository(context) }
    val scope = rememberCoroutineScope()
    val notificationManager = remember { context.getSystemService(NotificationManager::class.java) }
    val exactAlarms = remember { context.getSystemService(AlarmManager::class.java) }
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    val permissionPrefs = remember {
        context.getSharedPreferences("permission_intro", Context.MODE_PRIVATE)
    }

    var snapshot by remember { mutableStateOf(repo.cached()) }
    var hasTimetable by remember { mutableStateOf(repo.hasTimetable) }
    var needsLogin by remember { mutableStateOf(repo.needsLogin) }
    var profile by remember { mutableStateOf<Profile?>(null) }
    val updater = remember { AppUpdater(context) }
    val updateFlow = remember { UpdateFlow(context, updater, scope) }
    var update by remember { mutableStateOf(updater.cached()) }
    var updateDismissed by remember { mutableStateOf(update?.let(updater::isDismissed) == true) }
    var busy by remember { mutableStateOf(false) }
    var loggingIn by remember { mutableStateOf(false) }
    var fetchJob by remember { mutableStateOf<Job?>(null) }
    var message by remember { mutableStateOf<String?>(null) }
    var tab by remember { mutableStateOf(Tab.Timetable) }
    // 调课详情卡"✕"关闭持久化到 prefs：关掉后即使清后台重开也不再出现，直到出现新的变动。
    val changeSeenPrefs = remember { context.getSharedPreferences("kebiao", Context.MODE_PRIVATE) }
    var changeDismissed by remember { mutableStateOf(changeSeenPrefs.getStringSet("change_seen", emptySet())?.toSet().orEmpty()) }
    var pagePulse by remember { mutableIntStateOf(0) }
    var widgetWeek by remember { mutableStateOf(false) }
    var widgetPulse by remember { mutableIntStateOf(0) }
    var rhythmClock12h by remember { mutableStateOf(repo.rhythmClock12h) }
    var notificationsOk by remember { mutableStateOf(Reminders.canNotify(context)) }
    var exactOk by remember {
        mutableStateOf(Build.VERSION.SDK_INT < 31 || exactAlarms?.canScheduleExactAlarms() == true)
    }
    var promotedOk by remember {
        mutableStateOf(
            Build.VERSION.SDK_INT < 36 || notificationManager?.canPostPromotedNotifications() == true,
        )
    }
    var batteryOk by remember { mutableStateOf(ignoringBatteryOptimizations(context)) }
    var showPermissions by remember {
        mutableStateOf(
            !permissionPrefs.getBoolean("seen", false) &&
                (!notificationsOk || !exactOk || !promotedOk || !batteryOk),
        )
    }
    var showFeatureIntro by remember { mutableStateOf(false) }

    val notificationPermission = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        notificationsOk = granted
        Reminders.rescheduleAsync(context)
    }

    // cached() 要用 Keystore 解密整学期课表再投影，放到 IO 线程，避免每 30 秒/每次回前台卡主线程。
    suspend fun reloadLocalNow() {
        val loaded = withContext(Dispatchers.IO) {
            Triple(repo.cached(), repo.hasTimetable, repo.needsLogin) to repo.lastError
        }
        val (state, lastError) = loaded
        snapshot = state.first
        hasTimetable = state.second
        needsLogin = state.third
        if (!needsLogin && snapshot?.offline == false && lastError == null) message = null
    }

    fun reloadLocal() {
        scope.launch { reloadLocalNow() }
    }

    val schoolLogin = rememberLauncherForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) { result ->
        loggingIn = false
        scope.launch {
            reloadLocalNow()
            profile = repo.cachedProfile()
            if (result.resultCode == Activity.RESULT_OK) {
                message = null
                tab = Tab.Timetable
                LoginAlert.clear(context)
                Sync.schedule(context)
                Sync.armBoundary(context)
                Reminders.rescheduleAsync(context)
                Sync.redraw(context)
            }
        }
    }

    fun openSchoolLogin() {
        if (loggingIn || busy) return
        loggingIn = true
        message = null
        schoolLogin.launch(Intent(context, SchoolLoginActivity::class.java))
    }

    val widgetVersion by WidgetVersion.flow.collectAsState()
    LaunchedEffect(widgetVersion) {
        reloadLocalNow()
        profile = repo.cachedProfile()
    }

    // 整学期缓存按当前日生成视图，跨零点也不需要网络才能切换。
    LaunchedEffect(lifecycle) {
        lifecycle.repeatOnLifecycle(Lifecycle.State.RESUMED) {
            while (true) {
                reloadLocalNow()
                delay(30_000)
            }
        }
    }

    fun refresh(manual: Boolean) {
        if (busy || loggingIn || !repo.hasTimetable) return
        if (!manual) {
            if (repo.needsLogin) return
            val current = snapshot
            val age = System.currentTimeMillis() - (current?.fetchedAtMs ?: 0L)
            if (current != null && !current.offline && age < 2 * 60_000L) return
        }
        busy = true
        if (manual) message = null
        fetchJob = scope.launch {
            try {
                val snap = repo.fetch(refresh = manual)
                reloadLocalNow()
                if (snap != null) snapshot = snap
                if (manual) {
                    message = when {
                        needsLogin -> "请在 App 内登录学校账号；已保存的课表仍可离线查看。"
                        snap == null || snap.offline -> repo.lastError ?: "学校暂时不可达，继续展示本机课表。"
                        else -> repo.lastError
                    }
                }
                snapshot?.let { LoginAlert.update(context, it.payload) }
                Reminders.rescheduleAsync(context)
                Sync.armBoundary(context)
                Sync.redraw(context)
            } catch (error: CancellationException) {
                throw error
            } catch (error: Exception) {
                Diag.w("school refresh failed: ${error.javaClass.simpleName}")
                reloadLocalNow()
                if (manual) message = repo.lastError ?: "学校暂时不可达，已保留本机课表，请稍后重试。"
            } finally {
                busy = false
            }
        }
    }

    val onResume by rememberUpdatedState {
        notificationsOk = Reminders.canNotify(context)
        exactOk = Build.VERSION.SDK_INT < 31 || exactAlarms?.canScheduleExactAlarms() == true
        promotedOk = Build.VERSION.SDK_INT < 36 || notificationManager?.canPostPromotedNotifications() == true
        batteryOk = ignoringBatteryOptimizations(context)
        updateFlow.onResume()
        reloadLocal()
        refresh(manual = false)
    }
    DisposableEffect(lifecycle) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) onResume()
        }
        lifecycle.addObserver(observer)
        onDispose { lifecycle.removeObserver(observer) }
    }

    fun markPermissionsSeen() {
        showPermissions = false
        permissionPrefs.edit().putBoolean("seen", true).apply()
    }

    LaunchedEffect(notificationsOk, exactOk, promotedOk, batteryOk) {
        if (showPermissions && notificationsOk && exactOk && promotedOk && batteryOk) {
            markPermissionsSeen()
        }
    }

    // 升级上来的老用户（启动时已有课表）不再弹“欢迎使用”，直接记为已看过。
    val upgradedUser = remember {
        hasTimetable.also { had ->
            if (had && !changeSeenPrefs.getBoolean("feature_intro_seen", false)) {
                changeSeenPrefs.edit().putBoolean("feature_intro_seen", true).apply()
            }
        }
    }

    // 首次安装并注册（拿到课表）后，一次性介绍 App 特性；与权限弹窗不同时叠出。
    LaunchedEffect(hasTimetable, showPermissions) {
        if (!upgradedUser && hasTimetable && !showPermissions &&
            !changeSeenPrefs.getBoolean("feature_intro_seen", false)
        ) {
            showFeatureIntro = true
        }
    }

    fun clearLocalData() {
        fetchJob?.cancel()
        fetchJob = null
        repo.clearLocalData()
        LoginAlert.clear(context)
        Sync.cancelAll(context)
        LiveUpdates.cancel(context)
        Reminders.rescheduleAsync(context)
        scope.launch { Sync.redraw(context) }
        reloadLocal()
        profile = null
        busy = false
        message = null
        tab = Tab.Timetable
        Diag.i("local timetable cleared")
    }

    // 打开 App 时一天最多查一次新版本；查不到不打扰，“关于”里可以手动查
    LaunchedEffect(Unit) {
        updater.cleanInstalled()
        updateFlow.resumeAfterRestart(update)
        if (updater.checkDue) {
            runCatching { updater.check() }
                .onSuccess {
                    update = it
                    updateDismissed = it?.let(updater::isDismissed) == true
                }
                .onFailure { Diag.w("update check failed: ${it.javaClass.simpleName}") }
        }
    }

    // 个人资料只读取本机加密信息，不向任何服务器索取账号资料。
    LaunchedEffect(tab) {
        if (tab == Tab.Mine) profile = repo.cachedProfile()
    }

    LaunchedEffect(loginRequested, busy, loggingIn) {
        if (loginRequested && !busy && !loggingIn) {
            consumeLoginRequest()
            openSchoolLogin()
        }
    }

    LaunchedEffect(widgetView) {
        val view = widgetView ?: return@LaunchedEffect
        consumeWidgetView()
        tab = Tab.Timetable
        pagePulse++
        widgetWeek = view == "week"
        widgetPulse++
    }

    AppBackground {
        // 登录过期和旧缓存迁移都不阻挡已导入的离线课表。
        if (!hasTimetable) {
            SetupScreen(
                busy = busy || loggingIn,
                message = message,
                onLogin = ::openSchoolLogin,
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
                            snapshot = snapshot,
                            needsLogin = needsLogin,
                            busy = busy || loggingIn,
                            message = message,
                            onRefresh = { refresh(manual = true) },
                            onLogin = ::openSchoolLogin,
                            update = update.takeUnless { updateDismissed },
                            updateFlow = updateFlow,
                            onDismissUpdate = {
                                update?.let(updater::dismiss)
                                updateDismissed = true
                            },
                            dismissedChanges = changeDismissed,
                            onDismissChange = {
                                changeDismissed = changeDismissed + it
                                changeSeenPrefs.edit().putStringSet("change_seen", changeDismissed).apply()
                            },
                            initialWeek = widgetWeek,
                            viewPulse = widgetPulse,
                            onViewConsumed = { widgetPulse = 0 },
                            rhythmClock12h = rhythmClock12h,
                        )
                        Tab.Mine -> EnterUp {
                            MineScreen(
                                repo = repo,
                                snapshot = snapshot,
                                profile = profile,
                                needsLogin = needsLogin,
                                busy = busy || loggingIn,
                                notificationsOk = notificationsOk,
                                exactOk = exactOk,
                                promotedOk = promotedOk,
                                batteryOk = batteryOk,
                                onLogin = ::openSchoolLogin,
                                onRemove = ::clearLocalData,
                                rhythmClock12h = rhythmClock12h,
                                onRhythmClockChange = { twelve ->
                                    rhythmClock12h = twelve
                                    repo.rhythmClock12h = twelve
                                },
                            )
                        }
                        Tab.About -> EnterUp {
                            AboutScreen(
                                currentVersion = updater.currentVersion,
                                update = update,
                                updateFlow = updateFlow,
                                onCheckUpdate = {
                                    val found = updater.check()
                                    update = found
                                    updateDismissed = found?.let(updater::isDismissed) == true
                                    found
                                },
                            )
                        }
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
            batteryOk = batteryOk,
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
            onOpenAutoStart = { openAutoStartSettings(context) },
            onOpenBattery = { openBatteryOptimizationSettings(context) },
            onDismiss = { markPermissionsSeen() },
        )
    }

    if (showFeatureIntro) {
        FeatureIntroDialog(
            onDismiss = {
                changeSeenPrefs.edit().putBoolean("feature_intro_seen", true).apply()
                showFeatureIntro = false
            },
        )
    }
}
