package wiki.nufe.kebiao.ui

import android.content.Intent
import android.content.pm.ApplicationInfo
import android.net.Uri
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
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
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.launch
import wiki.nufe.kebiao.R
import wiki.nufe.kebiao.data.AppUpdate

@Composable
internal fun AboutScreen(
    currentVersion: String,
    update: AppUpdate?,
    onCheckUpdate: suspend () -> AppUpdate?,
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val version = remember {
        runCatching {
            context.packageManager.getPackageInfo(context.packageName, 0).versionName
        }.getOrNull().orEmpty()
    }
    var checkResult by remember { mutableStateOf<String?>(null) }
    var checking by remember { mutableStateOf(false) }
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
            AboutRow("版本", version.ifBlank { currentVersion })
            AboutRow(
                "检查更新",
                when {
                    checking -> "检查中…"
                    update != null -> "新版本 ${update.version}"
                    else -> checkResult ?: "点按检查"
                },
            ) {
                if (update != null) {
                    context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(update.url)))
                } else if (!checking) {
                    checking = true
                    scope.launch {
                        checkResult = try {
                            if (onCheckUpdate() == null) "已是最新" else null
                        } catch (_: Exception) {
                            "检查失败，稍后再试"
                        }
                        checking = false
                    }
                }
            }
            AboutRow("图标", "IconPark · Apache-2.0") {
                context.startActivity(
                    Intent(Intent.ACTION_VIEW, Uri.parse("https://github.com/bytedance/IconPark")),
                )
            }
            AboutRow("数据来源", "/mobile/<token>.json")
            if ((context.applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE) != 0) {
                AboutRow("开发者选项", "实时提醒测试") {
                    context.startActivity(
                        Intent().setClassName(context, "wiki.nufe.kebiao.preview.LiveUpdateTestActivity"),
                    )
                }
            }
        }
        Spacer(Modifier.height(110.dp))
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
