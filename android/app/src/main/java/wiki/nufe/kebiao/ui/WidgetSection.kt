package wiki.nufe.kebiao.ui

import android.appwidget.AppWidgetManager
import android.content.ComponentName
import android.os.SystemClock
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import wiki.nufe.kebiao.R
import wiki.nufe.kebiao.data.State
import wiki.nufe.kebiao.widget.TodayReceiver
import wiki.nufe.kebiao.widget.WeekReceiver

@Composable
internal fun WidgetSection() {
    val context = LocalContext.current
    val manager = remember { AppWidgetManager.getInstance(context) }
    val scope = rememberCoroutineScope()
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    var showPicker by remember { mutableStateOf(false) }
    var lastPinAt by remember { mutableLongStateOf(0L) }
    var showGuide by remember { mutableStateOf(false) }

    fun pin(receiver: Class<*>) {
        val now = SystemClock.elapsedRealtime()
        if (now - lastPinAt < PIN_COOLDOWN_MS) return
        lastPinAt = now
        val provider = ComponentName(context, receiver)
        val before = manager.getAppWidgetIds(provider).size
        if (manager.requestPinAppWidget(provider, null, null)) {
            scope.launch {
                delay(PIN_CHECK_DELAY_MS)
                lifecycle.currentStateFlow.first { it.isAtLeast(Lifecycle.State.RESUMED) }
                delay(PIN_CHECK_DELAY_MS)
                if (manager.getAppWidgetIds(provider).size <= before) showGuide = true
            }
        } else {
            showGuide = true
        }
    }

    SectionCard {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .clickable { showPicker = true },
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                "添加课表小组件到桌面",
                fontSize = 16.sp,
                fontWeight = FontWeight.SemiBold,
                modifier = Modifier.weight(1f),
            )
            Icon(
                painter = painterResource(R.drawable.ic_chevron_right),
                contentDescription = null,
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.size(18.dp),
            )
        }
    }

    if (showPicker) {
        WidgetPickerDialog(
            onDismiss = { showPicker = false },
            onPick = { receiver ->
                showPicker = false
                pin(receiver)
            },
        )
    }

    if (showGuide) {
        AlertDialog(
            onDismissRequest = { showGuide = false },
            title = { Text("没加到桌面？") },
            text = { Text("有的桌面（比如 OPPO、一加、vivo、华为）不让 App 直接添加小组件。请手动添加：$MANUAL_ADD") },
            confirmButton = { TextButton(onClick = { showGuide = false }) { Text("知道了") } },
        )
    }
}

@Composable
private fun WidgetPickerDialog(onDismiss: () -> Unit, onPick: (Class<*>) -> Unit) {
    PopupCard(onDismissRequest = onDismiss) {
        Text("添加课表小组件", fontSize = 18.sp, fontWeight = FontWeight.SemiBold)
        Spacer(Modifier.height(4.dp))
        Hint("选择一种放到桌面。")
        Spacer(Modifier.height(10.dp))
        WidgetOption("今天", "正在上课 / 下一节，课后自动切到明天", TodayReceiver::class.java, onPick)
        HorizontalDivider(
            modifier = Modifier.padding(vertical = 4.dp),
            color = MaterialTheme.colorScheme.outline.copy(alpha = 0.7f),
        )
        WidgetOption("本周", "按节次排，写准确起止时间", WeekReceiver::class.java, onPick)
        Spacer(Modifier.height(4.dp))
        TextButton(
            onClick = onDismiss,
            contentPadding = PaddingValues(horizontal = 8.dp),
            modifier = Modifier.align(Alignment.End),
        ) { Text("取消", fontSize = 12.sp) }
    }
}

@Composable
private fun WidgetOption(
    title: String,
    description: String,
    receiver: Class<*>,
    onPin: (Class<*>) -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable { onPin(receiver) }
            .padding(vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(title, fontSize = 14.sp, fontWeight = FontWeight.Medium)
            Spacer(Modifier.height(2.dp))
            Text(description, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Text("添加", fontSize = 12.sp, color = MaterialTheme.colorScheme.primary)
    }
}
