/** 设置里的“邮件通知”：绑定邮箱（验证码确认）、提醒开关、测试邮件、解绑。 */

import { api } from "../api.js";
import { h } from "../dom.js";
import { showToast } from "../components/toast.js";
import { confirmDialog } from "../components/dialog.js";

export function createEmailSection({ mail, onChange } = {}) {
  let state = { ...mail };
  let resendTimer = 0;

  const status = h("p", { class: "hint", role: "status", "aria-live": "polite" });

  // 未绑定：填邮箱 → 发验证码 → 填验证码
  const emailInput = h("input", {
    type: "email", autocomplete: "email", inputmode: "email", maxlength: "254",
    placeholder: "你的邮箱", "aria-label": "通知邮箱",
  });
  const sendBtn = h("button", { type: "button", class: "btn", onClick: () => sendCode() }, "发送验证码");
  const codeInput = h("input", {
    type: "text", inputmode: "numeric", autocomplete: "one-time-code", maxlength: "6",
    placeholder: "6 位验证码", "aria-label": "邮箱验证码",
  });
  const verifyBtn = h("button", { type: "button", class: "btn btn-primary", onClick: () => verify() }, "验证");
  const codeRow = h("div", { class: "code-row", hidden: true }, codeInput, verifyBtn);
  const bindBox = h("div", { class: "email-bind" },
    h("div", { class: "code-row" }, emailInput, sendBtn),
    codeRow);

  // 已绑定：邮箱 + 开关 + 测试 / 解绑
  const boundEmail = h("strong", { class: "email-address" });
  const statusToggle = h("input", { type: "checkbox", onChange: () => savePrefs() });
  const changesToggle = h("input", { type: "checkbox", onChange: () => savePrefs() });
  const testBtn = h("button", { type: "button", class: "btn btn-sm", onClick: () => sendTest() }, "发送测试邮件");
  const removeBtn = h("button", { type: "button", class: "btn btn-sm btn-ghost", onClick: () => removeEmail() }, "解绑邮箱");
  const boundBox = h("div", { class: "email-bound" },
    h("p", {}, "通知发送到 ", boundEmail),
    h("label", { class: "check" }, statusToggle,
      h("span", {}, "自动刷新暂停或连续失败时提醒我", h("span", { class: "muted" }, "（需要重新登录、验证码等）"))),
    h("label", { class: "check" }, changesToggle,
      h("span", {}, "后台刷新发现课表变动时提醒我", h("span", { class: "muted" }, "（增删课、改时间 / 地点）"))),
    h("div", { class: "button-row" }, testBtn, removeBtn));

  const el = h("div", { class: "email-settings" },
    h("p", { class: "hint" }, "绑定邮箱后，即使没打开网站，也能在自动刷新暂停或课表变动时收到提醒。邮箱加密保存，只用于这两类通知。"),
    bindBox,
    boundBox,
    status);

  function render() {
    const bound = Boolean(state.email);
    bindBox.hidden = bound;
    boundBox.hidden = !bound;
    boundEmail.textContent = state.email || "";
    statusToggle.checked = Boolean(state.notify_status);
    changesToggle.checked = Boolean(state.notify_changes);
    if (!bound && state.pending_email) {
      if (!emailInput.value) emailInput.value = state.pending_email;
      codeRow.hidden = false;
    }
  }

  function apply(next) {
    state = { ...state, ...next };
    onChange?.(state);
    render();
  }

  function startResendCooldown(seconds) {
    let left = Math.max(1, Math.ceil(seconds) || 60);
    clearInterval(resendTimer);
    sendBtn.disabled = true;
    sendBtn.textContent = `${left} 秒后重发`;
    resendTimer = window.setInterval(() => {
      left -= 1;
      if (left <= 0) {
        clearInterval(resendTimer);
        resendTimer = 0;
        sendBtn.disabled = false;
        sendBtn.textContent = "重新发送";
      } else {
        sendBtn.textContent = `${left} 秒后重发`;
      }
    }, 1000);
  }

  async function sendCode() {
    const email = emailInput.value.trim();
    if (!email || !emailInput.checkValidity()) {
      status.textContent = "请输入正确的邮箱地址";
      emailInput.focus();
      return;
    }
    sendBtn.disabled = true;
    status.textContent = "正在发送…";
    try {
      const result = await api.post("api/email/start", { email });
      state.pending_email = result.pending_email;
      codeRow.hidden = false;
      codeInput.value = "";
      codeInput.focus();
      status.textContent = `验证码已发送到 ${result.pending_email}，10 分钟内有效。没收到请看看垃圾箱。`;
      startResendCooldown(result.retry_after);
    } catch (error) {
      status.textContent = error.message;
      sendBtn.disabled = false;
    }
  }

  async function verify() {
    const code = codeInput.value.trim();
    if (!/^\d{6}$/.test(code)) {
      status.textContent = "请输入邮件里的 6 位验证码";
      codeInput.focus();
      return;
    }
    verifyBtn.disabled = true;
    try {
      const result = await api.post("api/email/verify", { code });
      clearInterval(resendTimer);
      resendTimer = 0;
      sendBtn.disabled = false;
      sendBtn.textContent = "发送验证码";
      codeRow.hidden = true;
      emailInput.value = "";
      status.textContent = "";
      apply(result);
      showToast("邮箱已绑定", { type: "success" });
    } catch (error) {
      status.textContent = error.message;
    } finally {
      verifyBtn.disabled = false;
    }
  }

  async function savePrefs() {
    const body = { notify_status: statusToggle.checked, notify_changes: changesToggle.checked };
    try {
      apply(await api.post("api/email/prefs", body));
      showToast("提醒设置已保存", { type: "success" });
    } catch (error) {
      render();
      showToast(error.message, { type: "error" });
    }
  }

  async function sendTest() {
    testBtn.disabled = true;
    try {
      await api.post("api/email/test");
      status.textContent = "测试邮件已发出，几分钟内没收到请检查垃圾箱。";
    } catch (error) {
      status.textContent = error.message;
    } finally {
      testBtn.disabled = false;
    }
  }

  async function removeEmail() {
    if (!await confirmDialog({
      title: "解绑通知邮箱？",
      message: "解绑后不再发送任何邮件，邮箱地址会从本站删除。",
      confirmText: "解绑",
      danger: true,
    })) return;
    try {
      apply(await api.post("api/email/remove"));
      status.textContent = "";
      showToast("已解绑邮箱", { type: "success" });
    } catch (error) {
      showToast(error.message, { type: "error" });
    }
  }

  render();
  return {
    el,
    destroy() {
      clearInterval(resendTimer);
    },
  };
}
