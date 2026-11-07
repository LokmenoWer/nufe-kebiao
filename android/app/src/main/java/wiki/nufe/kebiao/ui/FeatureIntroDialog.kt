package wiki.nufe.kebiao.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** 首次安装并完成注册（导入课表）后展示一次，介绍 App 特性。 */
@Composable
internal fun FeatureIntroDialog(onDismiss: () -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("欢迎使用 nufe课表") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("为南财学子客制化的免费课表 App：", fontSize = 13.sp, lineHeight = 20.sp)
                Text("· 自动刷新同步教务系统的课表展示", fontSize = 13.sp, lineHeight = 20.sp)
                Text("· 上课前消息通知，桌面小组件随时查看", fontSize = 13.sp, lineHeight = 20.sp)
                Text("· 无广告、小而美的界面", fontSize = 13.sp, lineHeight = 20.sp)
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("开始使用") } },
    )
}
