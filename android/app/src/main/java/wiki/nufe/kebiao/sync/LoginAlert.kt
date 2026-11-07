package wiki.nufe.kebiao.sync

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import wiki.nufe.kebiao.R
import wiki.nufe.kebiao.data.Payload
import wiki.nufe.kebiao.data.Repository
import wiki.nufe.kebiao.data.needsLogin
import wiki.nufe.kebiao.remind.Reminders
import wiki.nufe.kebiao.ui.MainActivity

/**
 * 登录失效时发一条系统通知：用户平时只看桌面小组件，不一定打开 App。
 * 每次失效只发一次，恢复后撤掉；没有通知权限时不记，拿到权限后的下一次同步再发。
 */
object LoginAlert {
    private const val CHANNEL = "account"
    private const val NOTIFICATION_ID = 41_001
    private const val KEY_SENT = "login_alert_sent"

    fun update(context: Context, payload: Payload) {
        val prefs = context.getSharedPreferences("kebiao", Context.MODE_PRIVATE)
        val sent = prefs.getBoolean(KEY_SENT, false)
        if (!payload.needsLogin) {
            if (sent) clear(context)
            return
        }
        if (sent || !Reminders.canNotify(context)) return
        if (post(context, payload)) prefs.edit().putBoolean(KEY_SENT, true).apply()
    }

    fun clear(context: Context) {
        context.getSharedPreferences("kebiao", Context.MODE_PRIVATE).edit().remove(KEY_SENT).apply()
        NotificationManagerCompat.from(context).cancel(NOTIFICATION_ID)
    }

    private fun post(context: Context, payload: Payload): Boolean {
        if (Build.VERSION.SDK_INT >= 26) {
            context.getSystemService(NotificationManager::class.java).createNotificationChannel(
                NotificationChannel(CHANNEL, "账号提醒", NotificationManager.IMPORTANCE_DEFAULT).apply {
                    description = "登录失效、课表停止更新时提醒"
                },
            )
        }
        val target = Intent(context, wiki.nufe.kebiao.ui.SchoolLoginActivity::class.java)
        val open = PendingIntent.getActivity(context, NOTIFICATION_ID, target, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val notification = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_notify_calendar)
            .setContentTitle(if (payload.status == "captcha") "课表需要重新验证" else "课表登录已失效")
            .setContentText("点这里在本机重新登录学校；已缓存的课表仍可离线查看。")
            .setContentIntent(open)
            .setAutoCancel(true)
            .build()
        return try {
            NotificationManagerCompat.from(context).notify(NOTIFICATION_ID, notification)
            true
        } catch (_: SecurityException) {
            false // 通知权限在这之间被关掉了
        }
    }
}
