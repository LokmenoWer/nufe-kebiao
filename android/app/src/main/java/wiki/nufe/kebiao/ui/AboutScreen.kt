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
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
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
import androidx.compose.ui.res.stringResource
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
    updateFlow: UpdateFlow,
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
    var showPrivacy by remember { mutableStateOf(false) }
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
            Text(
                stringResource(R.string.app_tagline),
                fontSize = 14.sp,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(12.dp))
            Text(
                "面向南京财经大学学生的非官方课表工具：在本机登录学校统一身份认证，直接从教务系统导入课表，" +
                    "提供离线日课表、周课表、桌面小组件与课前实时提醒。",
                fontSize = 13.sp,
                lineHeight = 20.sp,
            )
            Spacer(Modifier.height(10.dp))
            Text("与南京财经大学无隶属关系。", fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }

        Spacer(Modifier.height(14.dp))
        SectionCard {
            SectionTitle("产品信息")
            AboutRow("官网介绍与下载", SITE_URL) {
                context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(SITE_URL)))
            }
            AboutRow("GitHub 仓库", "LokmenoWer/nufe-kebiao") {
                context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(REPO_URL)))
            }
            AboutRow("当前版本", version.ifBlank { currentVersion })
            AboutRow(
                "检查更新",
                when {
                    checking -> "检查中…"
                    update == null -> checkResult ?: "点按检查"
                    else -> when (val step = updateFlow.step) {
                        UpdateStep.Idle -> "更新到 ${update.version}"
                        is UpdateStep.Downloading -> step.percent?.let { "下载中 $it%" } ?: "下载中…"
                        UpdateStep.NeedPermission -> "去允许安装"
                        is UpdateStep.Failed -> "更新失败，用浏览器下载"
                    }
                },
            ) {
                if (update != null) {
                    when (updateFlow.step) {
                        UpdateStep.Idle -> updateFlow.start(update)
                        is UpdateStep.Downloading -> Unit
                        UpdateStep.NeedPermission -> updateFlow.openPermissionSettings()
                        is UpdateStep.Failed -> updateFlow.openInBrowser(update)
                    }
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
        }

        Spacer(Modifier.height(14.dp))
        SectionCard {
            SectionTitle("其他信息")
            AboutRow("开源许可证", "GPL-3.0-or-later")
            AboutRow("第三方组件与图标", "IconPark · Apache-2.0") {
                context.startActivity(
                    Intent(Intent.ACTION_VIEW, Uri.parse("https://github.com/bytedance/IconPark")),
                )
            }
            AboutRow("隐私与数据说明", "数据来自学校教务系统") {
                showPrivacy = true
            }
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

    if (showPrivacy) {
        AlertDialog(
            onDismissRequest = { showPrivacy = false },
            title = { Text("隐私与数据说明") },
            text = {
                Text(
                    "App 直接访问南京财经大学统一认证与教务系统。学号、密码、登录会话及个人课表不上传至项目服务器；学校按其规则处理登录与验证。\n\n" +
                        "App 不保存密码。登录会话与姓名、学号使用本机密钥加密保存；课表保存在 App 私有目录，供离线查看、桌面组件和本地提醒使用。会话过期不删除课表，需要你在 App 内重新登录。\n\n" +
                        "“我的 → 清除本机数据”会删除本机课表、个人资料与登录态，并停止课程提醒。升级遗留的课表仅在本机展示，不会自动访问旧服务；本机清除不代表删除此前已存于旧服务的数据。\n\n" +
                        "官网仅用于产品介绍与下载，更新检查不携带学校凭据。App 每天向项目服务器发送一次匿名统计：随机生成的安装编号（服务器只存其哈希）、App 与系统版本、是否已导入课表和是否开启协助刷新，不含学号、姓名或课表。\n\n" +
                        "“服务器托管”默认关闭。仅当你在导入课表时勾选，App 才把本次学校登录状态加密交给项目服务器，由服务器在本机刷新失败时继续更新课表；可随时在“我的”中取消，服务器会删除这份登录状态并撤销设备。\n\n" +
                        "“协助刷新”默认关闭。开启后，手机仅在 Wi-Fi 下为项目服务器转发发往教务系统的加密数据；加密在服务器与学校之间端到端完成，本机无法查看或修改其中任何人的信息。\n\n" +
                        "App 不读取短信、通讯录、相册或位置信息。",
                    modifier = Modifier.verticalScroll(rememberScrollState()),
                    fontSize = 13.sp,
                    lineHeight = 20.sp,
                )
            },
            confirmButton = { TextButton(onClick = { showPrivacy = false }) { Text("知道了") } },
        )
    }
}

@Composable
private fun SectionTitle(text: String) {
    Text(text, fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
    Spacer(Modifier.height(2.dp))
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
