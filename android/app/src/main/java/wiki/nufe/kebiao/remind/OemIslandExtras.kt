package wiki.nufe.kebiao.remind

import android.app.Notification
import android.app.PendingIntent
import android.content.Context
import android.graphics.drawable.Icon
import android.os.Build
import android.os.Bundle
import org.json.JSONObject
import wiki.nufe.kebiao.R
import wiki.nufe.kebiao.data.LiveCapsuleTextMode
import wiki.nufe.kebiao.data.LiveDismissMode
import wiki.nufe.kebiao.data.Repository
import wiki.nufe.kebiao.ui.RomFamily
import wiki.nufe.kebiao.ui.detectRomFamily

/**
 * 灵动显示配置快照：主进程与 `:guard` 守护进程通过 Intent Extra 及持久化 Prefs 同步，
 * 避免多进程下 `SharedPreferences` 缓存不同步。
 */
data class LiveConfig(
    val dismissMode: LiveDismissMode = LiveDismissMode.FIVE_MIN_AFTER_START,
    val capsuleTextMode: LiveCapsuleTextMode = LiveCapsuleTextMode.COUNTDOWN,
    val progressEnabled: Boolean = false,
    val chronometer: Boolean = false,
) {
    companion object {
        fun from(repo: Repository): LiveConfig = LiveConfig(
            dismissMode = repo.liveDismissMode,
            capsuleTextMode = repo.liveCapsuleTextMode,
            progressEnabled = repo.liveProgressEnabled,
            chronometer = repo.liveChronometer,
        )
    }
}

/**
 * 各厂商灵动岛 / 实况通知 / 原子岛 / 流体云 Notification Extras 适配器与胶囊格式化工具。
 */
internal object OemIslandExtras {
    const val BRAND_COLOR_INT: Int = 0xFF4B63E0.toInt()
    const val BRAND_TRACK_COLOR_INT: Int = 0xFFB9C3FF.toInt()
    const val BRAND_COLOR_HEX: String = "#4B63E0"

    /** 根据用户选择的收起模式计算灵动卡片收起时间戳。 */
    fun resolveDismissMs(
        startMs: Long,
        endMs: Long,
        mode: LiveDismissMode = LiveDismissMode.FIVE_MIN_AFTER_START,
    ): Long = when (mode) {
        LiveDismissMode.AT_CLASS_START -> startMs
        LiveDismissMode.FIVE_MIN_AFTER_START -> minOf(endMs, startMs + Reminders.IN_CLASS_VISIBLE_MS)
        LiveDismissMode.UNTIL_CLASS_END -> endMs
    }

    /**
     * 提取适合状态栏胶囊展示的紧凑教室名称（控制在 7 个字符以内）：
     * 例如 `"博观楼 101"` -> `"博观101"`，`"德济楼 202"` -> `"德济202"`，`"博远楼JC406"` -> `"博远JC406"`。
     */
    fun compactRoom(room: String): String {
        val trimmed = room.trim()
        if (trimmed.isEmpty() || trimmed == "地点待定" || trimmed == "待定") return ""
        val noSpace = trimmed.replace(Regex("""\s+"""), "")
            .replace("教学楼", "")
            .replace("实验楼", "")
        val withoutBuildingSuffix = noSpace.replace("楼", "").ifBlank { noSpace }
        if (withoutBuildingSuffix.length <= 7) return withoutBuildingSuffix
        // 若仍超长，优先取末尾的房间号/代码（如 J1-203、JC406）
        val codeMatch = Regex("""[A-Za-z0-9-]{2,7}$""").find(withoutBuildingSuffix)
        if (codeMatch != null) return codeMatch.value
        return withoutBuildingSuffix.take(7)
    }

    /**
     * 生成状态栏胶囊（`setShortCriticalText` 及各 ROM 胶囊摘要区）显示的简短文案。
     * Android 16 规范建议不超过 7 个字符，避免在挖孔旁被截断。
     */
    fun formatCapsuleShortText(
        item: Reminders.Item,
        isBeforeClass: Boolean,
        remainMinutes: Long,
        dismissMode: LiveDismissMode = LiveDismissMode.FIVE_MIN_AFTER_START,
        textMode: LiveCapsuleTextMode = LiveCapsuleTextMode.COUNTDOWN,
    ): String {
        val remain = remainMinutes.coerceAtLeast(1L)
        val countdownText = if (isBeforeClass) {
            if (remain >= 100L) "剩${remain}分" else "剩 $remain 分钟"
        } else {
            if (dismissMode == LiveDismissMode.UNTIL_CLASS_END) {
                "剩 ${remain}分"
            } else {
                "正在上课"
            }
        }
        return when (textMode) {
            LiveCapsuleTextMode.COUNTDOWN -> countdownText
            LiveCapsuleTextMode.ROOM_FIRST -> compactRoom(item.room).ifBlank { countdownText }
            LiveCapsuleTextMode.COURSE_SHORT -> item.name.trim().take(5).ifBlank { countdownText }
        }
    }

    /**
     * 计算当前阶段进度百分比（0..100）：
     * - 课前阶段：从课前提醒触发时刻（`remindAtMs`）推进到上课时刻（`startMs`）；
     * - 课中阶段：从上课时刻（`startMs`）推进到下课时刻（`endMs`）。
     */
    fun computeProgressPercent(
        remindAtMs: Long,
        startMs: Long,
        endMs: Long,
        nowMs: Long,
        defaultLeadMinutes: Int = Repository.DEFAULT_REMIND_MINUTES,
    ): Int {
        return if (nowMs < startMs) {
            val leadStart = if (remindAtMs in 1 until startMs) {
                remindAtMs
            } else {
                startMs - defaultLeadMinutes.coerceAtLeast(1) * 60_000L
            }
            val span = (startMs - leadStart).coerceAtLeast(60_000L)
            (((nowMs - leadStart).coerceAtLeast(0L) * 100L) / span).toInt().coerceIn(0, 100)
        } else {
            val span = (endMs - startMs).coerceAtLeast(60_000L)
            (((nowMs - startMs).coerceAtLeast(0L) * 100L) / span).toInt().coerceIn(0, 100)
        }
    }

    /**
     * 构建小米澎湃 OS（HyperOS 1 / 2 / 3）焦点通知与超级岛 `miui.focus.param` JSON 字符串。
     * 包含 `param_v2` 下的 `baseInfo`（通知中心/锁屏焦点卡）与 `param_island`（超级岛大小岛结构）。
     */
    fun buildMiuiFocusParamJson(
        course: String,
        room: String,
        timeRange: String,
        meta: String,
        status: String,
        shortText: String,
        progress: Int,
        showProgress: Boolean,
        isDismiss: Boolean = false,
    ): String {
        val coercedProgress = progress.coerceIn(0, 100)
        val baseInfo = JSONObject()
            .put("type", 1)
            .put("title", course)
            .put("content", meta)
            .put("subContent", status)
            .put("color", BRAND_COLOR_HEX)

        val smallIslandArea = JSONObject()
            .put(
                "picInfo",
                JSONObject()
                    .put("type", 1)
                    .put("pic", "miui.focus.pic_ticker"),
            )
            .put(
                "textInfo",
                JSONObject()
                    .put("title", shortText),
            )

        val leftArea = JSONObject()
            .put("type", 1)
            .put(
                "picInfo",
                JSONObject()
                    .put("type", 1)
                    .put("pic", "miui.focus.pic_app"),
            )
            .put(
                "textInfo",
                JSONObject()
                    .put("title", course)
                    .put("content", room)
                    .put("narrowFont", false),
            )

        val rightArea = JSONObject()
            .put("type", 2)
            .put(
                "textInfo",
                JSONObject()
                    .put("title", shortText)
                    .put("content", timeRange),
            )

        val bigIslandArea = JSONObject()
            .put("imageTextInfoLeft", leftArea)
            .put("imageTextInfoRight", rightArea)

        val paramIsland = JSONObject()
            .put("islandProperty", 1)
            .put("islandOrder", true)
            .put("dismissIsland", isDismiss)
            .put("smallIslandArea", smallIslandArea)
            .put("bigIslandArea", bigIslandArea)

        if (showProgress) {
            val progressObj = JSONObject()
                .put("progress", coercedProgress)
                .put("colorReach", BRAND_COLOR_HEX)
                .put("colorUnreach", "#334B63E0")
            paramIsland.put("progressInfo", progressObj)
        }

        val paramV2 = JSONObject()
            .put("protocol", 1)
            .put("business", "schedule")
            .put("enableFloat", false)
            .put("updatable", true)
            .put("ticker", "$course · $shortText")
            .put("tickerPic", "miui.focus.pic_ticker")
            .put("aodTitle", "$course $shortText")
            .put("aodPic", "miui.focus.pic_ticker")
            .put("baseInfo", baseInfo)
            .put("param_island", paramIsland)

        return JSONObject().put("param_v2", paramV2).toString()
    }

    /**
     * 按当前设备所属 ROM 家族向 [Notification.Builder] 注入对应的灵动岛 / 胶囊 / 实况窗扩展字段。
     */
    fun applyOemExtras(
        context: Context,
        builder: Notification.Builder,
        item: Reminders.Item,
        course: String,
        meta: String,
        status: String,
        shortText: String,
        progress: Int,
        showProgress: Boolean,
        isUpdate: Boolean,
        openIntent: PendingIntent,
        family: RomFamily = detectRomFamily(),
    ) {
        val op = if (isUpdate) 1 else 0
        val smallIcon = Icon.createWithResource(context, R.drawable.ic_notify_calendar)
        val appIcon = Icon.createWithBitmap(Reminders.appIconBitmap(context))
        val room = item.room.ifBlank { "地点待定" }
        val timeRange = "${item.start}–${item.end}"

        when (family) {
            RomFamily.HYPER_OS -> {
                val json = buildMiuiFocusParamJson(
                    course = course,
                    room = room,
                    timeRange = timeRange,
                    meta = meta,
                    status = status,
                    shortText = shortText,
                    progress = progress,
                    showProgress = showProgress,
                )
                val pics = Bundle().apply {
                    putParcelable("miui.focus.pic_ticker", smallIcon)
                    putParcelable("miui.focus.pic_app", appIcon)
                }
                val extras = Bundle().apply {
                    putString("miui.focus.param", json)
                    putBundle("miui.focus.pics", pics)
                }
                builder.addExtras(extras)
            }

            RomFamily.FLYME, RomFamily.HARMONY_OS, RomFamily.MAGIC_OS -> {
                val capsule = Bundle().apply {
                    putInt("notification.live.capsuleStatus", 1)
                    putInt("notification.live.capsuleType", 1)
                    putParcelable("notification.live.capsuleIcon", smallIcon)
                    putInt("notification.live.capsuleBgColor", BRAND_COLOR_INT)
                    putString("notification.live.capsuleTitle", shortText)
                    putString("notification.live.capsuleContent", course)
                    putInt("notification.live.capsuleContentColor", 0xFFFFFFFF.toInt())
                    putBoolean("notification.live.capsuleRemind", !isUpdate)
                }
                val feature = Bundle().apply {
                    putCharSequence("notification.live.feature.title", course)
                    putCharSequence("notification.live.feature.content", meta)
                    putCharSequence("notification.live.feature.subText", status)
                    if (showProgress) {
                        putInt("notification.live.feature.progress", progress.coerceIn(0, 100))
                    }
                }
                val liveExtras = Bundle().apply {
                    putInt("notification.live.operation", op)
                    putInt("notification.live.type", if (showProgress) 3 else 1)
                    putString("notification.live.event", "SCHEDULE")
                    putCharSequence("notification.live.titleOverlay", course)
                    putBundle("notification.live.capsule", capsule)
                    putBundle("notification.live.feature", feature)
                }
                builder.addExtras(liveExtras)
            }

            RomFamily.ORIGIN_OS -> {
                val baseInfos = Bundle().apply {
                    putParcelable("notification.superx.baseInfos.icon", appIcon)
                    putCharSequence("notification.superx.baseInfos.title", course)
                    putCharSequence("notification.superx.baseInfos.content", meta)
                    putCharSequence("notification.superx.baseInfos.subInfo", status)
                }
                val capsule = Bundle().apply {
                    putInt("notification.superx.capsule.status", 1)
                    putParcelable("notification.superx.capsule.icon", smallIcon)
                    putCharSequence("notification.superx.capsule.title", shortText)
                    putCharSequence("notification.superx.capsule.content", course)
                    putString("notification.superx.capsule.bgColor", BRAND_COLOR_HEX)
                }
                val superxExtras = Bundle().apply {
                    putInt("notification.superx.operation", op)
                    putBoolean("notification.superx.showNotify", true)
                    putInt("notification.superx.template", if (showProgress) 3 else 1)
                    putString("notification.superx.scene", "SCHEDULE")
                    putParcelable("notification.superx.clickResp", openIntent)
                    putBundle("notification.superx.baseInfos", baseInfos)
                    putBundle("notification.superx.capsule", capsule)
                    if (showProgress) {
                        putInt("notification.superx.progress", progress.coerceIn(0, 100))
                    }
                }
                builder.addExtras(superxExtras)
            }

            RomFamily.COLOR_OS -> {
                if (Build.VERSION.SDK_INT < 36) {
                    val fluidExtras = Bundle().apply {
                        putInt("actionStatus", op)
                        putString("entityName", "SCHEDULE")
                        putString("capsuleTitle", shortText)
                        putString("capsuleContent", course)
                    }
                    builder.addExtras(fluidExtras)
                }
            }

            RomFamily.ONE_UI, RomFamily.ZUI, RomFamily.OTHER -> Unit
        }
    }
}
