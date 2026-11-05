package wiki.nufe.kebiao.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
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
import wiki.nufe.kebiao.data.lessonMs
import wiki.nufe.kebiao.widget.parseColor

@Composable
internal fun TodayRhythmCard(plan: DayPlan?, now: Now) {
    val lessons = plan?.day?.lessons.orEmpty()
    val tomorrow = plan?.title == "明天"
    val title = if (tomorrow) "明日节奏" else "今日节奏"
    val next = plan?.slots?.firstOrNull()
    val currentDay = plan?.day?.date == now.date
    val handColor = MaterialTheme.colorScheme.onSurface

    SectionCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(title, fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.weight(1f))
            Text(
                "现在 ${clock(now.minutes)}",
                fontSize = 11.sp,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Spacer(Modifier.height(10.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(132.dp), contentAlignment = Alignment.Center) {
                Canvas(Modifier.size(132.dp)) {
                    val stroke = 9.dp.toPx()
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
                            strokeWidth = 2.dp.toPx(),
                            cap = StrokeCap.Round,
                        )
                    }
                }
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(if (tomorrow) "明天" else "今天", fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Text("${lessons.size} 节", fontSize = 17.sp, fontWeight = FontWeight.SemiBold)
                }
            }
            Spacer(Modifier.width(18.dp))
            Column(Modifier.weight(1f)) {
                if (next != null) {
                    Text(
                        if (next.state == wiki.nufe.kebiao.data.State.LIVE) "正在上课" else "下一节",
                        fontSize = 11.sp,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Spacer(Modifier.height(3.dp))
                    Text("${next.lesson.start}–${next.lesson.end}", fontSize = 18.sp, fontWeight = FontWeight.SemiBold)
                    Spacer(Modifier.height(3.dp))
                    Text(
                        "${next.lesson.name} · ${next.lesson.room.ifEmpty { "地点待定" }}",
                        fontSize = 12.sp,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 2,
                    )
                    val target = if (next.state == wiki.nufe.kebiao.data.State.LIVE) {
                        lessonMs(plan?.day?.date.orEmpty(), next.lesson.end)
                    } else {
                        lessonMs(plan?.day?.date.orEmpty(), next.lesson.start)
                    }
                    val remain = target - now.ms
                    if (remain > 0) {
                        Spacer(Modifier.height(6.dp))
                        Text(
                            if (next.state == wiki.nufe.kebiao.data.State.LIVE) "还剩 ${remainText(remain)}"
                            else "${remainText(remain)}后上课",
                            fontSize = 12.sp,
                            color = MaterialTheme.colorScheme.primary,
                        )
                    }
                } else {
                    Text("今天没有课", fontSize = 14.sp)
                    Spacer(Modifier.height(4.dp))
                    Text(
                        plan?.empty ?: "可以休息一下。",
                        fontSize = 12.sp,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }
}

private const val TOTAL_WEEKS = 17

@Composable
internal fun SemesterProgressCard(week: Int?) {
    if (week == null || week <= 0) return
    SectionCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("学期进度", fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.weight(1f))
            Text(
                "第 $week 周 / $TOTAL_WEEKS 周",
                fontSize = 11.sp,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Spacer(Modifier.height(10.dp))
        LinearProgressIndicator(
            progress = { (week.toFloat() / TOTAL_WEEKS).coerceIn(0f, 1f) },
            modifier = Modifier.fillMaxWidth().height(7.dp).clip(RoundedCornerShape(50)),
            color = MaterialTheme.colorScheme.primary,
            trackColor = MaterialTheme.colorScheme.surfaceVariant,
        )
    }
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
