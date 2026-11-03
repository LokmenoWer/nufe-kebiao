/** 设置里的“安卓 App”：配对链接交给 App、管理已绑定设备、安装步骤。 */

import { api } from "../api.js";
import { h } from "../dom.js";
import { icon } from "../icons.js";
import * as T from "../time.js";
import { showToast } from "../components/toast.js";
import { confirmDialog } from "../components/dialog.js";

const RELEASE_URL = "https://github.com/LokmenoWer/nufe-kebiao/releases/latest";

export function createAndroidSection({ mobileUrl, onChange } = {}) {
  let url = mobileUrl || null;
  let pairUrl = null;

  const status = h("p", { class: "hint", role: "status", "aria-live": "polite" });
  const createBtn = h("button", { type: "button", class: "btn btn-sm btn-primary", onClick: () => create() }, "生成配对链接");
  const pairBtn = h("button", { type: "button", class: "btn btn-sm btn-primary", onClick: () => pair() }, "绑定新设备");
  const rotateBtn = h("button", { type: "button", class: "btn btn-sm btn-ghost", onClick: () => rotate() }, "重新生成");
  const disableBtn = h("button", { type: "button", class: "btn btn-sm btn-ghost", onClick: () => disable() }, "停用");
  // nufekebiao:// 能一键带过去；电脑上打开设置时复制配对链接，发到手机上在 App 里粘贴
  const openLink = h("a", { class: "btn btn-sm btn-primary", href: "#" }, "在安卓 App 中打开");
  const copyBtn = h("button", { type: "button", class: "btn btn-sm", onClick: () => copyLink() },
    icon("copy"), h("span", {}, "复制配对链接"));
  const pairRow = h("div", { class: "button-row", hidden: true }, openLink, copyBtn);
  const manual = h("textarea", {
    class: "widget-script", readonly: "", rows: "3", hidden: true, "aria-label": "配对链接，手动全选复制",
  });
  const devices = h("ul", { class: "device-list" });
  const devicesHint = h("p", { class: "hint", hidden: true }, "已绑定的设备：可以读取你的姓名、学号等个人信息。");

  const steps = h("ol", { class: "widget-steps" },
    h("li", {}, "下载安装最新版 ",
      h("a", { href: RELEASE_URL, target: "_blank", rel: "noopener noreferrer" }, "nufe课表 安卓 App"), "。"),
    h("li", {}, "在手机浏览器里点上面的“在安卓 App 中打开”，或“复制配对链接”后在 App 里粘贴。"),
    h("li", {}, "在 App 里点“添加课表小组件到桌面”，或长按桌面 → 小组件 → nufe课表。本周课表建议 4×3 以上。"),
    h("li", {}, "上课提醒在 App 里选提前几分钟，并允许通知；国产系统请允许 App 自启动、后台运行。"));

  const el = h("div", { class: "widget-settings" },
    h("p", { class: "hint" }, "用“nufe课表”App 查看今天的课、在桌面显示今天和本周的课，并在课前提醒。"
      + "课表链接不含姓名、学号等个人信息，这些只发给绑定过的设备。绑定后 App 还会用手机的网络向学校获取课表，"
      + "可在上面 iOS 模块的开关里关闭。手机丢失或链接泄露时请重新生成。"),
    h("div", { class: "button-row" }, createBtn, pairBtn, rotateBtn, disableBtn),
    pairRow,
    manual,
    status,
    devicesHint,
    devices,
    h("details", { class: "widget-help" }, h("summary", {}, "安装步骤"), steps));

  function render() {
    createBtn.hidden = Boolean(url);
    pairBtn.hidden = !url;
    rotateBtn.hidden = !url;
    disableBtn.hidden = !url;
    pairRow.hidden = !pairUrl;
    openLink.href = pairUrl || "#";
    if (!pairUrl) manual.hidden = true;
  }

  async function loadDevices() {
    if (!url) {
      devices.replaceChildren();
      devicesHint.hidden = true;
      return;
    }
    try {
      const result = await api.get("api/mobile/devices");
      devicesHint.hidden = !result.devices.length;
      devices.replaceChildren(...result.devices.map(deviceRow));
    } catch {
      // 列表取不到不影响别的功能，下次打开再试
    }
  }

  function deviceRow(device) {
    return h("li", { class: "device-item" },
      h("div", {}, h("div", {}, device.name),
        h("p", { class: "hint" }, `绑定于 ${T.formatDateTime(device.created_at)} · 最近使用 ${T.formatDateTime(device.last_seen_at)}`)),
      h("button", { type: "button", class: "btn btn-sm btn-ghost", onClick: () => revoke(device) }, "撤销"));
  }

  async function revoke(device) {
    if (!await confirmDialog({
      title: "撤销这台设备？",
      message: `“${device.name}”将不能再读取个人信息，但只要有链接，课表仍然能读。手机丢失请改用“重新生成”。`,
      confirmText: "撤销",
      danger: true,
    })) return;
    try {
      await api.post(`api/mobile/devices/${encodeURIComponent(device.id)}/revoke`);
      status.textContent = "设备已撤销。";
      await loadDevices();
    } catch (error) {
      showToast(error.message, { type: "error" });
    }
  }

  async function pair() {
    try {
      const result = await api.post("api/mobile/pair");
      pairUrl = result.pair_url;
      render();
      status.textContent = `配对链接 ${Math.round(result.expires_in / 60)} 分钟内有效，只能用一次，不要发给别人。`;
    } catch (error) {
      showToast(error.message, { type: "error" });
    }
  }

  async function create() {
    try {
      onChange?.(await api.post("api/widget/rotate"));
      await pair();
    } catch (error) {
      showToast(error.message, { type: "error" });
    }
  }

  async function rotate() {
    if (!await confirmDialog({
      title: "重新生成链接？",
      message: "旧链接和所有已绑定设备会立即失效，App 里要重新配对。iOS 小组件共用这个凭证，也要重新复制脚本。",
      confirmText: "重新生成",
    })) return;
    await create();
  }

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(pairUrl);
      manual.hidden = true;
      status.textContent = "配对链接已复制，打开 nufe课表 App 粘贴即可。";
      showToast("配对链接已复制", { type: "success" });
    } catch {
      manual.value = pairUrl;
      manual.hidden = false;
      manual.focus();
      manual.select();
      status.textContent = "浏览器没允许自动复制：链接已选中，请手动复制。";
    }
  }

  async function disable() {
    if (!await confirmDialog({
      title: "停用安卓 App 链接？",
      message: "链接和所有已绑定设备会立即失效，App 和 iOS 小组件都会提示链接失效。",
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

  // 在手机上配对完切回网页时，设备列表要跟上
  const onVisible = () => {
    if (!el.isConnected) {
      document.removeEventListener("visibilitychange", onVisible);
    } else if (document.visibilityState === "visible") {
      loadDevices();
    }
  };
  document.addEventListener("visibilitychange", onVisible);

  render();
  loadDevices();
  return {
    el,
    update(next) {
      url = next;
      pairUrl = null;
      status.textContent = "";
      render();
      loadDevices();
    },
  };
}
