package wiki.nufe.kebiao.ui

import android.Manifest
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
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.IntOffset
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import kotlinx.coroutines.launch
import wiki.nufe.kebiao.data.LinkInvalidException
import wiki.nufe.kebiao.data.Profile
import wiki.nufe.kebiao.data.AppUpdater
import wiki.nufe.kebiao.data.Repository
import wiki.nufe.kebiao.diag.Diag
import wiki.nufe.kebiao.remind.LiveUpdates
import wiki.nufe.kebiao.remind.Reminders
import wiki.nufe.kebiao.sync.LinkState
import wiki.nufe.kebiao.sync.LoginAlert
import wiki.nufe.kebiao.sync.Sync
import wiki.nufe.kebiao.widget.WidgetVersion

@Composable
internal fun AppRoot(
    incoming: String?,
    widgetView: String?,
    consumeIncoming: () -> Unit,
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

    var url by remember { mutableStateOf(repo.widgetUrl) }
    var snapshot by remember { mutableStateOf(repo.cached()) }
    var profile by remember { mutableStateOf<Profile?>(null) }
    val updater = remember { AppUpdater(context) }
    val updateFlow = remember { UpdateFlow(context, updater, scope) }
    var update by remember { mutableStateOf(updater.cached()) }
    var updateDismissed by remember { mutableStateOf(update?.let(updater::isDismissed) == true) }
    var bound by remember { mutableStateOf(repo.deviceId != null) }
    var invalid by remember { mutableStateOf(LinkState.isInvalid(context)) }
    var busy by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf<String?>(null) }
    var tab by remember { mutableStateOf(Tab.Timetable) }
    var pagePulse by remember { mutableIntStateOf(0) }
    var widgetWeek by remember { mutableStateOf(false) }
    var widgetPulse by remember { mutableIntStateOf(0) }
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

    val notificationPermission = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        notificationsOk = granted
        Reminders.rescheduleAsync(context)
    }

    val widgetVersion by WidgetVersion.flow.collectAsState()
    LaunchedEffect(widgetVersion) {
        if (widgetVersion > 0 && repo.widgetUrl != null) {
            repo.cached()?.let { snapshot = it }
            invalid = LinkState.isInvalid(context)
        }
    }

    fun syncSilentlyIfDue() {
        if (busy || repo.widgetUrl == null || LinkState.isInvalid(context)) return
        val current = snapshot
        val age = System.currentTimeMillis() - (current?.fetchedAtMs ?: 0L)
        if (current != null && !current.offline && age < 2 * 60_000L) return
        scope.launch {
            try {
                val snap = repo.fetch(refresh = false)
                if (snap != null) {
                    snapshot = snap
                    if (!snap.offline) {
                        LoginAlert.update(context, snap.payload)
                        Reminders.rescheduleAsync(context)
                        Sync.redraw(context)
                    }
                }
            } catch (_: LinkInvalidException) {
                invalid = true
                LinkState.markInvalid(context, true)
            } catch (_: Exception) {
            }
        }
    }

    DisposableEffect(lifecycle) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) {
                notificationsOk = Reminders.canNotify(context)
                exactOk = Build.VERSION.SDK_INT < 31 ||
                    exactAlarms?.canScheduleExactAlarms() == true
                promotedOk = Build.VERSION.SDK_INT < 36 ||
                    notificationManager?.canPostPromotedNotifications() == true
                batteryOk = ignoringBatteryOptimizations(context)
                updateFlow.onResume()
                syncSilentlyIfDue()
            }
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

    fun openSite() {
        val site = repo.siteUrl() ?: SITE_URL
        context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(site)))
    }

    fun save(raw: String) {
        val link = Repository.extractLink(raw)
        if (link == null) {
            Diag.w("link format invalid")
            message = "链接格式有误，请粘贴以 /mobile/<token>.json 结尾的配对链接。"
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
                    message = "无法连接服务器校验链接，请检查网络后重试。"
                    return@launch
                }
                snapshot = snap
                LoginAlert.update(context, snap.payload)
                url = link
                invalid = false
                tab = Tab.Timetable
                LinkState.markInvalid(context, false)
                Reminders.rescheduleAsync(context)
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
                message = "这条链接已失效，请在网站设置页重新生成。"
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
                    message = "当前无法连接服务器，已展示本机缓存课表。"
                } else {
                    Diag.i("refresh ok")
                    snap?.let { LoginAlert.update(context, it.payload) }
                }
                Reminders.rescheduleAsync(context)
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
        LoginAlert.clear(context)
        Sync.cancelAll(context)
        LiveUpdates.cancel(context)
        Reminders.rescheduleAsync(context)
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

    LaunchedEffect(widgetView) {
        val view = widgetView ?: return@LaunchedEffect
        consumeWidgetView()
        tab = Tab.Timetable
        pagePulse++
        widgetWeek = view == "week"
        widgetPulse++
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
                            update = update.takeUnless { updateDismissed },
                            updateFlow = updateFlow,
                            onDismissUpdate = {
                                update?.let(updater::dismiss)
                                updateDismissed = true
                            },
                            initialWeek = widgetWeek,
                            viewPulse = widgetPulse,
                            onViewConsumed = { widgetPulse = 0 },
                        )
                        Tab.Mine -> MineScreen(
                            repo = repo,
                            snapshot = snapshot,
                            profile = profile,
                            bound = bound,
                            notificationsOk = notificationsOk,
                            exactOk = exactOk,
                            promotedOk = promotedOk,
                            batteryOk = batteryOk,
                            onOpenSite = ::openSite,
                            onPair = ::save,
                            onRemove = ::removeLink,
                        )
                        Tab.About -> AboutScreen(
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
            onOpenBattery = { openBatteryOptimizationSettings(context) },
            onDismiss = { markPermissionsSeen() },
        )
    }
}
