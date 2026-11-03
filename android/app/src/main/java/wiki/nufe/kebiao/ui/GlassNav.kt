package wiki.nufe.kebiao.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInHorizontally
import androidx.compose.animation.slideOutHorizontally
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.blur
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.launch
import wiki.nufe.kebiao.R

@Composable
internal fun GlassNav(selected: Tab, onSelect: (Tab) -> Unit, modifier: Modifier = Modifier) {
    val dark = isSystemInDarkTheme()
    val glass = if (dark) Color(0xFF1B1B1F).copy(alpha = 0.72f) else Color.White.copy(alpha = 0.72f)
    val halo = if (dark) Color(0xFF2A355C) else Color(0xFFE3EAFF)
    val border = Brush.linearGradient(
        listOf(
            Color.White.copy(alpha = 0.9f),
            Color.White.copy(alpha = 0.12f),
            Color.White.copy(alpha = 0.65f),
        ),
    )
    val scope = rememberCoroutineScope()
    val view = LocalView.current
    val scale = remember { Animatable(1f) }
    var direction by remember { mutableIntStateOf(1) }

    fun select(item: Tab) {
        view.performHapticFeedback(android.view.HapticFeedbackConstants.VIRTUAL_KEY)
        if (item != selected) {
            direction = if (item.ordinal > selected.ordinal) 1 else -1
        }
        scope.launch {
            scale.snapTo(1f)
            scale.animateTo(1.04f, tween(80))
            scale.animateTo(1f, spring(dampingRatio = 0.55f, stiffness = 420f))
        }
        onSelect(item)
    }

    Box(
        modifier
            .padding(horizontal = 24.dp)
            .navigationBarsPadding()
            .padding(bottom = 14.dp)
            .graphicsLayer {
                scaleX = scale.value
                scaleY = scale.value
            },
    ) {
        Box(
            Modifier
                .matchParentSize()
                .blur(24.dp)
                .background(
                    Brush.linearGradient(listOf(halo.copy(alpha = 0.9f), halo.copy(alpha = 0.15f))),
                    RoundedCornerShape(50),
                ),
        )
        Surface(
            modifier = Modifier
                .fillMaxWidth()
                .border(1.dp, border, RoundedCornerShape(50)),
            shape = RoundedCornerShape(50),
            color = glass,
            shadowElevation = 18.dp,
        ) {
            Row(
                modifier = Modifier.fillMaxWidth().padding(6.dp),
                horizontalArrangement = Arrangement.SpaceEvenly,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Tab.entries.forEach { item ->
                    NavItem(item, item == selected, direction) { select(item) }
                }
            }
        }
    }
}

@Composable
private fun RowScope.NavItem(tab: Tab, selected: Boolean, direction: Int, onClick: () -> Unit) {
    val color = if (selected) MaterialTheme.colorScheme.onBackground else MaterialTheme.colorScheme.onSurfaceVariant
    Column(
        modifier = Modifier
            .weight(1f)
            .clip(RoundedCornerShape(50))
            .background(
                if (selected) MaterialTheme.colorScheme.onBackground.copy(alpha = 0.08f) else Color.Transparent,
            )
            .clickable(onClick = onClick)
            .padding(vertical = 7.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            NavIcon(tab, color)
            Spacer(Modifier.width(6.dp))
            Text(
                tab.label,
                fontSize = 12.sp,
                fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
                color = color,
            )
        }
        Spacer(Modifier.height(3.dp))
        Box(Modifier.height(2.dp).width(18.dp)) {
            NavIndicator(selected, direction)
        }
    }
}

@Composable
private fun NavIndicator(visible: Boolean, direction: Int) {
    AnimatedVisibility(
        visible = visible,
        enter = fadeIn(tween(160)) +
            slideInHorizontally(tween(200)) { if (direction > 0) -it else it },
        exit = fadeOut(tween(120)) +
            slideOutHorizontally(tween(160)) { if (direction > 0) it else -it },
    ) {
        Box(
            Modifier
                .fillMaxSize()
                .background(MaterialTheme.colorScheme.onBackground, RoundedCornerShape(50)),
        )
    }
}

@Composable
private fun NavIcon(tab: Tab, color: Color) {
    val icon = when (tab) {
        Tab.Timetable -> R.drawable.ic_nav_calendar
        Tab.Mine -> R.drawable.ic_nav_user
        Tab.About -> R.drawable.ic_nav_info
    }
    Icon(
        painter = painterResource(icon),
        contentDescription = null,
        tint = color,
        modifier = Modifier.size(20.dp),
    )
}
