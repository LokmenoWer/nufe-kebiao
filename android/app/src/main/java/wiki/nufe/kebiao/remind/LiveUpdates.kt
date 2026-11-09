package wiki.nufe.kebiao.remind

import android.app.AlarmManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.graphics.drawable.Icon
import android.os.Build
import androidx.core.content.ContextCompat
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import wiki.nufe.kebiao.R
import wiki.nufe.kebiao.data.LiveDismissMode
import wiki.nufe.kebiao.data.Now
import wiki.nufe.kebiao.data.Repository
import wiki.nufe.kebiao.data.lessonMs
import wiki.nufe.kebiao.diag.Diag
import wiki.nufe.kebiao.ui.LiveUpdateTier
import wiki.nufe.kebiao.ui.MainActivity
import wiki.nufe.kebiao.ui.canPostPromotedNotificationsCompat
import wiki.nufe.kebiao.ui.detectLiveUpdateTier
import wiki.nufe.kebiao.ui.liveUpdateName

/**
 * 多系统灵动岛 / 实时活动上课提醒：
 * - Android 16+ 原生 Live Updates（ColorOS 流体云、OriginOS 原子岛、MagicOS 灵动胶囊、HyperOS 超级岛/焦点通知、
 *   One UI 实时通知 Now Bar、Flyme 实况通知、Pixel/AOSP 状态栏胶囊）
 * - 魅族 Flyme 11+ 免白名单实况通知胶囊（`flyme.permission.LIVE_NOTIFICATION` + `notification.live.capsule`）
 * - 各大国产定制系统 Notification Extras 扩展协议（`miui.focus.param`、`notification.superx.*`、`notification.live.*`）
 *
 * 复用 Reminders 的课前闹钟作为触发点：到点后发一条 promoted / 扩展 ongoing 通知，
 * 再在上课与设定收起时刻自动更新或撤销。数据来自本机加密的整学期课表缓存（Repository.cached()）。
 */
object LiveUpdates {
    const val ACTION_START = "wiki.nufe.kebiao.LIVE_START"
    const val ACTION_END = "wiki.nufe.kebiao.LIVE_END"

    internal const val CHANNEL = "class_live_update"
    internal const val NOTIFICATION_ID = 9201
    internal const val GUARD_NOTIFICATION_ID = 9200
    private const val REQ_START = 9201
    private const val REQ_END = 9202
    private const val GUARD_PREFS = "live_update_guard"

    fun itemFrom(intent: Intent): Reminders.Item = Reminders.Item(
        atMs = intent.getLongExtra("at", 0L),
        title = intent.getStringExtra("title").orEmpty(),
        text = intent.getStringExtra("text").orEmpty(),
        date = intent.getStringExtra("date").orEmpty(),
        start = intent.getStringExtra("start").orEmpty(),
        end = intent.getStringExtra("end").orEmpty(),
        name = intent.getStringExtra("name").orEmpty(),
        room = intent.getStringExtra("room").orEmpty(),
        changed = intent.getBooleanExtra("changed", false),
        was = intent.getStringExtra("was"),
    )

    internal fun configFrom(intent: Intent?, context: Context): LiveConfig {
        if (intent != null && intent.hasExtra("cfg_dismiss")) {
            return LiveConfig(
                dismissMode = LiveDismissMode.fromCode(intent.getStringExtra("cfg_dismiss")),
                progressEnabled = intent.getBooleanExtra("cfg_progress", false),
                chronometer = intent.getBooleanExtra("cfg_chrono", false),
            )
        }
        val prefs = context.getSharedPreferences(GUARD_PREFS, Context.MODE_PRIVATE)
        if (prefs.contains("cfg_dismiss")) {
            return LiveConfig(
                dismissMode = LiveDismissMode.fromCode(prefs.getString("cfg_dismiss", null)),
                progressEnabled = prefs.getBoolean("cfg_progress", false),
                chronometer = prefs.getBoolean("cfg_chrono", false),
            )
        }
        return LiveConfig.from(Repository(context))
    }

    internal fun isPreview(intent: Intent?, context: Context): Boolean {
        if (intent != null && intent.hasExtra("preview")) {
            return intent.getBooleanExtra("preview", false)
        }
        return context.getSharedPreferences(GUARD_PREFS, Context.MODE_PRIVATE).getBoolean("preview", false)
    }

    internal fun resolveDismissMs(
        context: Context,
        item: Reminders.Item,
        config: LiveConfig = configFrom(null, context),
        isPreview: Boolean = isPreview(null, context),
    ): Long {
        val startMs = lessonMs(item.date, item.start)
        val endMs = lessonMs(item.date, item.end)
        val dismissMs = OemIslandExtras.resolveDismissMs(startMs, endMs, config.dismissMode)
        // 预览卡片在开课时刻前即自动收起，不切换为“正在上课”
        return if (isPreview && startMs > 0L) minOf(startMs, dismissMs) else dismissMs
    }

    internal fun available(context: Context): Boolean {
        val tier = detectLiveUpdateTier()
        if (tier == LiveUpdateTier.UNSUPPORTED) return false
        val manager = context.getSystemService(NotificationManager::class.java) ?: return false
        if (!manager.areNotificationsEnabled()) return false
        // 用户可能只关掉灵动提醒通道：此时退回普通提醒通道
        val channel = manager.getNotificationChannel(CHANNEL)
        if (channel != null && channel.importance == NotificationManager.IMPORTANCE_NONE) return false
        return when (tier) {
            LiveUpdateTier.NATIVE_PROMOTED -> canPostPromotedNotificationsCompat(manager)
            LiveUpdateTier.FLYME_CAPSULE, LiveUpdateTier.OEM_EXTRAS -> true
            LiveUpdateTier.UNSUPPORTED -> false
        }
    }

    /** 守护服务被系统重启时用：用户仍开着提醒和灵动显示、且系统仍允许，才恢复卡片。 */
    internal fun restorable(context: Context): Boolean {
        val repo = Repository(context)
        return repo.alarmEnabled && repo.liveUpdateEnabled && available(context)
    }

    /**
     * 灵动卡片是否自身已具备声音/震动提醒能力，从而可以让同次普通提醒静默。
     * 默认 `class_live_update` 通道为 `IMPORTANCE_LOW`（无声，避免每分钟刷新时反复响铃），
     * 此时必须保留普通课前提醒的声音/震动；仅当用户在系统设置中将灵动通道调至默认及以上且开启声音/震动时才静默普通提醒。
     */
    internal fun replacesAlert(context: Context): Boolean {
        if (detectLiveUpdateTier() != LiveUpdateTier.NATIVE_PROMOTED) return false
        val manager = context.getSystemService(NotificationManager::class.java) ?: return false
        val channel = manager.getNotificationChannel(CHANNEL) ?: return false
        return channel.importance >= NotificationManager.IMPORTANCE_DEFAULT &&
            (channel.sound != null || channel.shouldVibrate())
    }

    /** 当前是否有灵动提醒卡片正在状态栏/通知栏展示。 */
    fun isActive(context: Context): Boolean {
        val manager = context.getSystemService(NotificationManager::class.java) ?: return false
        return runCatching {
            manager.activeNotifications.any { it.id == NOTIFICATION_ID }
        }.getOrDefault(false)
    }

    /**
     * 立即预览灵动效果：优先取本机缓存里的下一节课名称与教室；
     * 若下一节课在 60 分钟以内则按真实时间倒数，若距今超过 60 分钟或当前无待上课程则生成一节 15 分钟后开课的预览课，
     * 方便用户随时在设置页或调试页验证当前系统的灵动岛样式与自定义配置。
     */
    fun preview(context: Context): Boolean {
        if (!available(context)) return false
        val repo = Repository(context)
        val now = Now.current()
        val lead = if (repo.remindMinutes > 0) repo.remindMinutes else Repository.DEFAULT_REMIND_MINUTES
        val planned = repo.cached()?.payload?.let { payload ->
            Reminders.plan(payload, now, lead).firstOrNull()
        }
        val item = if (planned != null &&
            lessonMs(planned.date, planned.start) in (now.ms + 60_000L)..(now.ms + 60 * 60_000L)
        ) {
            planned
        } else {
            val zone = ZoneId.of("Asia/Shanghai")
            val zdt = Instant.ofEpochMilli(now.ms).atZone(zone)
            val startZdt = zdt.plusMinutes(15)
            // 课表时间只有 HH:mm、按开课日期解析；跨零点时把下课钳到当天 23:59，避免下课早于上课导致预览失败。
            val endZdt = zdt.plusMinutes(60).let {
                if (it.toLocalDate() == startZdt.toLocalDate()) it
                else startZdt.withHour(23).withMinute(59)
            }
            val fmt = DateTimeFormatter.ofPattern("HH:mm")
            val courseName = planned?.name?.takeIf { it.isNotBlank() } ?: "高等数学"
            val roomName = planned?.room?.takeIf { it.isNotBlank() } ?: "博观楼 101"
            Reminders.Item(
                atMs = now.ms,
                title = "15 分钟后：$courseName",
                text = "${startZdt.format(fmt)}–${endZdt.format(fmt)} · $roomName",
                date = startZdt.toLocalDate().toString(),
                start = startZdt.format(fmt),
                end = endZdt.format(fmt),
                name = courseName,
                room = roomName,
                changed = planned?.changed ?: false,
                was = planned?.was,
            )
        }
        return post(context, item, LiveConfig.from(repo), isPreview = true)
    }

    /** 用户在设置中修改灵动自定义项时：若当前正展示着卡片，立即用新配置刷新卡片与守护服务。 */
    fun refreshActiveIfShown(context: Context) {
        if (!isActive(context)) return
        val item = savedItem(context) ?: return
        val preview = isPreview(null, context)
        // 例如上课中改成“上课时立即收起”：新配置下已过收起时间，直接撤掉卡片和守护服务。
        if (!post(context, item, LiveConfig.from(Repository(context)), isPreview = preview)) cancel(context)
    }

    /** 课前闹钟到点：发 Live Update，并按用户设定的收起模式安排上课切换与自动撤销（预览模式不注册系统闹钟）。 */
    fun post(
        context: Context,
        item: Reminders.Item,
        config: LiveConfig = LiveConfig.from(Repository(context)),
        isPreview: Boolean = false,
    ): Boolean {
        if (!available(context) || item.date.isBlank() || item.start.isBlank() || item.end.isBlank()) return false
        val now = System.currentTimeMillis()
        val startMs = lessonMs(item.date, item.start)
        val endMs = lessonMs(item.date, item.end)
        val dismissMs = resolveDismissMs(context, item, config, isPreview)
        if (dismissMs <= now) return false
        ensureChannel(context)
        val phase = if (startMs > now) Phase.BEFORE else Phase.DURING
        notify(context, item, phase, startMs, endMs, config, isUpdate = false, isPreview = isPreview)
        if (!isPreview) {
            if (config.dismissMode != LiveDismissMode.AT_CLASS_START && startMs > now) {
                schedule(context, item, config, ACTION_START, startMs, REQ_START)
            } else {
                cancelPhase(context, ACTION_START, REQ_START)
            }
            schedule(context, item, config, ACTION_END, dismissMs, REQ_END)
        }
        remember(context, item, dismissMs, config, isPreview)
        startGuard(context, item, config, isPreview)
        return true
    }

    /** 上课时刻：把“即将上课”切换成“正在上课”，并按用户设定的收起时机自动撤销。 */
    fun startClass(context: Context, intent: Intent) {
        if (!available(context)) {
            cancel(context)
            return
        }
        val item = itemFrom(intent)
        val config = configFrom(intent, context)
        val startMs = lessonMs(item.date, item.start)
        val endMs = lessonMs(item.date, item.end)
        val dismissMs = OemIslandExtras.resolveDismissMs(startMs, endMs, config.dismissMode)
        if (dismissMs <= System.currentTimeMillis()) {
            cancel(context)
            return
        }
        ensureChannel(context)
        notify(context, item, Phase.DURING, startMs, endMs, config, isUpdate = true)
        schedule(context, item, config, ACTION_END, dismissMs, REQ_END)
        remember(context, item, dismissMs, config)
        startGuard(context, item, config)
    }

    fun cancel(context: Context) {
        stopGuard(context)
        context.getSharedPreferences(GUARD_PREFS, Context.MODE_PRIVATE).edit().clear().apply()
        context.getSystemService(NotificationManager::class.java)?.apply {
            cancel(NOTIFICATION_ID)
            cancel(GUARD_NOTIFICATION_ID)
        }
        cancelPhase(context, ACTION_START, REQ_START)
        cancelPhase(context, ACTION_END, REQ_END)
    }

    private enum class Phase { BEFORE, DURING }

    private fun notify(
        context: Context,
        item: Reminders.Item,
        phase: Phase,
        startMs: Long,
        endMs: Long,
        config: LiveConfig,
        isUpdate: Boolean,
        isPreview: Boolean = false,
    ) {
        val notification = buildNotification(context, item, phase, startMs, endMs, config, isUpdate, isPreview) ?: return
        context.getSystemService(NotificationManager::class.java).notify(NOTIFICATION_ID, notification)
    }

    /** 前台守护服务复用同一张卡片；没有这张卡片时不需要保活。 */
    internal fun buildNotification(
        context: Context,
        item: Reminders.Item,
        config: LiveConfig = configFrom(null, context),
        isUpdate: Boolean = true,
        isPreview: Boolean = isPreview(null, context),
    ): Notification? {
        if (detectLiveUpdateTier() == LiveUpdateTier.UNSUPPORTED) return null
        ensureChannel(context)
        val startMs = lessonMs(item.date, item.start)
        val endMs = lessonMs(item.date, item.end)
        val phase = if (startMs > System.currentTimeMillis()) Phase.BEFORE else Phase.DURING
        return buildNotification(context, item, phase, startMs, endMs, config, isUpdate, isPreview)
    }

    private fun buildNotification(
        context: Context,
        item: Reminders.Item,
        phase: Phase,
        startMs: Long,
        endMs: Long,
        config: LiveConfig,
        isUpdate: Boolean,
        isPreview: Boolean = false,
    ): Notification? {
        if (detectLiveUpdateTier() == LiveUpdateTier.UNSUPPORTED) return null
        val now = System.currentTimeMillis()
        val room = OemIslandExtras.displayRoom(item.room)
        val changedText = if (item.changed) "（已调课${item.was?.let { "：$it" } ?: ""}）" else ""
        val course = (item.name.ifBlank { "下一节课" }) + changedText
        val titleLine = "${item.start}-${item.end} $room"
        val remain = remainingMinutes(phase, startMs, endMs)
        val status = if (phase == Phase.BEFORE) "${remain}分钟后上课" else "正在上课 · 还有 $remain 分钟下课"
        val short = OemIslandExtras.formatCapsuleShortText(
            isBeforeClass = phase == Phase.BEFORE,
            remainMinutes = remain,
            dismissMode = config.dismissMode,
        )
        val progress = OemIslandExtras.computeProgressPercent(
            remindAtMs = item.atMs,
            startMs = startMs,
            endMs = endMs,
            nowMs = now,
        )
        val rawDismissMs = OemIslandExtras.resolveDismissMs(startMs, endMs, config.dismissMode)
        val dismissMs = if (isPreview && startMs > 0L) minOf(startMs, rawDismissMs) else rawDismissMs
        val timeout = (dismissMs - now).coerceAtLeast(1L)
        val open = PendingIntent.getActivity(
            context,
            0,
            Intent(context, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val builder = Notification.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_notify_calendar)
            .setContentTitle(titleLine)
            .setContentText(course)
            .setSubText(status)
            .setContentIntent(open)
            .setOngoing(true)
            .setCategory(Notification.CATEGORY_PROGRESS)
            .setVisibility(Notification.VISIBILITY_PUBLIC)
            .setOnlyAlertOnce(true)
            .setTimeoutAfter(timeout)

        if (config.chronometer) {
            val targetMs = if (phase == Phase.BEFORE) startMs else endMs
            builder.setWhen(targetMs)
                .setShowWhen(true)
                .setUsesChronometer(true)
                .setChronometerCountDown(true)
        } else {
            builder.setShowWhen(false)
        }

        if (Build.VERSION.SDK_INT >= 36) {
            if (config.progressEnabled) {
                applyProgressStyle(context, builder, progress)
            }
            applyPromotedFlags(builder, short)
        }

        OemIslandExtras.applyOemExtras(
            context = context,
            builder = builder,
            item = item,
            titleLine = titleLine,
            course = course,
            status = status,
            shortText = short,
            progress = progress,
            showProgress = config.progressEnabled,
            isUpdate = isUpdate,
            openIntent = open,
        )
        return builder.build()
    }

    /** 剩余分钟数：课前倒数到开课，课上倒数到下课。 */
    private fun remainingMinutes(phase: Phase, startMs: Long, endMs: Long): Long {
        val target = if (phase == Phase.BEFORE) startMs else endMs
        return ((target - System.currentTimeMillis() + 59_999L) / 60_000L).coerceAtLeast(1L)
    }

    /**
     * 剩余分钟变化时重发同一张卡片（系统倒计时只有秒级，胶囊也只显示一段文字）。
     * 返回距下一次分钟跳变的毫秒数；0 表示卡片已到收起时间。
     */
    internal fun refresh(
        context: Context,
        item: Reminders.Item,
        config: LiveConfig = configFrom(null, context),
        isPreview: Boolean = isPreview(null, context),
    ): Long {
        val now = System.currentTimeMillis()
        val startMs = lessonMs(item.date, item.start)
        val endMs = lessonMs(item.date, item.end)
        val dismissMs = resolveDismissMs(context, item, config, isPreview)
        if (dismissMs <= now) return 0L
        val manager = context.getSystemService(NotificationManager::class.java) ?: return 0L
        // 卡片已被用户划掉，或 cancel() 已撤销：不再重发，否则一分钟后又会冒出来。
        if (manager.activeNotifications.none { it.id == NOTIFICATION_ID }) return 0L
        buildNotification(context, item, config, isUpdate = true, isPreview = isPreview)?.let {
            manager.notify(NOTIFICATION_ID, it)
        }
        val target = if (now < startMs) startMs else endMs
        val left = target - now
        if (left <= 0) return 0L
        val rem = left % 60_000L
        return if (rem == 0L) 60_000L else rem.coerceAtLeast(1_000L)
    }

    /** Android 16 ProgressStyle：通过 runCatching 保护，防止部分裁剪 API 36 框架缺少类或方法。 */
    private fun applyProgressStyle(context: Context, builder: Notification.Builder, progress: Int) {
        if (Build.VERSION.SDK_INT < 36) return
        runCatching {
            val style = Notification.ProgressStyle()
                .setProgress(progress.coerceIn(0, 100))
                .setProgressSegments(
                    listOf(
                        Notification.ProgressStyle.Segment(50).setColor(OemIslandExtras.BRAND_COLOR_INT),
                        Notification.ProgressStyle.Segment(50).setColor(OemIslandExtras.BRAND_TRACK_COLOR_INT),
                    ),
                )
                .setProgressTrackerIcon(Icon.createWithResource(context, R.drawable.ic_notify_calendar))
                .setStyledByProgress(true)
            builder.setStyle(style)
        }
    }

    /** 部分魔改 API 36 框架可能没实现这两个方法，反射调用，缺失就跳过。 */
    private fun applyPromotedFlags(builder: Notification.Builder, shortText: String) {
        try {
            Notification.Builder::class.java
                .getMethod("setRequestPromotedOngoing", Boolean::class.javaPrimitiveType)
                .invoke(builder, true)
        } catch (_: Throwable) {
        }
        try {
            Notification.Builder::class.java
                .getMethod("setShortCriticalText", String::class.java)
                .invoke(builder, shortText)
        } catch (_: Throwable) {
        }
    }

    internal fun ensureChannel(context: Context) {
        val manager = context.getSystemService(NotificationManager::class.java) ?: return
        if (manager.getNotificationChannel(CHANNEL) != null) return
        val liveName = liveUpdateName()
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL, "${liveName}上课提醒", NotificationManager.IMPORTANCE_LOW).apply {
                description = "Android 实时活动 / $liveName 上课提醒"
                lockscreenVisibility = Notification.VISIBILITY_PUBLIC
            },
        )
    }

    private fun schedule(
        context: Context,
        item: Reminders.Item,
        config: LiveConfig,
        action: String,
        atMs: Long,
        requestCode: Int,
    ) {
        if (atMs <= System.currentTimeMillis()) return
        val alarms = context.getSystemService(AlarmManager::class.java)
        val intent = Intent(context, ReminderReceiver::class.java)
            .setAction(action)
            .also { putExtras(it, item, config) }
        val pending = PendingIntent.getBroadcast(
            context, requestCode, intent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        // 国产 ROM 会把普通精确闹钟放进最长 1 小时的弹性窗口；改用用户可见的 alarm clock
        // 才能保证开课时刻切换，关闭则依靠通知 timeout 与此闹钟双保险。
        val show = PendingIntent.getActivity(
            context,
            requestCode,
            Intent(context, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        runCatching {
            alarms.setAlarmClock(AlarmManager.AlarmClockInfo(atMs, show), pending)
        }.onFailure {
            if (Build.VERSION.SDK_INT < 31 || alarms.canScheduleExactAlarms()) {
                alarms.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, atMs, pending)
            } else {
                alarms.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, atMs, pending)
            }
        }
    }

    private fun cancelPhase(context: Context, action: String, requestCode: Int) {
        val alarms = context.getSystemService(AlarmManager::class.java)
        val intent = Intent(context, ReminderReceiver::class.java).setAction(action)
        val pending = PendingIntent.getBroadcast(
            context, requestCode, intent, PendingIntent.FLAG_NO_CREATE or PendingIntent.FLAG_IMMUTABLE,
        ) ?: return
        alarms.cancel(pending)
        pending.cancel()
    }

    /** 记住当前卡片与配置，App 下次打开或守护进程重启时若还没到收起时间，就按原样恢复。 */
    internal fun remember(
        context: Context,
        item: Reminders.Item,
        dismissMs: Long,
        config: LiveConfig = configFrom(null, context),
        isPreview: Boolean = false,
    ) {
        context.getSharedPreferences(GUARD_PREFS, Context.MODE_PRIVATE).edit()
            .putLong("dismiss", dismissMs)
            .putLong("at", item.atMs)
            .putString("date", item.date)
            .putString("start", item.start)
            .putString("end", item.end)
            .putString("name", item.name)
            .putString("room", item.room)
            .putBoolean("changed", item.changed)
            .putString("was", item.was)
            .putString("cfg_dismiss", config.dismissMode.code)
            .putBoolean("cfg_progress", config.progressEnabled)
            .putBoolean("cfg_chrono", config.chronometer)
            .putBoolean("preview", isPreview)
            .apply()
    }

    internal fun clearRemembered(context: Context) {
        context.getSharedPreferences(GUARD_PREFS, Context.MODE_PRIVATE).edit().clear().apply()
    }

    /** 读出“当前还在展示”的灵动卡片数据；已过收起时间或没数据时返回 null。供重启后恢复用。 */
    internal fun savedItem(context: Context): Reminders.Item? {
        val prefs = context.getSharedPreferences(GUARD_PREFS, Context.MODE_PRIVATE)
        if (prefs.getLong("dismiss", 0L) <= System.currentTimeMillis()) return null
        val date = prefs.getString("date", "").orEmpty()
        val start = prefs.getString("start", "").orEmpty()
        val end = prefs.getString("end", "").orEmpty()
        if (date.isBlank() || start.isBlank() || end.isBlank()) return null
        return Reminders.Item(
            atMs = prefs.getLong("at", 0L),
            title = "",
            text = "",
            date = date,
            start = start,
            end = end,
            name = prefs.getString("name", "").orEmpty(),
            room = prefs.getString("room", "").orEmpty(),
            changed = prefs.getBoolean("changed", false),
            was = prefs.getString("was", null),
        )
    }

    /** 打开 App 时调用：正在展示灵动卡片就补上前台守护，防止清理后台时进程被杀。 */
    internal fun ensureGuard(context: Context) {
        val item = savedItem(context)
        if (item == null) {
            context.getSharedPreferences(GUARD_PREFS, Context.MODE_PRIVATE).edit().clear().apply()
            return
        }
        startGuard(context, item, configFrom(null, context), isPreview(null, context))
    }

    private fun startGuard(
        context: Context,
        item: Reminders.Item,
        config: LiveConfig = configFrom(null, context),
        isPreview: Boolean = false,
    ) {
        if (detectLiveUpdateTier() == LiveUpdateTier.UNSUPPORTED) return
        val intent = Intent(context, LiveUpdateGuardService::class.java).also {
            putExtras(it, item, config, isPreview)
        }
        runCatching { ContextCompat.startForegroundService(context, intent) }
            .onFailure { Diag.w("live update guard start failed: ${it.javaClass.simpleName}") }
    }

    private fun stopGuard(context: Context) {
        runCatching { context.stopService(Intent(context, LiveUpdateGuardService::class.java)) }
    }

    internal fun putExtras(
        intent: Intent,
        item: Reminders.Item,
        config: LiveConfig? = null,
        isPreview: Boolean = false,
    ) {
        intent.putExtra("at", item.atMs)
            .putExtra("title", item.title)
            .putExtra("text", item.text)
            .putExtra("date", item.date)
            .putExtra("start", item.start)
            .putExtra("end", item.end)
            .putExtra("name", item.name)
            .putExtra("room", item.room)
            .putExtra("changed", item.changed)
            .putExtra("was", item.was)
            .putExtra("preview", isPreview)
        if (config != null) {
            intent.putExtra("cfg_dismiss", config.dismissMode.code)
                .putExtra("cfg_progress", config.progressEnabled)
                .putExtra("cfg_chrono", config.chronometer)
        }
    }
}
