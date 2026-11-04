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
    h("span", {}, "用这台手机的网络直接向学校获取课表（推荐）",
      h("span", { class: "muted" }, "（本站服务器少访问学校，不容易被学校限制。会把教务系统的登录状态交给你自己的小组件和绑定过的安卓 App，"
        + "不含统一身份认证的凭据；手机拿不到时仍由服务器更新。这个开关对 iOS 和安卓都生效）")));
  const manual = h("textarea", {
    class: "widget-script", readonly: "", rows: "6", hidden: true, "aria-label": "小组件脚本，手动全选复制",
  });

  const steps = h("ol", { class: "widget-steps" },
    h("li", {}, "在 App Store 安装免费的 ",
      h("a", { href: SCRIPTABLE_URL, target: "_blank", rel: "noopener noreferrer" }, "Scriptable"), "。"),
    h("li", {}, "点上面的“复制脚本”，打开 Scriptable → 右上角 ＋ → 粘贴，名字随意（如“南财课表”）。"),
    h("li", {}, "回到桌面长按 → 左上角 ＋ → 搜索 Scriptable → 选尺寸并添加。"),
    h("li", {}, "长按小组件 → 编辑小组件：Script 选刚才的脚本；Parameter 填 ",
      h("code", {}, "today"), "（今天，默认）、", h("code", {}, "tomorrow"), "（明天）或 ",
      h("code", {}, "week"), "（本周，用中号 / 大号）或 ", h("code", {}, "full"),
      "（铺满：本周课表按时长铺满整块，信息更全，建议大号）。再写一个数字是提前几分钟提醒，例如 ",
      h("code", {}, "today 15"), "；", h("code", {}, "today 0"), " 关闭。不写则提前 10 分钟。几块小组件请写同一个数字。"),
    h("li", {}, "锁屏小组件同理，会显示下一节课和倒计时。"),
    h("li", {}, "上课提醒是手机本地通知（课程、时间、教室）。请打开 Scriptable 运行一次脚本并允许通知；"
      + "课表有变动后，再运行一次脚本就会按新课表重排。不要依赖桌面刷新去改通知。"),
    h("li", {}, "课表该更新时（每节课出门前也会确认一次），小组件会用手机的网络直接向学校拿课表；"
      + "拿不到或关闭了这项时由服务器拉取，标题栏显示“更新中”，大约两分钟后再画一次。"
      + "在 Scriptable 里运行脚本，选“刷新课表”可以马上更新。"),
    h("li", {}, "小组件提示“脚本可更新”时，在 Scriptable 里运行脚本选“更新脚本”即可，不用重新复制。"
      + "提示“请重新复制脚本”时，点上面的“复制脚本”替换旧脚本的全部内容，手机才能继续直接向学校获取课表。"));

  const el = h("div", { class: "widget-settings" },
    h("p", { class: "hint" }, "借助免费的 Scriptable App，在 iPhone / iPad 桌面和锁屏上显示今天、明天或本周的课。"
      + "课表过期或快上课时，小组件会主动请服务器更新，并在上课前发本地通知。"
      + "小组件用一条只读链接取数据，链接本身就是凭证，泄露后请重新生成。"
      + "脚本里还有一把代抓密钥，能领到你的教务登录状态，不要把整个脚本发给别人。"),
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
      message: "旧链接会立即失效，已经添加的小组件需要粘贴新复制的脚本。安卓 App 共用这个凭证，也要重新粘贴链接。",
      confirmText: "重新生成",
    })) return;
    try {
      const result = await api.post("api/widget/rotate");
      const had = Boolean(url);
      onChange?.(result);
      manual.hidden = true;
      status.textContent = had ? "已生成新链接：请重新复制脚本，替换 Scriptable 里的旧脚本。" : "链接已生成，下一步：复制脚本。";
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
      status.textContent = "脚本已复制，去 Scriptable 新建脚本并粘贴。";
      showToast("脚本已复制", { type: "success" });
    } catch {
      // 剪贴板被拒绝：把脚本放进文本框，让用户手动全选复制
      try {
        manual.value = await request;
        manual.hidden = false;
        manual.focus();
        manual.select();
        status.textContent = "浏览器没允许自动复制：脚本已选中，请手动复制。";
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
      status.textContent = want ? "已开启：小组件会用手机的网络向学校获取课表。" : "已关闭：课表改由服务器向学校获取。";
    } catch (error) {
      fetchToggle.checked = !want;
      showToast(error.message, { type: "error" });
    }
  }

  async function disable() {
    if (!await confirmDialog({
      title: "停用桌面小组件？",
      message: "链接会立即失效，已经添加的小组件和安卓 App 都会提示链接失效。",
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
