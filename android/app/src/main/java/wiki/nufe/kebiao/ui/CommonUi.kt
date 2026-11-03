package wiki.nufe.kebiao.ui

import android.os.Build
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.border
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.TransformOrigin
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog

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
            color = Color.White,
            contentColor = Color(0xFF111111),
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
