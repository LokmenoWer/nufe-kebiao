package wiki.nufe.kebiao.ui

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import java.io.File
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import wiki.nufe.kebiao.data.AppUpdate
import wiki.nufe.kebiao.data.AppUpdater
import wiki.nufe.kebiao.data.UpdateRejectedException
import wiki.nufe.kebiao.diag.Diag

internal sealed interface UpdateStep {
    data object Idle : UpdateStep

    /** percent 为 null：服务器没给总大小。 */
    data class Downloading(val percent: Int?) : UpdateStep

    /** 下载好了，等用户在系统设置里允许“安装未知应用”。 */
    data object NeedPermission : UpdateStep
    data class Failed(val message: String) : UpdateStep
}

/** 下载 → 必要时去要权限 → 打开系统安装界面。主页卡片和“关于”共用一份状态。 */
internal class UpdateFlow(
    private val context: Context,
    private val updater: AppUpdater,
    private val scope: CoroutineScope,
) {
    var step by mutableStateOf<UpdateStep>(UpdateStep.Idle)
        private set
    private var job: Job? = null
    private var ready: File? = null
    private var current: AppUpdate? = null

    /** 允许安装后系统可能重启 App：重启后接着上次的版本装，文件已经下载好了。 */
    fun resumeAfterRestart(update: AppUpdate?) {
        val awaiting = updater.takeAwaitingPermission()
        if (update != null && awaiting == update.version && updater.canInstall()) start(update)
    }

    fun start(update: AppUpdate) {
        if (step is UpdateStep.Downloading) return
        current = update
        step = UpdateStep.Downloading(null)
        job = scope.launch {
            try {
                ready = updater.download(update) { percent ->
                    val next = UpdateStep.Downloading(percent)
                    if (step != next) step = next
                }
                proceed()
            } catch (e: CancellationException) {
                step = UpdateStep.Idle
                throw e
            } catch (_: UpdateRejectedException) {
                step = UpdateStep.Failed("安装包完整性或签名校验未通过，请改用浏览器下载。")
            } catch (_: Exception) {
                step = UpdateStep.Failed("下载失败，请检查网络后重试。")
            }
        }
    }

    fun cancel() {
        job?.cancel()
    }

    fun openPermissionSettings() {
        runCatching { context.startActivity(updater.permissionIntent().addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
            .onFailure { step = UpdateStep.Failed("无法打开系统授权设置，请改用浏览器下载。") }
    }

    /** 从系统设置回来时接着装。 */
    fun onResume() {
        if (step == UpdateStep.NeedPermission && updater.canInstall()) {
            updater.takeAwaitingPermission()
            proceed()
        }
    }

    fun openInBrowser(update: AppUpdate) {
        context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(update.url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }

    private fun proceed() {
        val file = ready ?: return
        if (!updater.canInstall()) {
            step = UpdateStep.NeedPermission
            current?.let(updater::markAwaitingPermission)
            openPermissionSettings()
            return
        }
        try {
            context.startActivity(updater.installIntent(file))
            step = UpdateStep.Idle
        } catch (_: ActivityNotFoundException) {
            Diag.w("update: no installer")
            step = UpdateStep.Failed("无法拉起系统安装界面，请改用浏览器下载。")
        }
    }
}
