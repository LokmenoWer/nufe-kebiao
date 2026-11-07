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
  const devicesHint = h("p", { class: "hint", hidden: true }, "已绑定的设备（可读取姓名、学号等个人信息，并支持手机端代抓课表）：");

  const steps = h("ol", { class: "widget-steps" },
    h("li", {}, "下载并安装最新版 ",
      h("a", { href: RELEASE_URL, target: "_blank", rel: "noopener noreferrer" }, "nufe课表 安卓 App"),
      "；若无法访问 GitHub，可直接从", h("a", { href: "android/latest.apk" }, "本站镜像下载"), "。"),
    h("li", {}, "在手机浏览器中点击上方“在安卓 App 中打开”，或点击“复制配对链接”后打开 App 粘贴。"),
    h("li", {}, "在 App 首页点击“添加课表小组件到桌面”，或长按手机桌面 → 小组件 → 找到“nufe课表”添加（本周课表建议使用 4×3 及以上尺寸）。"),
    h("li", {}, "在 App 内开启“上课通知”并设定提前提醒时间；国产定制系统请在电池设置中允许 App 自启动与后台不受限运行。"));

  const el = h("div", { class: "widget-settings" },
    h("div", { class: "callout" }, "App 2.0 起可在手机上直接登录学校、课表只保存在本机，无需本站账号，也不需要下面的配对链接；"
      + "可直接", h("a", { href: "android/latest.apk" }, "下载最新版安装包"), "。以下配对仅供旧版（1.x）App 使用。"),
    h("p", { class: "hint" }, "使用“nufe课表”安卓 App 查看今日与本周课程、添加桌面小组件并接收课前提醒。"
      + "课表基础链接不包含姓名、学号等个人身份信息，个人信息仅向已完成绑定的设备提供；绑定后 App 还会协助使用手机网络向教务系统同步课表"
      + "（可在上方 iOS 小组件模块中统一开关）。若手机丢失或链接外泄，请及时点击“重新生成”。"),
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
      message: `撤销后，“${device.name}”将无法再读取个人信息或参与手机代抓，但原链接仍可读取课表。若手机丢失或链接外泄，请使用“重新生成”。`,
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
      status.textContent = `配对链接将在 ${Math.round(result.expires_in / 60)} 分钟后过期，仅限使用一次，请勿转发他人。`;
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
      message: "重新生成后，旧链接与所有已绑定设备将立即失效，安卓 App 需重新配对；iOS 小组件共用该凭证，也需重新复制脚本。",
      confirmText: "重新生成",
    })) return;
    await create();
  }

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(pairUrl);
      manual.hidden = true;
      status.textContent = "配对链接已复制，请打开 nufe课表 App 粘贴。";
      showToast("配对链接已复制", { type: "success" });
    } catch {
      manual.value = pairUrl;
      manual.hidden = false;
      manual.focus();
      manual.select();
      status.textContent = "浏览器未允许自动写入剪贴板，链接已全选，请手动复制。";
    }
  }

  async function disable() {
    if (!await confirmDialog({
      title: "停用安卓 App 链接？",
      message: "停用后链接与所有已绑定设备将立即失效，安卓 App 与 iOS 小组件均会提示链接失效。",
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
