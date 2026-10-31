/** 登录视图：账号密码 / 扫码 / 图形验证码 / 短信二次认证。 */

import { post } from "../api.js";
import { h, on, replace } from "../dom.js";
import { icon, REPO_URL } from "../icons.js";

const INTRO = {
  pwd: "服务器代你登录统一身份认证，只读取课表。密码不会明文落库。",
  qr: "用学校官方 App 扫码并在手机上确认，与统一身份认证页面上的“扫码登录”相同，不涉及密码。",
};

function field(id, labelText, input) {
  return h("div", { class: "field" },
    h("label", { class: "field-label", for: id }, labelText),
    input);
}

function setButtonLabel(button, label) {
  const span = button.querySelector(".btn-label");
  if (span) span.textContent = label;
  else button.textContent = label;
}

function buttonWithIcon(label, iconName, props = {}) {
  return h("button", { type: "button", class: "btn", ...props },
    icon(iconName),
    h("span", { class: "btn-label" }, label));
}

export function mountLogin(root, { onSuccess } = {}) {
  let mode = "pwd";
  let stage = "form";
  let flowId = null;
  let busy = false;
  let pollTimer = 0;
  let pollInFlight = false;
  let smsTimer = 0;
  const cleanups = [];

  const errorEl = h("p", { class: "form-error", role: "alert", "aria-live": "assertive" });
  const gateHint = h("p", { class: "field-hint", id: "agree-hint" }, "勾选上方的同意说明后即可登录。");

  const tabs = h("div", { class: "tabs", role: "tablist", "aria-label": "登录方式" });
  const tabPwd = h("button", {
    type: "button", role: "tab", "data-mode": "pwd", "aria-selected": "true", tabindex: "0",
    "aria-controls": "password-form",
  }, "账号密码");
  const tabQr = h("button", {
    type: "button", role: "tab", "data-mode": "qr", "aria-selected": "false", tabindex: "-1",
    "aria-controls": "qr-panel",
  }, "扫码登录");
  tabs.append(tabPwd, tabQr);

  const introEl = h("p", { class: "auth-intro" }, INTRO.pwd);

  // ---- 账号密码 ----
  const studentInput = h("input", {
    id: "student-id", name: "student_id", type: "text", inputmode: "numeric",
    autocomplete: "username", autocapitalize: "off", spellcheck: "false",
    maxlength: "20", required: "", placeholder: "例如 20230001",
  });
  const passwordInput = h("input", {
    id: "password", name: "password", type: "password", autocomplete: "current-password",
    maxlength: "128", required: "",
  });
  const passwordToggle = h("button", {
    type: "button", class: "input-action", "aria-label": "显示密码", "aria-pressed": "false",
    title: "显示密码",
    onClick: () => {
      const show = passwordInput.type === "password";
      passwordInput.type = show ? "text" : "password";
      passwordToggle.setAttribute("aria-pressed", String(show));
      passwordToggle.setAttribute("aria-label", show ? "隐藏密码" : "显示密码");
      replace(passwordToggle, icon(show ? "eye-off" : "eye"));
      passwordInput.focus();
    },
  }, icon("eye"));
  const passwordWrap = h("div", { class: "input-wrap" }, passwordInput, passwordToggle);

  const rememberInput = h("input", { type: "checkbox", name: "remember_password" });
  const riskNote = h("div", { class: "callout callout-warn", hidden: true },
    "密码会用 AES-256-GCM 加密后保存在服务器上，只在登录态过期、后台刷新需要重新登录时解密使用，"
    + "不写入日志。服务器一旦被攻破仍有泄露风险。不勾选时只保存登录态，过期后需要你回来重新登录。可随时在设置里删除。");

  const passwordForm = h("form", { id: "password-form", class: "auth-form", novalidate: "", autocomplete: "on" },
    field("student-id", "学号", studentInput),
    field("password", "统一身份认证密码", passwordWrap),
    h("label", { class: "check" }, rememberInput,
      h("span", {}, "保存加密的密码，登录态过期后自动续期", h("span", { class: "muted" }, "（可选）"))),
    riskNote);

  // ---- 扫码 ----
  const qrImg = h("img", { alt: "统一身份认证登录二维码", decoding: "async" });
  const qrOverlay = h("div", { class: "qr-overlay", hidden: true });
  const qrFrame = h("div", { class: "qr-frame", hidden: true }, qrImg, qrOverlay);
  const qrStatus = h("p", { class: "qr-status", role: "status", "aria-live": "polite" });
  const qrRefreshBtn = h("button", {
    type: "button", class: "btn btn-ghost btn-sm", hidden: true,
    onClick: () => refreshQr(),
  }, icon("refresh"), h("span", {}, "刷新二维码"));
  const qrPanel = h("div", { class: "qr-panel", id: "qr-panel", hidden: true },
    qrFrame,
    qrStatus,
    h("p", { class: "qr-help muted" }, "打开学校官方 App → 扫一扫 → 在手机上确认登录。"),
    qrRefreshBtn);

  // ---- 共享选项 ----
  const keepLoginInput = h("input", { type: "checkbox", checked: "" });
  const agreeInput = h("input", { type: "checkbox", "aria-describedby": "agree-hint" });
  const options = h("div", { class: "auth-options" },
    h("label", { class: "check" }, keepLoginInput,
      h("span", {}, "保持登录 180 天", h("span", { class: "muted" }, "（公共电脑请取消）"))),
    h("label", { class: "check" }, agreeInput,
      h("span", {}, "我已了解本站为非官方工具，自愿授权本站代我登录并读取课表，并可随时删除账号与全部数据。")),
    gateHint);

  const loginBtn = buttonWithIcon("登录并同步课表", "arrow-right", { type: "submit", form: "password-form" });
  loginBtn.classList.add("btn-primary", "btn-block");
  const qrStartBtn = buttonWithIcon("显示登录二维码", "qr", { hidden: true });
  qrStartBtn.classList.add("btn-primary", "btn-block");
  const actions = h("div", { class: "auth-actions" }, loginBtn, qrStartBtn, errorEl);

  // ---- 验证码步骤 ----
  const captchaImg = h("img", { class: "captcha-img", alt: "图形验证码" });
  const captchaRefreshBtn = h("button", {
    type: "button", class: "btn btn-ghost btn-sm", title: "换一张",
    onClick: () => refreshCaptcha(),
  }, icon("refresh"), h("span", {}, "换一张"));
  const captchaInput = h("input", {
    id: "captcha-code", name: "captcha", type: "text", autocomplete: "off",
    autocapitalize: "off", spellcheck: "false", maxlength: "16", required: "",
    inputmode: "text", placeholder: "输入图中的字符",
  });
  const captchaError = h("p", { class: "form-error", role: "alert", "aria-live": "assertive" });
  const captchaPanel = h("form", { class: "auth-step", hidden: true, novalidate: "" },
    h("div", { class: "step-heading" },
      h("h2", {}, "图形验证码"),
      h("p", { class: "muted" }, "统一身份认证要求输入验证码。验证码只转交给你本人填写，程序不会识别或绕过。")),
    h("div", { class: "captcha-row" }, captchaImg, captchaRefreshBtn),
    field("captcha-code", "验证码", captchaInput),
    h("div", { class: "step-actions" },
      h("button", { type: "submit", class: "btn btn-primary" }, "继续"),
      h("button", { type: "button", class: "btn btn-ghost", onClick: () => backToForm() }, "返回")),
    captchaError);

  // ---- 短信步骤 ----
  const smsPhone = h("strong", {}, "绑定的手机");
  const smsCodeInput = h("input", {
    id: "sms-code", name: "code", type: "text", inputmode: "numeric", autocomplete: "one-time-code",
    maxlength: "10", required: "", placeholder: "短信验证码",
  });
  const smsSendBtn = h("button", {
    type: "button", class: "btn", onClick: () => sendSms(),
  }, "发送验证码");
  const smsTrust = h("input", { type: "checkbox", checked: "" });
  const smsInfo = h("p", { class: "form-hint", role: "status", "aria-live": "polite" });
  const smsError = h("p", { class: "form-error", role: "alert", "aria-live": "assertive" });
  const smsPanel = h("form", { class: "auth-step", hidden: true, novalidate: "" },
    h("div", { class: "step-heading" },
      h("h2", {}, "短信二次认证"),
      h("p", { class: "muted" }, "验证码将发送到 ", smsPhone, "。")),
    h("div", { class: "code-row" }, smsCodeInput, smsSendBtn),
    h("label", { class: "check" }, smsTrust, h("span", {}, "信任这台设备，之后减少短信验证")),
    h("div", { class: "step-actions" },
      h("button", { type: "submit", class: "btn btn-primary" }, "验证并登录"),
      h("button", { type: "button", class: "btn btn-ghost", onClick: () => backToForm() }, "返回")),
    smsInfo,
    smsError);

  // ---- 页面 ----
  const brand = h("div", { class: "brand" },
    h("span", { class: "brand-mark", "aria-hidden": "true" }),
    h("span", { class: "brand-name" }, "南财课表"));

  const card = h("section", { class: "auth-card", "aria-labelledby": "login-title" },
    brand,
    h("div", { class: "auth-heading" },
      h("h1", { id: "login-title" }, "登录"),
      h("p", { class: "muted" }, "用南京财经大学统一身份认证账号登录，自动同步你的课表。")),
    tabs,
    introEl,
    passwordForm,
    qrPanel,
    options,
    actions,
    captchaPanel,
    smsPanel,
    h("details", { class: "auth-note" },
      h("summary", {}, "安全与隐私"),
      h("p", {}, "本站不设独立密码：登录即用学校统一身份认证验证一次。默认只保存加密的登录态 cookie；"
        + "登录态过期后需要你回来重新登录。所有课表数据都可在设置里一键删除。")));

  const footer = h("footer", { class: "auth-footer" },
    h("p", {}, "非官方第三方工具，与南京财经大学无隶属关系。代登录可能受学校信息系统使用条款约束，请自行评估并对授权负责。"),
    h("p", {}, "开源项目：",
      h("a", { href: REPO_URL, target: "_blank", rel: "noopener noreferrer" }, "GitHub"),
      " · GPL-3.0"));

  const page = h("main", { class: "auth-page" }, card, footer);
  root.replaceChildren(page);

  // ---- 状态切换 ----
  function setError(message) {
    errorEl.textContent = message || "";
  }

  function setStepError(node, message) {
    node.textContent = message || "";
  }

  function updateGate() {
    const allowed = agreeInput.checked && !busy;
    loginBtn.disabled = !allowed;
    qrStartBtn.disabled = !allowed;
    gateHint.hidden = agreeInput.checked;
    loginBtn.setAttribute("aria-disabled", String(!allowed));
    qrStartBtn.setAttribute("aria-disabled", String(!allowed));
  }

  function setBusy(next, label) {
    busy = next;
    studentInput.disabled = next;
    passwordInput.disabled = next;
    rememberInput.disabled = next;
    if (label) setButtonLabel(loginBtn, label);
    if (!next) setButtonLabel(loginBtn, "登录并同步课表");
    updateGate();
  }

  function renderVisibility() {
    const formStage = stage === "form";
    tabs.hidden = !formStage;
    introEl.hidden = !formStage;
    passwordForm.hidden = !(formStage && mode === "pwd");
    qrPanel.hidden = !(formStage && mode === "qr");
    options.hidden = !formStage;
    actions.hidden = !formStage;
    loginBtn.hidden = mode !== "pwd";
    qrStartBtn.hidden = !(formStage && mode === "qr" && qrFrame.hidden);
    captchaPanel.hidden = stage !== "captcha";
    smsPanel.hidden = stage !== "mfa";
    if (!formStage) setError("");
  }

  function setStage(next) {
    stage = next;
    renderVisibility();
  }

  function setMode(next, { focus = true } = {}) {
    if (mode !== next) {
      cancelFlow();
      stopPolling();
      resetQr();
      mode = next;
    }
    for (const tab of [tabPwd, tabQr]) {
      const selected = tab.dataset.mode === mode;
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? "0" : "-1";
    }
    introEl.textContent = INTRO[mode];
    setStage("form");
    if (focus) {
      if (mode === "pwd") studentInput.focus();
      else qrStartBtn.focus();
    }
  }

  function backToForm() {
    cancelFlow();
    stopPolling();
    resetQr();
    clearInterval(smsTimer);
    smsTimer = 0;
    setButtonLabel(smsSendBtn, "发送验证码");
    smsSendBtn.disabled = false;
    setStepError(captchaError, "");
    setStepError(smsError, "");
    smsInfo.textContent = "";
    setStage("form");
    if (mode === "pwd") studentInput.focus();
    else qrStartBtn.focus();
  }

  function cancelFlow() {
    const id = flowId;
    flowId = null;
    stopPolling();
    if (id) post("api/login/cancel", { flow_id: id }).catch(() => {});
  }

  // ---- 登录结果 ----
  async function handleResult(result) {
    if (!result) {
      setError("服务器返回了空响应，请重试");
      return;
    }
    if (result.ok) {
      flowId = null;
      cleanup();
      onSuccess?.(result.csrf);
      return;
    }
    if (result.need === "qr") {
      flowId = result.flow_id;
      renderQr(result);
      return;
    }
    if (result.need === "captcha") {
      flowId = result.flow_id;
      setStage("captcha");
      if (result.captcha_image) captchaImg.src = result.captcha_image;
      captchaInput.value = "";
      setStepError(captchaError, result.error || "");
      captchaInput.focus();
      return;
    }
    if (result.need === "mfa") {
      flowId = result.flow_id;
      stopPolling();
      setStage("mfa");
      smsPhone.textContent = result.phone || "绑定的手机";
      smsCodeInput.value = "";
      setStepError(smsError, result.error || "");
      smsCodeInput.focus();
      return;
    }
    flowId = result.flow_id || null;
    setError(result.error || "登录流程出现未知状态，请返回重试");
  }

  // ---- 账号密码 ----
  on(passwordForm, "submit", async (event) => {
    event.preventDefault();
    if (busy) return;
    setError("");
    const studentId = studentInput.value.trim();
    if (!/^[0-9A-Za-z]{4,20}$/.test(studentId)) {
      setError("请输入 4–20 位学号");
      studentInput.focus();
      return;
    }
    if (!passwordInput.value) {
      setError("请输入统一身份认证密码");
      passwordInput.focus();
      return;
    }
    if (!agreeInput.checked) {
      setError("请先勾选上方的使用说明");
      agreeInput.focus();
      return;
    }
    setBusy(true, "正在登录统一身份认证…");
    try {
      const result = await post("api/login", {
        student_id: studentId,
        password: passwordInput.value,
        remember_password: rememberInput.checked,
        keep_login: keepLoginInput.checked,
        agree_terms: true,
      });
      passwordInput.value = "";
      await handleResult(result);
    } catch (error) {
      passwordInput.value = "";
      setError(error.message);
    } finally {
      setBusy(false);
    }
  });

  on(rememberInput, "change", () => {
    riskNote.hidden = !rememberInput.checked;
  });
  on(agreeInput, "change", updateGate);

  // ---- 验证码 ----
  on(captchaPanel, "submit", async (event) => {
    event.preventDefault();
    const code = captchaInput.value.trim();
    setStepError(captchaError, "");
    if (!code) {
      setStepError(captchaError, "请输入验证码");
      captchaInput.focus();
      return;
    }
    if (!flowId) {
      setStepError(captchaError, "登录流程已过期，请返回重新登录");
      return;
    }
    const submit = captchaPanel.querySelector('button[type="submit"]');
    submit.disabled = true;
    try {
      await handleResult(await post("api/login/captcha", { flow_id: flowId, captcha: code }));
    } catch (error) {
      setStepError(captchaError, error.message);
      if (/过期|过多/.test(error.message)) {
        window.setTimeout(() => backToForm(), 1600);
      }
    } finally {
      submit.disabled = false;
    }
  });

  async function refreshCaptcha() {
    if (!flowId) return;
    captchaRefreshBtn.disabled = true;
    try {
      const result = await post("api/login/captcha-image", { flow_id: flowId });
      captchaImg.src = result.captcha_image;
      captchaInput.value = "";
      captchaInput.focus();
      setStepError(captchaError, "");
    } catch (error) {
      setStepError(captchaError, error.message);
    } finally {
      captchaRefreshBtn.disabled = false;
    }
  }

  // ---- 短信 ----
  function startSmsCooldown(seconds) {
    const total = Math.max(30, Math.ceil(seconds) || 60);
    let left = total;
    clearInterval(smsTimer);
    smsSendBtn.disabled = true;
    setButtonLabel(smsSendBtn, `${left} 秒后重发`);
    smsTimer = window.setInterval(() => {
      left -= 1;
      if (left <= 0) {
        clearInterval(smsTimer);
        smsTimer = 0;
        smsSendBtn.disabled = false;
        setButtonLabel(smsSendBtn, "重新发送");
      } else {
        setButtonLabel(smsSendBtn, `${left} 秒后重发`);
      }
    }, 1000);
  }

  async function sendSms() {
    if (!flowId) {
      setStepError(smsError, "登录流程已过期，请返回重新登录");
      return;
    }
    smsSendBtn.disabled = true;
    setStepError(smsError, "");
    smsInfo.textContent = "正在发送…";
    try {
      const result = await post("api/login/sms/send", { flow_id: flowId });
      smsInfo.textContent = result.message || "验证码已发送";
      startSmsCooldown(result.retry_after);
      smsCodeInput.focus();
    } catch (error) {
      smsInfo.textContent = "";
      setStepError(smsError, error.message);
      smsSendBtn.disabled = false;
    }
  }

  on(smsPanel, "submit", async (event) => {
    event.preventDefault();
    const code = smsCodeInput.value.trim();
    setStepError(smsError, "");
    if (code.length < 4) {
      setStepError(smsError, "请输入短信验证码");
      smsCodeInput.focus();
      return;
    }
    if (!flowId) {
      setStepError(smsError, "登录流程已过期，请返回重新登录");
      return;
    }
    const submit = smsPanel.querySelector('button[type="submit"]');
    submit.disabled = true;
    try {
      await handleResult(await post("api/login/sms/verify", {
        flow_id: flowId,
        code,
        trust_device: smsTrust.checked,
      }));
    } catch (error) {
      setStepError(smsError, error.message);
      if (/过期|过多/.test(error.message)) {
        window.setTimeout(() => backToForm(), 1600);
      }
    } finally {
      submit.disabled = false;
    }
  });

  // ---- 扫码 ----
  function resetQr() {
    stopPolling();
    qrFrame.hidden = true;
    qrOverlay.hidden = true;
    qrRefreshBtn.hidden = true;
    qrStatus.textContent = "";
    qrImg.removeAttribute("src");
    renderVisibility();
  }

  function renderQr(result) {
    qrFrame.hidden = false;
    if (result.qr_image) qrImg.src = result.qr_image;
    if (result.error) setError(result.error);
    const status = result.qr_status || "waiting";
    qrOverlay.hidden = status === "waiting";
    qrRefreshBtn.hidden = status !== "expired";
    if (status === "scanned") {
      qrOverlay.textContent = "已扫描，请在手机上确认";
      qrStatus.textContent = "等待手机确认…";
    } else if (status === "expired") {
      qrOverlay.textContent = "二维码已失效";
      qrStatus.textContent = "点击“刷新二维码”重新获取。";
    } else {
      qrStatus.textContent = "请用学校官方 App 扫描二维码";
    }
    renderVisibility();
    if (status !== "expired" && status !== "scanned") schedulePoll(2000);
    else if (status === "scanned") schedulePoll(2000);
  }

  function stopPolling() {
    window.clearTimeout(pollTimer);
    pollTimer = 0;
  }

  function schedulePoll(delay) {
    stopPolling();
    if (!flowId || mode !== "qr" || qrFrame.hidden || stage !== "form") return;
    if (document.visibilityState === "hidden") return;
    pollTimer = window.setTimeout(pollQr, delay);
  }

  async function pollQr() {
    pollTimer = 0;
    if (!flowId || pollInFlight || mode !== "qr" || qrFrame.hidden || stage !== "form") return;
    pollInFlight = true;
    try {
      const result = await post("api/login/qr/poll", { flow_id: flowId });
      await handleResult(result);
    } catch (error) {
      if (!flowId) return;
      setError(error.message);
      if (/过期|不存在|流程已/.test(error.message)) {
        flowId = null;
        qrOverlay.hidden = false;
        qrOverlay.textContent = "二维码已失效";
        qrStatus.textContent = "点击“刷新二维码”重新获取。";
        qrRefreshBtn.hidden = false;
        return;
      }
      schedulePoll(4000);
    } finally {
      pollInFlight = false;
    }
  }

  async function startQr() {
    if (!agreeInput.checked) {
      setError("请先勾选上方的使用说明");
      agreeInput.focus();
      return;
    }
    setError("");
    qrStartBtn.disabled = true;
    setButtonLabel(qrStartBtn, "正在获取二维码…");
    try {
      await handleResult(await post("api/login/qr/start", {
        agree_terms: true,
        keep_login: keepLoginInput.checked,
      }));
    } catch (error) {
      setError(error.message);
    } finally {
      setButtonLabel(qrStartBtn, "显示登录二维码");
      updateGate();
    }
  }

  async function refreshQr() {
    if (!flowId) {
      await startQr();
      return;
    }
    qrRefreshBtn.disabled = true;
    setError("");
    try {
      const result = await post("api/login/qr/refresh", { flow_id: flowId });
      resetQr();
      await handleResult(result);
    } catch (error) {
      setError(error.message);
      if (/过期|过多/.test(error.message)) {
        flowId = null;
        resetQr();
      }
    } finally {
      qrRefreshBtn.disabled = false;
    }
  }

  // ---- 事件绑定 ----
  tabPwd.addEventListener("click", () => setMode("pwd"));
  tabQr.addEventListener("click", () => setMode("qr"));
  on(tabs, "keydown", (event) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const next = mode === "pwd" ? "qr" : "pwd";
    setMode(next, { focus: false });
    (next === "pwd" ? tabPwd : tabQr).focus();
  });
  qrStartBtn.addEventListener("click", startQr);

  const onVisibility = () => {
    if (document.visibilityState === "visible" && mode === "qr" && flowId && !qrFrame.hidden && stage === "form") {
      schedulePoll(250);
    }
  };
  document.addEventListener("visibilitychange", onVisibility);
  cleanups.push(() => document.removeEventListener("visibilitychange", onVisibility));

  function cleanup() {
    cancelFlow();
    stopPolling();
    clearInterval(smsTimer);
    smsTimer = 0;
    for (const fn of cleanups) fn();
  }

  setMode("pwd", { focus: false });
  renderVisibility();
  updateGate();
  if (window.matchMedia("(pointer: fine)").matches) studentInput.focus();

  return { destroy: cleanup };
}
