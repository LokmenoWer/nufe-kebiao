package wiki.nufe.kebiao.sync

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import wiki.nufe.kebiao.R
import wiki.nufe.kebiao.data.Payload
import wiki.nufe.kebiao.remind.Reminders
import wiki.nufe.kebiao.ui.MainActivity

object ChangeAlert {
    private const val ID = 41_002
    private const val CHANNEL = "timetable_changes"

    fun update(context: Context, payload: Payload) {
        val changed = payload.changedAt ?: return
        val prefs = context.getSharedPreferences("kebiao", Context.MODE_PRIVATE)
        if (changed <= prefs.getLong("change_alert_at", 0) || !Reminders.canNotify(context)) return
        context.getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(CHANNEL, "调课通知", NotificationManager.IMPORTANCE_DEFAULT),
        )
        val open = PendingIntent.getActivity(context, ID, Intent(context, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val weeks = payload.changes.mapNotNull { it.week }.distinct().sorted()
        val text = if (weeks.isEmpty()) "本机发现课程调整，点击查看详细信息。"
        else "第${weeks.joinToString("、")}周调整了课程，点击查看详细信息。"
        val notification = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_notify_calendar)
            .setContentTitle("课表有变化")
            .setContentText(text)
            .setContentIntent(open)
            .setAutoCancel(true)
            .build()
        try {
            NotificationManagerCompat.from(context).notify(ID, notification)
            prefs.edit().putLong("change_alert_at", changed).apply()
        } catch (_: SecurityException) {
        }
    }

    fun clear(context: Context) = NotificationManagerCompat.from(context).cancel(ID)
}
