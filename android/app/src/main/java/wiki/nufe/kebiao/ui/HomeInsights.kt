package wiki.nufe.kebiao.ui

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
import wiki.nufe.kebiao.data.Now
import wiki.nufe.kebiao.data.State
import wiki.nufe.kebiao.data.lessonMs
import wiki.nufe.kebiao.widget.parseColor

private const val TOTAL_WEEKS = 17

@Composable
internal fun TodayRhythmCard(plan: DayPlan?, now: Now) {
    val lessons = plan?.day?.lessons.orEmpty()
    val tomorrow = plan?.title == "明天"
    val next = plan?.slots?.firstOrNull()
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
                Row(verticalAlignment = Alignment.CenterVertically) {
                    RhythmRing(
                        lessons = plan?.day?.lessons.orEmpty(),
                        now = now,
                        currentDay = currentDay,
                        tomorrow = tomorrow,
                        modifier = Modifier.size(84.dp),
                    )
                    Spacer(Modifier.width(8.dp))
                    Column(Modifier.weight(1f)) {
                        if (next != null) {
                            Text(
                                if (next.state == State.LIVE) "正在上课" else "下一节",
                                fontSize = 10.sp,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                            Spacer(Modifier.height(2.dp))
                            Text(
                                "${next.lesson.start}–${next.lesson.end}",
                                fontSize = 15.sp,
                                fontWeight = FontWeight.SemiBold,
                                maxLines = 1,
                            )
                            Text(
                                "${next.lesson.name} · ${next.lesson.room.ifEmpty { "地点待定" }}",
                                fontSize = 10.sp,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                maxLines = 2,
                            )
                            val target = if (next.state == State.LIVE) {
                                lessonMs(plan?.day?.date.orEmpty(), next.lesson.end)
                            } else {
                                lessonMs(plan?.day?.date.orEmpty(), next.lesson.start)
                            }
                            val remain = target - now.ms
                            if (remain > 0) {
                                Spacer(Modifier.height(3.dp))
                                Text(
                                    if (next.state == State.LIVE) "还剩 ${remainText(remain)}"
                                    else "${remainText(remain)}后上课",
                                    fontSize = 10.sp,
                                    color = MaterialTheme.colorScheme.primary,
                                )
                            }
                        } else {
                            Text("今天没有课", fontSize = 13.sp)
                            Spacer(Modifier.height(2.dp))
                            Text(
                                plan?.empty ?: "可以休息一下。",
                                fontSize = 11.sp,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun RhythmRing(
    lessons: List<wiki.nufe.kebiao.data.Lesson>,
    now: Now,
    currentDay: Boolean,
    tomorrow: Boolean,
    modifier: Modifier,
) {
    val handColor = MaterialTheme.colorScheme.onSurface
    Box(modifier, contentAlignment = Alignment.Center) {
        Canvas(Modifier.size(96.dp)) {
            val stroke = 7.dp.toPx()
            val inset = stroke / 2
            val arcSize = Size(size.width - stroke, size.height - stroke)
            drawArc(
                color = Color(0x1F000000),
                startAngle = -90f,
                sweepAngle = 360f,
                useCenter = false,
                topLeft = Offset(inset, inset),
                size = arcSize,
                style = Stroke(stroke, cap = StrokeCap.Round),
            )
            lessons.forEach { lesson ->
                val start = lesson.startMinutes.coerceIn(0, 24 * 60)
                val end = lesson.endMinutes.coerceIn(start, 24 * 60)
                if (end > start) {
                    drawArc(
                        color = parseColor(lesson.color),
                        startAngle = start / 1440f * 360f - 90f,
                        sweepAngle = (end - start) / 1440f * 360f,
                        useCenter = false,
                        topLeft = Offset(inset, inset),
                        size = arcSize,
                        style = Stroke(stroke, cap = StrokeCap.Round),
                    )
                }
            }
            if (currentDay) {
                val angle = (now.minutes / 1440f * 360f - 90f) * (Math.PI / 180).toFloat()
                val radius = size.minDimension / 2 - stroke
                val center = Offset(size.width / 2, size.height / 2)
                val startRadius = size.minDimension * 0.28f
                drawLine(
                    color = handColor,
                    start = Offset(
                        center.x + kotlin.math.cos(angle) * startRadius,
                        center.y + kotlin.math.sin(angle) * startRadius,
                    ),
                    end = Offset(
                        center.x + kotlin.math.cos(angle) * radius,
                        center.y + kotlin.math.sin(angle) * radius,
                    ),
                    strokeWidth = 1.5.dp.toPx(),
                    cap = StrokeCap.Round,
                )
            }
        }
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text(
                if (tomorrow) "明天" else "今天",
                fontSize = 9.sp,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text("${lessons.size} 节", fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
        }
    }
}

@Composable
internal fun SemesterProgressCard(week: Int?, modifier: Modifier = Modifier) {
    if (week == null || week <= 0) return
    val current = week.coerceIn(1, TOTAL_WEEKS)
    val outline = MaterialTheme.colorScheme.outline
    Surface(
        modifier = modifier,
        shape = RoundedCornerShape(26.dp),
        color = MaterialTheme.colorScheme.surface,
        border = BorderStroke(1.dp, outline.copy(alpha = 0.75f)),
    ) {
        Column(
            modifier = Modifier.fillMaxSize().padding(horizontal = 8.dp, vertical = 12.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text("学期", fontSize = 10.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(6.dp))
            Canvas(Modifier.weight(1f).width(18.dp)) {
                val gap = 1.5.dp.toPx()
                val height = (size.height - gap * (TOTAL_WEEKS - 1)) / TOTAL_WEEKS
                for (w in 1..TOTAL_WEEKS) {
                    val top = (w - 1) * (height + gap)
                    val color = weekColor(w).copy(alpha = if (w <= current) 1f else 0.18f)
                    drawRoundRect(
                        color = color,
                        topLeft = Offset(0f, top),
                        size = Size(size.width, height),
                        cornerRadius = CornerRadius(2.dp.toPx(), 2.dp.toPx()),
                    )
                    if (w == current) {
                        drawRoundRect(
                            color = outline,
                            topLeft = Offset(0f, top),
                            size = Size(size.width, height),
                            cornerRadius = CornerRadius(2.dp.toPx(), 2.dp.toPx()),
                            style = Stroke(1.dp.toPx()),
                        )
                    }
                }
            }
            Spacer(Modifier.height(6.dp))
            Text("$week/$TOTAL_WEEKS", fontSize = 10.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

private fun weekColor(week: Int): Color = when (week) {
    in 1..3 -> Color(0xFF8FD694)   // 浅绿
    in 4..7 -> Color(0xFF2F9E44)   // 深绿
    in 8..11 -> Color(0xFF4B63E0)  // 蓝
    in 12..15 -> Color(0xFFE8590C) // 橙
    else -> Color(0xFFE03131)      // 红
}

private fun clock(minutes: Int): String =
    String.format(java.util.Locale.ROOT, "%02d:%02d", minutes / 60, minutes % 60)

private fun remainText(ms: Long): String {
    val total = (ms / 1000).coerceAtLeast(0)
    val hours = total / 3600
    val minutes = total % 3600 / 60
    return when {
        hours >= 1 && minutes >= 1 -> "$hours 小时 $minutes 分钟"
        hours >= 1 -> "$hours 小时"
        minutes >= 1 -> "$minutes 分钟"
        else -> "$total 秒"
    }
}
