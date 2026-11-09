package wiki.nufe.kebiao.remind

import android.app.Notification
import android.app.PendingIntent
import android.content.Context
import android.graphics.drawable.Icon
import android.os.Build
import android.os.Bundle
import org.json.JSONObject
import wiki.nufe.kebiao.R
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
    val progressEnabled: Boolean = false,
    val chronometer: Boolean = false,
) {
    companion object {
        fun from(repo: Repository): LiveConfig = LiveConfig(
            dismissMode = repo.liveDismissMode,
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

    private val CAMPUS_PREFIX_REGEX = Regex("""^\S+?校区\s+""")
    private val WHITESPACE_REGEX = Regex("""\s+""")

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
     * 提取适合灵动卡片主标题展示的精简教室名称：
     * 去除冗余的“xx校区 ”前缀与内部空格（如 `"仙林校区 博远楼 JC406"` -> `"博远楼JC406"`，
     * `"仙林校区 博观楼 101"` -> `"博观楼101"`），确保“时间 + 教室”能在一行大号粗体中完整展示。
     */
    fun displayRoom(room: String): String {
        val trimmed = room.trim()
        if (trimmed.isEmpty()) return "地点待定"
        val withoutCampus = trimmed.replaceFirst(CAMPUS_PREFIX_REGEX, "").ifBlank { trimmed }
        return withoutCampus.replace(WHITESPACE_REGEX, "")
    }

    /**
     * 生成状态栏胶囊（`setShortCriticalText` 及各 ROM 胶囊摘要区）显示的简短文案。
     * 状态栏胶囊空间极窄（通常仅约 4 个汉字宽度），固定展示高可读性的剩余时间或上课状态。
     */
    fun formatCapsuleShortText(
        isBeforeClass: Boolean,
        remainMinutes: Long,
        dismissMode: LiveDismissMode = LiveDismissMode.FIVE_MIN_AFTER_START,
    ): String {
        val remain = remainMinutes.coerceAtLeast(1L)
        return if (isBeforeClass) {
            if (remain >= 100L) "剩${remain}分" else "剩 $remain 分钟"
        } else {
            if (dismissMode == LiveDismissMode.UNTIL_CLASS_END) {
                "剩 ${remain}分"
            } else {
                "正在上课"
            }
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
     * 包含 `param_v2` 下的 `baseInfo`（通知中心/锁屏焦点卡，主标题放大时间与教室，次行展示课程名）
     * 与 `param_island`（超级岛大小岛结构）。
     */
    fun buildMiuiFocusParamJson(
        titleLine: String,
        course: String,
        room: String,
        timeRange: String,
        status: String,
        shortText: String,
        progress: Int,
        showProgress: Boolean,
        isDismiss: Boolean = false,
    ): String {
        val coercedProgress = progress.coerceIn(0, 100)
        val baseInfo = JSONObject()
            .put("type", 1)
            .put("title", titleLine)
            .put("content", course)
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
                    .put("pic", "miui.focus.pic_ticker"),
            )
            .put(
                "textInfo",
                JSONObject()
                    .put("title", room)
                    .put("content", course)
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
            .put("ticker", "$titleLine · $shortText")
            .put("tickerPic", "miui.focus.pic_ticker")
            .put("aodTitle", "$titleLine $shortText")
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
        titleLine: String,
        course: String,
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
        val room = displayRoom(item.room)
        val timeRange = "${item.start}-${item.end}"

        when (family) {
            RomFamily.HYPER_OS -> {
                val json = buildMiuiFocusParamJson(
                    titleLine = titleLine,
                    course = course,
                    room = room,
                    timeRange = timeRange,
                    status = status,
                    shortText = shortText,
                    progress = progress,
                    showProgress = showProgress,
                )
                val pics = Bundle().apply {
                    putParcelable("miui.focus.pic_ticker", smallIcon)
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
                    putString("notification.live.capsuleContent", room)
                    putInt("notification.live.capsuleContentColor", 0xFFFFFFFF.toInt())
                    putBoolean("notification.live.capsuleRemind", !isUpdate)
                }
                val feature = Bundle().apply {
                    putCharSequence("notification.live.feature.title", titleLine)
                    putCharSequence("notification.live.feature.content", course)
                    putCharSequence("notification.live.feature.subText", status)
                    if (showProgress) {
                        putInt("notification.live.feature.progress", progress.coerceIn(0, 100))
                    }
                }
                val liveExtras = Bundle().apply {
                    putInt("notification.live.operation", op)
                    putInt("notification.live.type", if (showProgress) 3 else 1)
                    putString("notification.live.event", "SCHEDULE")
                    putCharSequence("notification.live.titleOverlay", titleLine)
                    putBundle("notification.live.capsule", capsule)
                    putBundle("notification.live.feature", feature)
                }
                builder.addExtras(liveExtras)
            }

            RomFamily.ORIGIN_OS -> {
                val baseInfos = Bundle().apply {
                    putCharSequence("notification.superx.baseInfos.title", titleLine)
                    putCharSequence("notification.superx.baseInfos.content", course)
                    putCharSequence("notification.superx.baseInfos.subInfo", status)
                }
                val capsule = Bundle().apply {
                    putInt("notification.superx.capsule.status", 1)
                    putParcelable("notification.superx.capsule.icon", smallIcon)
                    putCharSequence("notification.superx.capsule.title", shortText)
                    putCharSequence("notification.superx.capsule.content", room)
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
                        putString("capsuleContent", room)
                    }
                    builder.addExtras(fluidExtras)
                }
            }

            RomFamily.ONE_UI, RomFamily.ZUI, RomFamily.OTHER -> Unit
        }
    }
}
