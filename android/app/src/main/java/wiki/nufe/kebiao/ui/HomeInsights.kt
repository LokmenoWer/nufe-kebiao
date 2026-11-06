package wiki.nufe.kebiao.ui

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.LinearOutSlowInEasing
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import wiki.nufe.kebiao.data.DayPlan
import wiki.nufe.kebiao.data.Lesson
import wiki.nufe.kebiao.data.Now
import wiki.nufe.kebiao.widget.parseColor

private const val TOTAL_WEEKS = 17

@Composable
internal fun TodayRhythmCard(plan: DayPlan?, now: Now, clock12h: Boolean = true) {
    val lessons = plan?.day?.lessons.orEmpty()
    val tomorrow = plan?.title == "明天"
    val currentDay = plan?.day?.date == now.date

    SectionCard {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.Top) {
            Column(Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        if (tomorrow) "明日节奏" else "今日节奏",
                        fontSize = 14.sp,
                        fontWeight = FontWeight.SemiBold,
                    )
                    Spacer(Modifier.weight(1f))
                    Text(
                        "现在 ${clock(now.minutes)}",
                        fontSize = 9.sp,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                Spacer(Modifier.height(6.dp))
                Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
                    RhythmRing(
                        lessons = lessons,
                        now = now,
                        currentDay = currentDay,
                        tomorrow = tomorrow,
                        clock12h = clock12h,
                        modifier = Modifier.size(112.dp),
                    )
                }
            }
        }
    }
}

/** 环上的一段课：分钟数以当天 0 点为起点，12 小时制下可能为负（窗口跨到前一天）。 */
private data class RingSegment(val start: Int, val end: Int, val color: Color, val past: Boolean)

/**
 * 12 小时制像真实钟表：一圈 12 小时，只画一个 12 小时窗口里的课，避免上午 8 点和晚上 8 点叠在一起。
 * 今天的窗口是“现在往前 2 小时到往后 10 小时”，能覆盖正在上的课和到晚上的课；
 * 预告明天时没有“现在”，窗口从明天第一节课所在的整点开始。
 * 24 小时制一圈是整天，0 点在正上方。
 */
private fun ringSegments(lessons: List<Lesson>, now: Now, currentDay: Boolean, clock12h: Boolean): List<RingSegment> {
    val (windowStart, windowEnd) = when {
        !clock12h -> 0 to 24 * 60
        currentDay -> (now.minutes - 120).let { it to it + 12 * 60 }
        else -> ((lessons.minOfOrNull { it.startMinutes } ?: 8 * 60) / 60 * 60).let { it to it + 12 * 60 }
    }
    return lessons.mapNotNull { lesson ->
        val start = lesson.startMinutes.coerceIn(windowStart, windowEnd)
        val end = lesson.endMinutes.coerceIn(start, windowEnd)
        if (end <= start) return@mapNotNull null
        RingSegment(start, end, parseColor(lesson.color), past = currentDay && lesson.endMinutes <= now.minutes)
    }
}

@Composable
private fun RhythmRing(
    lessons: List<Lesson>,
    now: Now,
    currentDay: Boolean,
    tomorrow: Boolean,
    clock12h: Boolean,
    modifier: Modifier,
) {
    val handColor = MaterialTheme.colorScheme.onSurface
    // 底环跟随主题：写死黑色在暗色模式下几乎看不见。
    val trackColor = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.12f)
    val tickColor = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.6f)
    val period = if (clock12h) 12 * 60 else 24 * 60
    val segments = remember(lessons, now.date, now.minutes, currentDay, clock12h) {
        ringSegments(lessons, now, currentDay, clock12h)
    }
    val spin = remember { Animatable(0f) }
    LaunchedEffect(Unit) {
        spin.animateTo(1f, tween(durationMillis = 1100, easing = FastOutSlowInEasing))
    }
    Box(modifier, contentAlignment = Alignment.Center) {
        Canvas(Modifier.fillMaxSize()) {
            val stroke = 8.dp.toPx()
            val inset = stroke / 2
            val arcSize = Size(size.width - stroke, size.height - stroke)
            val center = Offset(size.width / 2, size.height / 2)
            fun angleOf(minutes: Int) = Math.floorMod(minutes, period) / period.toFloat() * 360f - 90f
            fun polar(degrees: Float, radius: Float): Offset {
                val rad = degrees * (Math.PI / 180).toFloat()
                return Offset(center.x + kotlin.math.cos(rad) * radius, center.y + kotlin.math.sin(rad) * radius)
            }
            drawArc(
                color = trackColor,
                startAngle = -90f,
                sweepAngle = 360f,
                useCenter = false,
                topLeft = Offset(inset, inset),
                size = arcSize,
                style = Stroke(stroke, cap = StrokeCap.Round),
            )
            // 12 / 3 / 6 / 9 点（24 小时制是 0 / 6 / 12 / 18 点）的刻度，让表盘一眼能读。
            val tickOuter = size.minDimension / 2 - stroke - 3.dp.toPx()
            val tickInner = tickOuter - 4.dp.toPx()
            for (quarter in 0 until 4) {
                val degrees = quarter * 90f - 90f
                drawLine(
                    color = tickColor,
                    start = polar(degrees, tickInner),
                    end = polar(degrees, tickOuter),
                    strokeWidth = 1.5.dp.toPx(),
                    cap = StrokeCap.Round,
                )
            }
            segments.forEach { segment ->
                drawArc(
                    color = if (segment.past) segment.color.copy(alpha = 0.35f) else segment.color,
                    startAngle = angleOf(segment.start),
                    sweepAngle = (segment.end - segment.start) / period.toFloat() * 360f,
                    useCenter = false,
                    topLeft = Offset(inset, inset),
                    size = arcSize,
                    style = Stroke(stroke, cap = StrokeCap.Round),
                )
            }
            if (currentDay) {
                val degrees = angleOf(now.minutes) + spin.value * 360f
                drawLine(
                    color = handColor,
                    start = polar(degrees, size.minDimension * 0.28f),
                    end = polar(degrees, size.minDimension / 2 - stroke),
                    strokeWidth = 1.5.dp.toPx(),
                    cap = StrokeCap.Round,
                )
            }
        }
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text(
                if (tomorrow) "明天" else "今天",
                fontSize = 10.sp,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text("${lessons.size} 节", fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
        }
    }
}

@Composable
internal fun SemesterProgressCard(week: Int?, modifier: Modifier = Modifier) {
    if (week == null || week <= 0) return
    val current = week.coerceIn(1, TOTAL_WEEKS)
    val slotBorder = MaterialTheme.colorScheme.outline
    val currentBorder = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.8f)
    val blank = MaterialTheme.colorScheme.surface
    val fill = remember { Animatable(0f) }
    LaunchedEffect(current) {
        fill.snapTo(0f)
        fill.animateTo(
            current.toFloat(),
            tween(durationMillis = 180 + current * 80, easing = LinearOutSlowInEasing),
        )
    }
    Surface(
        modifier = modifier,
        shape = RoundedCornerShape(26.dp),
        color = MaterialTheme.colorScheme.surface,
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.75f)),
    ) {
        Column(
            modifier = Modifier.fillMaxSize().padding(horizontal = 8.dp, vertical = 12.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text("学期进度", fontSize = 9.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(6.dp))
            Canvas(Modifier.weight(1f).width(18.dp)) {
                val gap = 2.dp.toPx()
                val height = (size.height - gap * (TOTAL_WEEKS - 1)) / TOTAL_WEEKS
                val radius = CornerRadius(2.dp.toPx(), 2.dp.toPx())
                for (w in 1..TOTAL_WEEKS) {
                    val top = (TOTAL_WEEKS - w) * (height + gap)
                    if (w <= current) {
                        val reveal = (fill.value - (w - 1)).coerceIn(0f, 1f)
                        if (reveal > 0f) {
                            val h = height * reveal
                            val r = CornerRadius(minOf(2.dp.toPx(), h / 2), minOf(2.dp.toPx(), h / 2))
                            drawRoundRect(
                                color = weekColor(w),
                                topLeft = Offset(0f, top + height - h),
                                size = Size(size.width, h),
                                cornerRadius = r,
                            )
                        }
                        drawRoundRect(
                            color = slotBorder,
                            topLeft = Offset(0f, top),
                            size = Size(size.width, height),
                            cornerRadius = radius,
                            style = Stroke(1.dp.toPx()),
                        )
                    } else {
                        drawRoundRect(
                            color = blank,
                            topLeft = Offset(0f, top),
                            size = Size(size.width, height),
                            cornerRadius = radius,
                        )
                        drawRoundRect(
                            color = slotBorder,
                            topLeft = Offset(0f, top),
                            size = Size(size.width, height),
                            cornerRadius = radius,
                            style = Stroke(1.dp.toPx()),
                        )
                    }
                    if (w == current) {
                        drawRoundRect(
                            color = currentBorder,
                            topLeft = Offset(0f, top),
                            size = Size(size.width, height),
                            cornerRadius = radius,
                            style = Stroke(1.dp.toPx()),
                        )
                    }
                }
            }
            Spacer(Modifier.height(6.dp))
            Text("$current/$TOTAL_WEEKS", fontSize = 9.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

private fun weekColor(week: Int): Color = when (week) {
    in 1..3 -> Color(0xFF8FD694)   // 浅绿
    in 4..6 -> Color(0xFF2F9E44)   // 深绿
    in 7..9 -> Color(0xFF4B63E0)   // 蓝
    in 10..12 -> Color(0xFF7048E8) // 紫
    in 13..15 -> Color(0xFFE8590C) // 橙
    else -> Color(0xFFE03131)      // 红
}

private fun clock(minutes: Int): String =
    String.format(java.util.Locale.ROOT, "%02d:%02d", minutes / 60, minutes % 60)

/**
 * 剩余时间按分钟向上取整：还差 1 分 30 秒显示“2 分钟”，与流体云胶囊的“剩 N 分钟”一致。
 * 首页每 30 秒才刷新一次，显示秒数反而不准。
 */
internal fun remainText(ms: Long): String {
    val total = ((ms.coerceAtLeast(0) + 59_999L) / 60_000L).coerceAtLeast(1L)
    val hours = total / 60
    val minutes = total % 60
    return when {
        hours >= 1 && minutes >= 1 -> "$hours 小时 $minutes 分钟"
        hours >= 1 -> "$hours 小时"
        else -> "$minutes 分钟"
    }
}
