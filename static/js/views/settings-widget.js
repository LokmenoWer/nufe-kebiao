/** 设置里的“iOS 桌面小组件”：生成只读链接、复制 Scriptable 脚本、使用步骤。安卓见 settings-android.js。 */

import { api } from "../api.js";
import { h } from "../dom.js";
import { icon } from "../icons.js";
import { showToast } from "../components/toast.js";
import { confirmDialog } from "../components/dialog.js";

// 用国区链接：不带地区的链接从大陆 IP 打开会被跳到 App Store 首页，而不是应用页
const SCRIPTABLE_URL = "https://apps.apple.com/cn/app/scriptable/id1405459188";

/** iOS Safari 要求剪贴板写入发生在点击手势里：把“还在下载的脚本”交给 ClipboardItem，而不是 await 之后再写。 */
async function copyFromRequest(textPromise) {
  if (typeof ClipboardItem !== "undefined" && navigator.clipboard?.write) {
    const blob = textPromise.then((text) => new Blob([text], { type: "text/plain" }));
    await navigator.clipboard.write([new ClipboardItem({ "text/plain": blob })]);
    return;
  }
  await navigator.clipboard.writeText(await textPromise);
}

export function createWidgetSection({ widgetUrl, widgetFetch = true, onChange, onFetchChange } = {}) {
  let url = widgetUrl || null;

  const status = h("p", { class: "hint", role: "status", "aria-live": "polite" });
  const createBtn = h("button", { type: "button", class: "btn btn-sm", onClick: () => rotate() }, "生成小组件链接");
  const copyBtn = h("button", { type: "button", class: "btn btn-sm btn-primary", onClick: () => copyScript() },
    icon("copy"), h("span", {}, "复制脚本"));
  const rotateBtn = h("button", { type: "button", class: "btn btn-sm btn-ghost", onClick: () => rotate() }, "重新生成");
  const disableBtn = h("button", { type: "button", class: "btn btn-sm btn-ghost", onClick: () => disable() }, "停用");
  const fetchToggle = h("input", { type: "checkbox", onChange: () => saveFetch() });
  fetchToggle.checked = widgetFetch;
  const fetchRow = h("label", { class: "check" }, fetchToggle,
    h("span", {}, "使用手机网络直接向教务系统获取课表（推荐）",
      h("span", { class: "muted" }, "（可减少本站服务器对学校接口的访问频率，降低被限流风险。开启后会将教务系统会话下发至你本人的小组件与已绑定的安卓 App，"
        + "不包含统一身份认证凭据；手机端拉取失败时仍由服务器兜底更新。此开关同时对 iOS 与安卓生效）")));
  const manual = h("textarea", {
    class: "widget-script", readonly: "", rows: "6", hidden: true, "aria-label": "小组件脚本，手动全选复制",
  });

  const steps = h("ol", { class: "widget-steps" },
    h("li", {}, "在 App Store 安装免费的 ",
      h("a", { href: SCRIPTABLE_URL, target: "_blank", rel: "noopener noreferrer" }, "Scriptable"), "。"),
    h("li", {}, "点击上方“复制脚本”，打开 Scriptable → 右上角 ＋ → 粘贴代码并保存（名称可自定，如“南财课表”）。"),
    h("li", {}, "返回手机桌面并长按空白处 → 点击左上角 ＋ → 搜索 Scriptable → 选择尺寸并添加小组件。"),
    h("li", {}, "长按刚添加的小组件 → 编辑小组件：Script 选择刚才保存的脚本；Parameter 可填 ",
      h("code", {}, "today"), "（今天，默认）、", h("code", {}, "tomorrow"), "（明天）、",
      h("code", {}, "week"), "（本周，按节次，建议中号 / 大号）或 ", h("code", {}, "full"),
      "（铺满：本周课表按时长铺满整块，信息更全，建议大号）。参数后可追加数字表示提前几分钟提醒，如 ",
      h("code", {}, "today 15"), "；填 ", h("code", {}, "today 0"), " 关闭提醒，不填则默认提前 10 分钟。若添加多块小组件，请保持提醒分钟数一致。"),
    h("li", {}, "锁屏小组件添加方式相同，支持显示下一节课与实时倒计时。"),
    h("li", {}, "课前提醒通过手机本地通知发送（包含课程、时间与教室）。首次使用请打开 Scriptable 运行一次脚本并授予通知权限；"
      + "后续若课表发生变动，在 Scriptable 内再次运行脚本即可按最新课表重排提醒。"),
    h("li", {}, "到达刷新时机（含每节课课前 30 分钟检查）时，小组件会优先用手机网络直接向教务系统获取课表；"
      + "若获取失败或关闭了上方开关，则转由服务器后台更新，此时标题栏显示“更新中”并在约两分钟后重绘。"
      + "在 Scriptable 内运行脚本并选择“刷新课表”可立即手动同步。"),
    h("li", {}, "当小组件提示“脚本可更新”时，在 Scriptable 内运行脚本并选择“更新脚本”即可原地升级，无需重新复制。"
      + "若提示“请重新复制脚本”，请点击上方“复制脚本”替换旧脚本全文，以恢复手机端直接获取课表功能。"));

  const el = h("div", { class: "widget-settings" },
    h("p", { class: "hint" }, "借助免费的 Scriptable 应用，可在 iPhone / iPad 桌面和锁屏上展示今天、明天或本周课表，"
      + "支持自动更新与课前本地通知。小组件通过专属只读链接读取数据，链接本身即为访问凭证，如不慎泄露请及时重新生成；"
      + "复制出的脚本中还包含你的专属代抓密钥，请勿将完整脚本转发给他人。"),
    h("div", { class: "button-row" }, createBtn, copyBtn, rotateBtn, disableBtn),
    fetchRow,
    manual,
    status,
    h("details", { class: "widget-help" }, h("summary", {}, "安装步骤"), steps));

  function render() {
    createBtn.hidden = Boolean(url);
    copyBtn.hidden = !url;
    rotateBtn.hidden = !url;
    disableBtn.hidden = !url;
    fetchRow.hidden = !url;
    if (!url) manual.hidden = true;
  }

  async function rotate() {
    if (url && !await confirmDialog({
      title: "重新生成小组件链接？",
      message: "生成后旧链接将立即失效，已添加的 iOS 小组件需重新复制并粘贴脚本；安卓 App 共用该凭证，也需重新配对连接。",
      confirmText: "重新生成",
    })) return;
    try {
      const result = await api.post("api/widget/rotate");
      const had = Boolean(url);
      onChange?.(result);
      manual.hidden = true;
      status.textContent = had ? "已生成新链接：请重新复制脚本，并替换 Scriptable 中的旧脚本。" : "链接已生成，下一步请点击“复制脚本”。";
    } catch (error) {
      showToast(error.message, { type: "error" });
    }
  }

  async function copyScript() {
    const request = fetch("api/widget/script", { credentials: "same-origin", cache: "no-store" })
      .then((r) => {
        if (!r.ok) throw new Error(r.status === 404 ? "请先生成小组件链接" : `获取脚本失败（${r.status}）`);
        return r.text();
      });
    try {
      await copyFromRequest(request);
      manual.hidden = true;
      status.textContent = "脚本已复制，请前往 Scriptable 新建脚本并粘贴。";
      showToast("脚本已复制", { type: "success" });
    } catch {
      // 剪贴板被拒绝：把脚本放进文本框，让用户手动全选复制
      try {
        manual.value = await request;
        manual.hidden = false;
        manual.focus();
        manual.select();
        status.textContent = "浏览器未允许自动写入剪贴板，脚本已全选，请手动复制。";
      } catch (error) {
        status.textContent = error.message;
      }
    }
  }

  async function saveFetch() {
    const want = fetchToggle.checked;
    try {
      await api.post("api/widget/fetch-pref", { enabled: want });
      onFetchChange?.(want);
      status.textContent = want ? "已开启：小组件与已绑定 App 将优先使用手机网络获取课表。" : "已关闭：课表统一由本站服务器向教务系统获取。";
    } catch (error) {
      fetchToggle.checked = !want;
      showToast(error.message, { type: "error" });
    }
  }

  async function disable() {
    if (!await confirmDialog({
      title: "停用桌面小组件？",
      message: "停用后链接将立即失效，已添加的 iOS 小组件与安卓 App 均会停止同步并提示链接失效。",
      confirmText: "停用",
      danger: true,
    })) return;
    try {
      await api.post("api/widget/disable");
      onChange?.({ widget_url: null, mobile_url: null });
      status.textContent = "已停用。";
    } catch (error) {
      showToast(error.message, { type: "error" });
    }
  }

  render();
  return {
    el,
    update(next) {
      url = next;
      status.textContent = "";
      render();
    },
  };
}
