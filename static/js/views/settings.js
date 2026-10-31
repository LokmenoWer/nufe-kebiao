/** 设置抽屉：账号、自动刷新、日历订阅、外观、删除数据。 */

import { api } from "../api.js";
import { h } from "../dom.js";
import { icon } from "../icons.js";
import { showToast } from "../components/toast.js";
import { confirmDialog, openDialog } from "../components/dialog.js";
import * as T from "../time.js";
import { initialOf } from "../courses.js";
import { getThemeMode, setThemeMode } from "../theme.js";

const STATUS = {
  ok: { label: "正常", tone: "ok" },
  error: { label: "刷新失败", tone: "warn" },
  captcha: { label: "需要验证码", tone: "warn" },
  needs_rebind: { label: "需要重新登录", tone: "danger" },
};

const TRIGGERS = { login: "登录", manual: "手动", scheduled: "定时" };

function section(title, ...children) {
  return h("section", { class: "settings-card" }, h("h3", {}, title), ...children);
}

function iconButton(label, iconName, onClick, className = "btn btn-icon btn-ghost") {
  return h("button", {
    type: "button", class: className, "aria-label": label, title: label, onClick,
  }, icon(iconName));
}

export function openSettings({
  me,
  onUpdated,
  onThemeChange,
  onLogout,
  onLogoutAll,
  onAccountDeleted,
} = {}) {
  let current = { ...me };
  let hours = new Set(Array.isArray(current.refresh_hours) ? current.refresh_hours : []);
  let icsUrl = current.ics_url || null;
  let busy = false;

  const closeBtn = iconButton("关闭设置", "x", () => dialog.close("close"));

  const avatar = h("span", { class: "avatar avatar-lg", "aria-hidden": "true" }, initialOf(current.name));
  const nameEl = h("strong", { class: "account-name" }, current.name || "未命名");
  const idEl = h("span", { class: "muted" }, `学号 ${current.student_id || "—"}`);
  const statusPill = h("span", { class: "pill" });
  const credText = h("p", { class: "muted" });
  const forgetBtn = h("button", {
    type: "button", class: "btn btn-sm btn-ghost",
    onClick: () => forgetPassword(),
  }, "删除已保存的密码");

  const refreshInfo = h("dl", { class: "kv" });
  const chips = h("div", { class: "chips", role: "group", "aria-label": "每天自动刷新时间" });
  const saveHoursBtn = h("button", {
    type: "button", class: "btn btn-sm", disabled: true,
    onClick: () => saveHours(),
  }, "保存刷新时间");
  const refreshHint = h("p", { class: "hint" });
  const logList = h("ul", { class: "log-list" }, h("li", { class: "muted" }, "加载中…"));
  const logDetails = h("details", { class: "log-details" },
    h("summary", {}, "最近刷新记录"),
    logList);

  const icsInput = h("input", {
    class: "ics-input", readonly: "", placeholder: "尚未生成订阅链接",
    "aria-label": "日历订阅链接", value: icsUrl || "",
  });
  const copyBtn = iconButton("复制订阅链接", "copy", () => copyIcs(), "btn btn-icon");
  const rotateBtn = h("button", { type: "button", class: "btn btn-sm", onClick: () => rotateIcs() }, "生成链接");
  const disableBtn = h("button", {
    type: "button", class: "btn btn-sm btn-ghost", hidden: true,
    onClick: () => disableIcs(),
  }, "停用订阅");
  const icsInfo = h("p", { class: "hint" });

  const themeButtons = h("div", { class: "seg seg-wide", role: "group", "aria-label": "外观" });
  const themeOptions = [["auto", "跟随系统"], ["light", "浅色"], ["dark", "深色"]];
  for (const [value, label] of themeOptions) {
    themeButtons.append(h("button", {
      type: "button", "data-theme-mode": value, "aria-pressed": "false",
      onClick: () => {
        setThemeMode(value);
        renderTheme();
        onThemeChange?.();
      },
    }, label));
  }

  const logoutBtn = h("button", { type: "button", class: "btn btn-sm", onClick: () => logout() },
    icon("logout"), h("span", {}, "退出登录"));
  const logoutAllBtn = h("button", {
    type: "button", class: "btn btn-sm btn-ghost",
    onClick: () => logoutAll(),
  }, "退出所有设备");
  const deleteBtn = h("button", {
    type: "button", class: "btn btn-danger btn-sm",
    onClick: () => deleteAccount(),
  }, icon("trash"), h("span", {}, "删除我的账号与所有数据"));

  const content = h("div", { class: "settings-body" },
    h("header", { class: "settings-head" },
      h("h2", { id: "settings-title" }, "账号与设置"),
      closeBtn),

    section("账号",
      h("div", { class: "account-row" },
        avatar,
        h("div", { class: "account-info" }, nameEl, idEl),
        statusPill),
      credText,
      forgetBtn,
      h("div", { class: "button-row" }, logoutBtn, logoutAllBtn)),

    section("自动刷新",
      refreshInfo,
      h("p", { class: "hint" }, "选择每天自动刷新的整点（最多几个见下方说明）。实际开始时间会在该小时内随机几分钟，避开整点高峰。"),
      chips,
      refreshHint,
      h("div", { class: "button-row" }, saveHoursBtn),
      logDetails),

    section("日历订阅",
      h("p", { class: "hint" }, "在 iOS / macOS 日历、Google 日历或 Outlook 里“添加订阅”并填入链接。链接本身就是凭证，泄露后请重新生成。"),
      h("div", { class: "copy-row" }, icsInput, copyBtn),
      h("div", { class: "button-row" }, rotateBtn, disableBtn),
      icsInfo),

    section("外观", themeButtons),

    h("section", { class: "settings-card danger-zone" },
      h("h3", {}, "删除数据"),
      h("p", { class: "hint" }, "立即删除本站保存的登录态、加密密码、课表、刷新记录与订阅链接，不可恢复。"),
      h("div", { class: "button-row" }, deleteBtn)));

  content.setAttribute("aria-labelledby", "settings-title");
  const dialog = openDialog({
    className: "settings-dialog",
    label: "账号与设置",
    content,
    onClose: () => {},
  });

  // ---- 渲染 ----
  function tone() {
    return STATUS[current.status] || { label: current.status || "未知", tone: "warn" };
  }

  function renderAccount() {
    avatar.textContent = initialOf(current.name);
    nameEl.textContent = current.name || "未命名";
    idEl.textContent = `学号 ${current.student_id || "—"}`;
    const meta = tone();
    statusPill.textContent = meta.label;
    statusPill.className = `pill pill-${meta.tone}`;
    credText.textContent = current.cred_mode === "password"
      ? "已保存加密的密码：登录态过期时会自动重新登录。"
      : "只保存登录态，不保存密码。登录态过期后需要你重新登录。";
    forgetBtn.hidden = current.cred_mode !== "password";
  }

  function renderRefreshInfo() {
    refreshInfo.replaceChildren(...[
      ["状态", tone().label],
      ["上次成功", T.formatDateTime(current.last_success_at)],
      ["下次计划", current.next_refresh_at ? T.formatDateTime(current.next_refresh_at) : "已暂停"],
    ].flatMap(([label, value]) => [h("dt", {}, label), h("dd", {}, String(value))]));
  }

  function renderChips() {
    const allowed = Array.isArray(current.refresh_allowed_hours) ? current.refresh_allowed_hours : [];
    const max = current.refresh_max_times ?? 3;
    chips.replaceChildren(...allowed.map((hour) => {
      const selected = hours.has(hour);
      const disabled = !selected && hours.size >= max;
      return h("button", {
        type: "button",
        dataset: { hour: String(hour) },
        "aria-pressed": String(selected),
        disabled,
        title: disabled ? `每天最多选择 ${max} 个时间` : undefined,
        onClick: () => {
          if (selected) hours.delete(hour);
          else if (hours.size < max) hours.add(hour);
          else {
            showToast(`每天最多选择 ${max} 个时间`, { type: "warn" });
            return;
          }
          renderChips();
          updateSaveState();
        },
      }, `${String(hour).padStart(2, "0")}:00`);
    }));
    refreshHint.textContent =
      `每天最多选 ${max} 个整点；不选则约每 ${current.refresh_default_interval_hours ?? 12} 小时刷新一次。`
      + "为减轻学校服务器压力，只开放夜间和午间。";
  }

  function updateSaveState() {
    const original = new Set(Array.isArray(current.refresh_hours) ? current.refresh_hours : []);
    const same = original.size === hours.size && [...hours].every((hour) => original.has(hour));
    saveHoursBtn.disabled = busy || same;
  }

  function renderIcs() {
    icsInput.value = icsUrl || "";
    copyBtn.disabled = !icsUrl;
    rotateBtn.textContent = icsUrl ? "重新生成链接" : "生成链接";
    disableBtn.hidden = !icsUrl;
  }

  function renderTheme() {
    const active = getThemeMode();
    for (const button of themeButtons.querySelectorAll("button")) {
      button.setAttribute("aria-pressed", String(button.dataset.themeMode === active));
    }
  }

  function renderAll() {
    renderAccount();
    renderRefreshInfo();
    renderChips();
    updateSaveState();
    renderIcs();
    renderTheme();
  }

  // ---- 操作 ----
  async function saveHours() {
    if (busy) return;
    busy = true;
    saveHoursBtn.disabled = true;
    saveHoursBtn.textContent = "保存中…";
    try {
      const result = await api.post("api/settings/refresh-hours", { hours: [...hours].sort((a, b) => a - b) });
      current.refresh_hours = [...hours].sort((a, b) => a - b);
      current.next_refresh_at = result.next_refresh_at;
      onUpdated?.(current);
      renderRefreshInfo();
      showToast(hours.size ? `已保存，下次自动刷新约 ${T.formatDateTime(result.next_refresh_at)}` : "已恢复默认刷新频率", { type: "success" });
    } catch (error) {
      showToast(error.message, { type: "error" });
    } finally {
      busy = false;
      saveHoursBtn.textContent = "保存刷新时间";
      updateSaveState();
    }
  }

  async function forgetPassword() {
    if (!await confirmDialog({
      title: "删除已保存的密码？",
      message: "删除后登录态过期需要你手动重新登录。已经保存的登录态不会受影响。",
      confirmText: "删除密码",
      danger: true,
    })) return;
    try {
      await api.post("api/credentials/forget");
      current.cred_mode = "cookie";
      onUpdated?.(current);
      renderAccount();
      showToast("已删除保存的密码", { type: "success" });
    } catch (error) {
      showToast(error.message, { type: "error" });
    }
  }

  async function copyIcs() {
    if (!icsUrl) return;
    try {
      await navigator.clipboard.writeText(icsUrl);
      showToast("订阅链接已复制", { type: "success" });
    } catch {
      icsInput.focus();
      icsInput.select();
      showToast("请手动复制（已选中）", { type: "info" });
    }
  }

  async function rotateIcs() {
    if (icsUrl && !await confirmDialog({
      title: "重新生成订阅链接？",
      message: "旧链接会立即失效，已经订阅的日历需要手动换成新链接。",
      confirmText: "重新生成",
    })) return;
    try {
      const result = await api.post("api/ics/rotate");
      const had = Boolean(icsUrl);
      icsUrl = result.ics_url;
      current.ics_url = icsUrl;
      onUpdated?.(current);
      renderIcs();
      icsInfo.textContent = had ? "已生成新链接，旧链接已失效。" : "链接已生成，复制后在日历 App 中添加订阅。";
      showToast("订阅链接已生成", { type: "success" });
    } catch (error) {
      showToast(error.message, { type: "error" });
    }
  }

  async function disableIcs() {
    if (!await confirmDialog({
      title: "停用日历订阅？",
      message: "当前订阅链接会立即失效。",
      confirmText: "停用",
      danger: true,
    })) return;
    try {
      await api.post("api/ics/disable");
      icsUrl = null;
      current.ics_url = null;
      onUpdated?.(current);
      renderIcs();
      icsInfo.textContent = "订阅已停用。";
      showToast("订阅已停用", { type: "success" });
    } catch (error) {
      showToast(error.message, { type: "error" });
    }
  }

  function logout() {
    dialog.close("signed-out");
    onLogout?.();
  }

  async function logoutAll() {
    if (!await confirmDialog({
      title: "在所有设备上退出？",
      message: "手机、电脑等其他浏览器都需要重新登录。",
      confirmText: "全部退出",
    })) return;
    dialog.close("signed-out");
    onLogoutAll?.();
  }

  async function deleteAccount() {
    if (!await confirmDialog({
      title: "删除账号与所有数据？",
      message: "本站保存的登录态、加密密码、课表、刷新记录和订阅链接都会被立即删除，不可恢复。",
      confirmText: "删除账号",
      danger: true,
    })) return;
    try {
      await api.post("api/account/delete");
    } catch (error) {
      if (error.status !== 401) {
        showToast(error.message, { type: "error" });
        return;
      }
    }
    dialog.close("deleted");
    onAccountDeleted?.();
  }

  async function loadLog() {
    try {
      const rows = await api.get("api/refresh-log");
      if (!Array.isArray(rows) || rows.length === 0) {
        logList.replaceChildren(h("li", { class: "muted" }, "暂无记录"));
        return;
      }
      logList.replaceChildren(...rows.map((row) => h("li", { class: row.ok ? "is-ok" : "is-bad" },
        h("span", { class: "log-dot" }),
        h("span", { class: "log-time" }, T.formatDateTime(row.at)),
        h("span", { class: "log-text" }, `${TRIGGERS[row.trigger] || row.trigger}：${row.message || (row.ok ? "成功" : "失败")}`))));
    } catch (error) {
      logList.replaceChildren(h("li", { class: "muted" }, `记录加载失败：${error.message}`));
    }
  }

  renderAll();
  loadLog();

  return { close: () => dialog.close("close") };
}
