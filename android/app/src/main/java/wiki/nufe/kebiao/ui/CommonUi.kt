package wiki.nufe.kebiao.ui

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.PowerManager
import android.provider.Settings
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

internal fun liveUpdateName(): String {
    val name = (Build.MANUFACTURER + " " + Build.BRAND).lowercase()
    return when {
        "oppo" in name || "oneplus" in name || "realme" in name -> "流体云"
        "xiaomi" in name || "redmi" in name || "poco" in name -> "实时动态"
        else -> "系统实时提醒"
    }
}

internal fun isColorOsLike(): Boolean {
    val id = "${Build.MANUFACTURER} ${Build.BRAND}".lowercase()
    return listOf("oppo", "oneplus", "realme").any { it in id }
}

internal fun ignoringBatteryOptimizations(context: Context): Boolean =
    context.getSystemService(PowerManager::class.java)
        ?.isIgnoringBatteryOptimizations(context.packageName) == true

/**
 * 尝试打开 ColorOS 的“自启动”页。这些页面多数需要系统权限（oplus.permission.OPLUS_COMPONENT_SAFE），
 * 第三方 App 通常拉不起来；失败时退回系统设置首页，由界面文案指引用户手动进入“应用 → 自启动”。
 */
internal fun openAutoStartSettings(context: Context) {
    val pages = listOf(
        ComponentName("com.oplus.battery", "com.oplus.startupapp.view.StartupAppListActivity"),
        ComponentName("com.coloros.safecenter", "com.coloros.safecenter.permission.startup.StartupAppListActivity"),
    )
    for (page in pages) {
        val intent = Intent().setComponent(page).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        if (runCatching { context.startActivity(intent) }.isSuccess) return
    }
    // 都失败时退回系统设置首页，而不是应用详情。
    runCatching {
        context.startActivity(Intent(Settings.ACTION_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }
}

/**
 * ColorOS 的电池白名单只解除 Android 的限制，系统自己的“耗电管理 / 自启动”还会冻结 App。
 * 优先打开厂商自己的后台页，入口不存在时再退回应用详情，最后才用标准电池弹窗。
 */
internal fun openBatteryOptimizationSettings(context: Context) {
    if (isColorOsLike()) {
        val oemPages = listOf(
            ComponentName("com.oplus.battery", "com.oplus.startupapp.view.StartupAppListActivity"),
            ComponentName("com.oplus.battery", "com.oplus.powermanager.fuelgaue.PowerConsumptionActivity"),
            ComponentName(
                "com.coloros.safecenter",
                "com.coloros.safecenter.permission.startup.StartupAppListActivity",
            ),
        )
        for (page in oemPages) {
            val intent = Intent().setComponent(page).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            if (runCatching { context.startActivity(intent) }.isSuccess) return
        }
        val details = Intent(
            Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
            Uri.parse("package:${context.packageName}"),
        ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        if (runCatching { context.startActivity(details) }.isSuccess) return
    }
    val direct = Intent(
        Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
        Uri.parse("package:${context.packageName}"),
    ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    if (runCatching { context.startActivity(direct) }.isSuccess) return
    runCatching {
        context.startActivity(
            Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
        )
    }
}
