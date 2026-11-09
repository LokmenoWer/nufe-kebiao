package wiki.nufe.kebiao.ui

import android.app.Notification
import android.app.NotificationManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.PowerManager
import android.provider.Settings
import android.widget.Toast
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.TransformOrigin
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import kotlinx.coroutines.delay

@Composable
internal fun EnterUp(delayMillis: Int = 0, content: @Composable () -> Unit) {
    val progress = remember { Animatable(0f) }
    LaunchedEffect(Unit) {
        delay(delayMillis.toLong())
        progress.animateTo(1f, tween(380, easing = FastOutSlowInEasing))
    }
    Box(
        modifier = Modifier.graphicsLayer {
            alpha = progress.value
            translationY = (1f - progress.value) * 20.dp.toPx()
        },
    ) { content() }
}

@Composable
internal fun PopupCard(
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
            color = MaterialTheme.colorScheme.surface,
            contentColor = MaterialTheme.colorScheme.onSurface,
            border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.75f)),
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
internal fun SectionCard(content: @Composable ColumnScope.() -> Unit) {
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
internal fun Hint(text: String) {
    Text(
        text,
        fontSize = 12.sp,
        lineHeight = 18.sp,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

internal enum class RomFamily(
    val displayName: String,
    val needsAutoStart: Boolean,
    val autoStartStep: String,
    val batteryStep: String,
    val batteryToast: String,
) {
    COLOR_OS(
        displayName = "ColorOS",
        needsAutoStart = true,
        autoStartStep = "打开系统设置 → 应用 → 自启动，勾选本 app",
        batteryStep = "在耗电管理中设为「完全允许后台行为」",
        batteryToast = "请在「耗电管理」中设为完全允许后台行为，并开启「自启动」",
    ),
    HYPER_OS(
        displayName = "HyperOS / MIUI",
        needsAutoStart = true,
        autoStartStep = "打开应用设置 → 权限管理 → 开启「后台自启动」",
        batteryStep = "在电量消耗 / 省电策略中设为「无限制」",
        batteryToast = "请点击「电量消耗 / 省电策略」设为无限制，并在权限中开启「自启动」",
    ),
    ORIGIN_OS(
        displayName = "OriginOS",
        needsAutoStart = true,
        autoStartStep = "打开应用权限 → 权限 → 开启「自启动 / 关联启动」",
        batteryStep = "在系统耗电管理中开启「允许后台高耗电」",
        batteryToast = "请在权限中开启「自启动」，并在耗电管理中开启「允许后台高耗电」",
    ),
    MAGIC_OS(
        displayName = "MagicOS",
        needsAutoStart = true,
        autoStartStep = "打开系统管家 → 应用启动管理 → 改为手动管理并允许自启动",
        batteryStep = "在应用启动管理中开启「允许自启动、关联启动、后台活动」",
        batteryToast = "请在「应用启动管理」中关闭自动管理，开启自启动与后台活动",
    ),
    HARMONY_OS(
        displayName = "HarmonyOS / EMUI",
        needsAutoStart = true,
        autoStartStep = "打开系统设置 → 应用和服务 → 应用启动管理 → 手动允许自启动",
        batteryStep = "在应用启动管理中开启「允许自启动、关联启动、后台活动」",
        batteryToast = "请在「应用启动管理」中关闭自动管理，开启自启动与后台活动",
    ),
    FLYME(
        displayName = "Flyme",
        needsAutoStart = true,
        autoStartStep = "打开手机管家 → 权限管理 → 后台管理 → 允许后台运行",
        batteryStep = "在后台管理中设为「允许后台运行」",
        batteryToast = "请在权限与后台管理中允许本应用自启动及后台运行",
    ),
    ZUI(
        displayName = "ZUI",
        needsAutoStart = true,
        autoStartStep = "打开安全中心 → 自启管理 → 允许本应用自启动",
        batteryStep = "在电池优化与耗电保护中设为「无限制」",
        batteryToast = "请在应用设置中允许自启动并关闭电池优化限制",
    ),
    ONE_UI(
        displayName = "One UI",
        needsAutoStart = false,
        autoStartStep = "打开系统设置 → 应用程序 → 选择本应用 → 电池 → 设为「不受限制」",
        batteryStep = "在应用电池设置中设为「不受限制」，避免深度休眠推迟提醒",
        batteryToast = "请在应用电池设置中将本应用设为「不受限制」",
    ),
    OTHER(
        displayName = "Android",
        needsAutoStart = false,
        autoStartStep = "打开系统设置 → 应用 → 允许自启动",
        batteryStep = "在耗电管理或后台行为中，设置为完全允许后台行为",
        batteryToast = "请在电池设置中将本应用设为无限制",
    ),
}

/**
 * 灵动显示（系统状态栏胶囊 / 灵动岛 / 实时卡片）支持层级：
 * - [NATIVE_PROMOTED]：Android 16+（API 36+）原生 Live Updates（ColorOS 16 流体云、OriginOS 6 原子岛、
 *   MagicOS 10 灵动胶囊、HyperOS 3 超级岛/焦点通知、One UI 8 Now Bar、Flyme AIOS 2、Pixel/AOSP 等）。
 * - [FLYME_CAPSULE]：魅族 Flyme 11+（Android 13+）实况通知胶囊，声明 `flyme.permission.LIVE_NOTIFICATION`
 *   并通过 `notification.live.capsule` Bundle 驱动，无需企业白名单。
 * - [OEM_EXTRAS]：Android 8.0+ 国产定制 ROM（HyperOS/MIUI、OriginOS、MagicOS、HarmonyOS、ColorOS 15 等），
 *   通过各自 `Notification.extras` 扩展协议注入胶囊与卡片参数；受厂商白名单限制的机型会自动降级为常驻倒计时通知。
 * - [UNSUPPORTED]：其他不支持的系统。
 */
internal enum class LiveUpdateTier {
    NATIVE_PROMOTED,
    FLYME_CAPSULE,
    OEM_EXTRAS,
    UNSUPPORTED,
}

internal fun systemProperty(key: String): String = runCatching {
    val cls = Class.forName("android.os.SystemProperties")
    cls.getMethod("get", String::class.java, String::class.java).invoke(null, key, "") as? String
}.getOrNull().orEmpty().trim()

internal fun detectRomFamily(
    manufacturer: String = Build.MANUFACTURER.orEmpty(),
    brand: String = Build.BRAND.orEmpty(),
    propLookup: (String) -> String = ::systemProperty,
): RomFamily {
    val id = "$manufacturer $brand".lowercase()
    return when {
        listOf("oppo", "oneplus", "realme").any { it in id } ||
            propLookup("ro.build.version.opporom").isNotBlank() ||
            propLookup("ro.build.version.oplusrom").isNotBlank() -> RomFamily.COLOR_OS
        listOf("xiaomi", "redmi", "poco", "blackshark").any { it in id } ||
            propLookup("ro.mi.os.version.name").isNotBlank() ||
            propLookup("ro.miui.ui.version.name").isNotBlank() -> RomFamily.HYPER_OS
        listOf("vivo", "iqoo").any { it in id } ||
            propLookup("ro.vivo.os.version").isNotBlank() -> RomFamily.ORIGIN_OS
        "honor" in id ||
            propLookup("ro.honor.build.version.magic").isNotBlank() -> RomFamily.MAGIC_OS
        "huawei" in id ||
            propLookup("hw_sc.build.platform.version").isNotBlank() ||
            propLookup("ro.build.version.emui").isNotBlank() -> RomFamily.HARMONY_OS
        "meizu" in id ||
            propLookup("ro.build.display.id").lowercase().contains("flyme") -> RomFamily.FLYME
        listOf("lenovo", "zuk").any { it in id } ||
            propLookup("ro.com.zui.version").isNotBlank() ||
            propLookup("ro.build.version.zui").isNotBlank() ||
            propLookup("ro.com.lenovo.region").isNotBlank() -> RomFamily.ZUI
        "samsung" in id ||
            propLookup("ro.build.version.oneui").isNotBlank() -> RomFamily.ONE_UI
        else -> RomFamily.OTHER
    }
}

internal fun romSummary(
    manufacturer: String = Build.MANUFACTURER.orEmpty(),
    brand: String = Build.BRAND.orEmpty(),
    propLookup: (String) -> String = ::systemProperty,
): String {
    val family = detectRomFamily(manufacturer, brand, propLookup)
    val ver = listOf(
        "ro.mi.os.version.name",
        "ro.miui.ui.version.name",
        "ro.build.version.oplusrom",
        "ro.build.version.opporom",
        "ro.vivo.os.version",
        "ro.honor.build.version.magic",
        "hw_sc.build.platform.version",
        "ro.build.version.emui",
        "ro.com.zui.version",
        "ro.build.version.zui",
        "ro.build.version.oneui",
    ).firstNotNullOfOrNull { key -> propLookup(key).takeIf { it.isNotBlank() } }
        ?: Build.DISPLAY.orEmpty()
    return if (ver.isNotBlank()) "${family.displayName} ($ver)" else family.displayName
}

internal fun liveUpdateName(family: RomFamily = detectRomFamily()): String = when (family) {
    RomFamily.COLOR_OS -> "流体云"
    RomFamily.HYPER_OS -> "超级岛 / 焦点通知"
    RomFamily.ORIGIN_OS -> "原子岛"
    RomFamily.MAGIC_OS -> "灵动胶囊"
    RomFamily.HARMONY_OS -> "实况窗"
    RomFamily.FLYME -> "实况通知"
    RomFamily.ONE_UI -> "实时通知"
    RomFamily.ZUI, RomFamily.OTHER -> "实时活动"
}

internal fun isColorOsLike(): Boolean = detectRomFamily() == RomFamily.COLOR_OS

/** 检查当前系统框架是否完整实现了 Android 16 Promoted Ongoing Notifications 方法（防止部分魔改 API 36 ROM 缺失方法）。 */
internal fun hasPromotedNotificationApi(sdkInt: Int = Build.VERSION.SDK_INT): Boolean {
    if (sdkInt < 36) return false
    return runCatching {
        NotificationManager::class.java.getMethod("canPostPromotedNotifications")
        Notification.Builder::class.java.getMethod(
            "setRequestPromotedOngoing",
            Boolean::class.javaPrimitiveType,
        )
        true
    }.getOrDefault(false)
}

internal fun detectLiveUpdateTier(
    family: RomFamily = detectRomFamily(),
    sdkInt: Int = Build.VERSION.SDK_INT,
    promotedApiAvailable: Boolean = hasPromotedNotificationApi(sdkInt),
): LiveUpdateTier = when {
    sdkInt >= 36 && promotedApiAvailable -> LiveUpdateTier.NATIVE_PROMOTED
    family == RomFamily.FLYME && sdkInt >= 33 -> LiveUpdateTier.FLYME_CAPSULE
    sdkInt >= 26 && family in setOf(
        RomFamily.COLOR_OS,
        RomFamily.HYPER_OS,
        RomFamily.ORIGIN_OS,
        RomFamily.MAGIC_OS,
        RomFamily.HARMONY_OS,
        RomFamily.FLYME,
    ) -> LiveUpdateTier.OEM_EXTRAS
    else -> LiveUpdateTier.UNSUPPORTED
}

internal fun isLiveUpdateSupported(): Boolean =
    detectLiveUpdateTier() != LiveUpdateTier.UNSUPPORTED

/** 安全调用 Android 16 `canPostPromotedNotifications()`，避免缺失该方法的定制 ROM 抛 NoSuchMethodError。 */
internal fun canPostPromotedNotificationsCompat(manager: NotificationManager?): Boolean {
    if (Build.VERSION.SDK_INT < 36 || manager == null) return false
    return runCatching { manager.canPostPromotedNotifications() }.getOrDefault(false)
}

/**
 * 检查“系统实时提醒”授权是否就绪：
 * - [LiveUpdateTier.NATIVE_PROMOTED] 需系统允许 `canPostPromotedNotifications`；
 * - [LiveUpdateTier.FLYME_CAPSULE] 与 [LiveUpdateTier.OEM_EXTRAS] 走清单权限或通知扩展，无需单独的 API 36 授权开关。
 */
internal fun isPromotedPermissionOk(context: Context): Boolean {
    if (detectLiveUpdateTier() != LiveUpdateTier.NATIVE_PROMOTED) return true
    val manager = context.getSystemService(NotificationManager::class.java)
    return canPostPromotedNotificationsCompat(manager)
}

internal fun needsAutoStartGuide(): Boolean = detectRomFamily().needsAutoStart

internal fun ignoringBatteryOptimizations(context: Context): Boolean =
    context.getSystemService(PowerManager::class.java)
        ?.isIgnoringBatteryOptimizations(context.packageName) == true

/**
 * 尝试打开国产 ROM 的“自启动 / 应用启动管理”页。
 * 部分高版本系统对安全中心子页面加了权限限制，失败时退回本应用的详情页并 Toast 提示操作路径。
 */
internal fun openAutoStartSettings(context: Context) {
    val pkg = context.packageName
    val family = detectRomFamily()
    val candidates = when (family) {
        RomFamily.COLOR_OS -> listOf(
            Intent("com.oplus.battery.permission.startup.StartupAppListActivity"),
            Intent("com.coloros.safecenter.permission.startup.StartupAppListActivity"),
            Intent("com.oppo.safe.permission.startup.StartupAppListActivity"),
            Intent().setComponent(ComponentName("com.oplus.battery", "com.oplus.startupapp.view.StartupAppListActivity")),
            Intent().setComponent(ComponentName("com.coloros.safecenter", "com.coloros.safecenter.permission.startup.StartupAppListActivity")),
            Intent().setComponent(ComponentName("com.oplus.safecenter", "com.oplus.safecenter.permission.startup.StartupAppListActivity")),
            Intent().setComponent(ComponentName("com.oppo.safe", "com.oppo.safe.permission.startup.StartupAppListActivity")),
        )
        RomFamily.HYPER_OS -> listOf(
            Intent("miui.intent.action.OP_AUTO_START").addCategory(Intent.CATEGORY_DEFAULT),
            Intent().setComponent(ComponentName("com.miui.securitycenter", "com.miui.permcenter.autostart.AutoStartManagementActivity")),
            Intent("miui.intent.action.APP_PERM_EDITOR").putExtra("extra_pkgname", pkg),
            Intent().setComponent(ComponentName("com.miui.securitycenter", "com.miui.permcenter.permissions.PermissionsEditorActivity"))
                .putExtra("extra_pkgname", pkg),
            Intent().setComponent(ComponentName("com.miui.securitycenter", "com.miui.permcenter.permissions.AppPermissionsEditorActivity"))
                .putExtra("extra_pkgname", pkg),
        )
        RomFamily.ORIGIN_OS -> listOf(
            Intent().setComponent(ComponentName("com.iqoo.secure", "com.iqoo.secure.ui.phoneoptimize.BgStartUpManager")),
            Intent().setComponent(ComponentName("com.iqoo.secure", "com.iqoo.secure.ui.phoneoptimize.AddWhiteListActivity")),
            Intent().setComponent(ComponentName("com.vivo.permissionmanager", "com.vivo.permissionmanager.activity.BgStartUpManagerActivity")),
            Intent().setComponent(ComponentName("com.iqoo.secure", "com.iqoo.secure.MainGuideActivity")),
        )
        RomFamily.MAGIC_OS -> listOf(
            Intent().setComponent(ComponentName("com.hihonor.systemmanager", "com.hihonor.systemmanager.startupmgr.ui.StartupNormalAppListActivity")),
            Intent().setComponent(ComponentName("com.hihonor.systemmanager", "com.hihonor.systemmanager.appcontrol.activity.StartupAppControlActivity")),
            Intent().setComponent(ComponentName("com.hihonor.systemmanager", "com.hihonor.systemmanager.optimize.process.ProtectActivity")),
        )
        RomFamily.HARMONY_OS -> listOf(
            Intent().setComponent(ComponentName("com.huawei.systemmanager", "com.huawei.systemmanager.startupmgr.ui.StartupNormalAppListActivity")),
            Intent().setComponent(ComponentName("com.huawei.systemmanager", "com.huawei.systemmanager.appcontrol.activity.StartupAppControlActivity")),
            Intent().setComponent(ComponentName("com.huawei.systemmanager", "com.huawei.systemmanager.optimize.process.ProtectActivity")),
        )
        RomFamily.FLYME -> listOf(
            Intent("com.meizu.safe.security.SHOW_APPSEC").putExtra("packageName", pkg),
            Intent().setComponent(ComponentName("com.meizu.safe", "com.meizu.safe.permission.SmartBGActivity")),
        )
        RomFamily.ZUI -> listOf(
            Intent().setComponent(ComponentName("com.zui.safecenter", "com.lenovo.safecenter.MainTab.LeSafeMainActivity")),
        )
        RomFamily.ONE_UI, RomFamily.OTHER -> emptyList()
    }
    for (intent in candidates) {
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        if (runCatching { context.startActivity(intent) }.isSuccess) return
    }
    Toast.makeText(context, family.autoStartStep, Toast.LENGTH_LONG).show()
    if (openAppDetailsSettings(context)) return
    runCatching {
        context.startActivity(Intent(Settings.ACTION_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }
}

/**
 * 国产 ROM 的电池白名单只解除 Android 原生 Doze 限制，厂商自己的“省电策略 / 耗电管理 / 启动管理”还会冻结 App。
 * 若尚未加入原生白名单且非强制打开厂商页，优先请求系统白名单；已加入白名单或跳转厂商页时直达对应 ROM 的耗电配置页。
 */
internal fun openBatteryOptimizationSettings(context: Context, preferOemPage: Boolean = false) {
    val pkg = context.packageName
    val family = detectRomFamily()
    if (!preferOemPage && !ignoringBatteryOptimizations(context)) {
        val direct = Intent(
            Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
            Uri.parse("package:$pkg"),
        ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        if (runCatching { context.startActivity(direct) }.isSuccess) return
    }
    val label = runCatching {
        context.applicationInfo.loadLabel(context.packageManager).toString()
    }.getOrDefault("nufe课表")
    val oemPages = when (family) {
        RomFamily.COLOR_OS -> listOf(
            Intent().setComponent(ComponentName("com.oplus.battery", "com.oplus.powermanager.fuelgaue.PowerConsumptionActivity")),
            Intent().setComponent(ComponentName("com.oplus.battery", "com.oplus.startupapp.view.StartupAppListActivity")),
            Intent().setComponent(ComponentName("com.coloros.safecenter", "com.coloros.safecenter.permission.startup.StartupAppListActivity")),
        )
        RomFamily.HYPER_OS -> listOf(
            Intent().setComponent(ComponentName("com.miui.powerkeeper", "com.miui.powerkeeper.ui.HiddenAppsConfigActivity"))
                .putExtra("package_name", pkg)
                .putExtra("package_label", label),
        )
        RomFamily.ORIGIN_OS -> listOf(
            Intent().setComponent(ComponentName("com.vivo.abe", "com.vivo.applicationbehaviorengine.ui.ExcessivePowerManagerActivity")),
            Intent().setComponent(ComponentName("com.iqoo.secure", "com.iqoo.secure.ui.phoneoptimize.BgStartUpManager")),
        )
        RomFamily.MAGIC_OS -> listOf(
            Intent().setComponent(ComponentName("com.hihonor.systemmanager", "com.hihonor.systemmanager.optimize.process.ProtectActivity")),
            Intent().setComponent(ComponentName("com.hihonor.systemmanager", "com.hihonor.systemmanager.startupmgr.ui.StartupNormalAppListActivity")),
        )
        RomFamily.HARMONY_OS -> listOf(
            Intent().setComponent(ComponentName("com.huawei.systemmanager", "com.huawei.systemmanager.optimize.process.ProtectActivity")),
            Intent().setComponent(ComponentName("com.huawei.systemmanager", "com.huawei.systemmanager.startupmgr.ui.StartupNormalAppListActivity")),
        )
        RomFamily.FLYME -> listOf(
            Intent().setComponent(ComponentName("com.meizu.safe", "com.meizu.safe.permission.SmartBGActivity")),
            Intent("com.meizu.safe.security.SHOW_APPSEC").putExtra("packageName", pkg),
        )
        RomFamily.ZUI, RomFamily.ONE_UI, RomFamily.OTHER -> emptyList()
    }
    for (intent in oemPages) {
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        if (runCatching { context.startActivity(intent) }.isSuccess) return
    }
    if (family.needsAutoStart) {
        Toast.makeText(context, family.batteryToast, Toast.LENGTH_LONG).show()
        if (openAppDetailsSettings(context)) return
    }
    val direct = Intent(
        Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
        Uri.parse("package:$pkg"),
    ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    if (runCatching { context.startActivity(direct) }.isSuccess) return
    if (runCatching {
            context.startActivity(
                Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            )
        }.isSuccess
    ) return
    openAppDetailsSettings(context)
}

internal fun openAppDetailsSettings(context: Context): Boolean {
    val details = Intent(
        Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
        Uri.parse("package:${context.packageName}"),
    ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    return runCatching { context.startActivity(details) }.isSuccess
}

internal fun openNotificationSettings(context: Context) {
    val intent = Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
        .putExtra(Settings.EXTRA_APP_PACKAGE, context.packageName)
        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    if (runCatching { context.startActivity(intent) }.isSuccess) return
    openAppDetailsSettings(context)
}

/**
 * 优先打开 Android 16+ 的“实时更新 / 流体云 / 实时通知”专属授权页，
 * 若系统未实现该页面则回退到本应用的标准通知设置页。
 */
internal fun openPromotedNotificationSettings(context: Context) {
    if (Build.VERSION.SDK_INT >= 36) {
        val promoted = Intent(Settings.ACTION_APP_NOTIFICATION_PROMOTION_SETTINGS)
            .putExtra(Settings.EXTRA_APP_PACKAGE, context.packageName)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        if (runCatching { context.startActivity(promoted) }.isSuccess) return
    }
    if (detectRomFamily() == RomFamily.FLYME) {
        val flymeSec = Intent("com.meizu.safe.security.SHOW_APPSEC")
            .putExtra("packageName", context.packageName)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        if (runCatching { context.startActivity(flymeSec) }.isSuccess) return
    }
    openNotificationSettings(context)
}

internal fun openExactAlarmSettings(context: Context) {
    if (Build.VERSION.SDK_INT >= 31) {
        val withPkg = Intent(
            Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM,
            Uri.parse("package:${context.packageName}"),
        ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        if (runCatching { context.startActivity(withPkg) }.isSuccess) return
        val generic = Intent(Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        if (runCatching { context.startActivity(generic) }.isSuccess) return
    }
    openAppDetailsSettings(context)
}

internal fun openExternalUrl(context: Context, url: String) {
    val intent = Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    runCatching { context.startActivity(intent) }
        .onFailure { Toast.makeText(context, "没有可用的浏览器应用", Toast.LENGTH_SHORT).show() }
}
