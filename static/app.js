"use strict";

/* =========================================================
 * 工具
 * ======================================================= */
const $ = (id) => document.getElementById(id);
const DOW = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];
const DOW_SHORT = ["一", "二", "三", "四", "五", "六", "日"];
const PALETTE = ["#3b5bdb", "#0c8599", "#2b8a3e", "#e8590c", "#ae3ec9", "#1971c2", "#c2255c", "#5c940d", "#f08c00", "#6741d9", "#d9480f", "#087f5b"];

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null && c !== false) node.append(c);
  return node;
}

const ICONS = {
  "chevron-left": "M15 18l-6-6 6-6",
  "chevron-right": "M9 18l6-6-6-6",
  refresh: "M21 12a9 9 0 1 1-2.64-6.36M21 4v5h-5",
  x: "M18 6L6 18M6 6l12 12",
  copy: "M9 9h10v10H9zM5 15V5h10",
  logout: "M15 4h4v16h-4M10 8l-4 4 4 4M6 12h10",
  pin: "M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.8-7 11-7 11zM12 12.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z",
  user: "M20 21a8 8 0 0 0-16 0M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8z",
};
function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  for (const [k, v] of Object.entries({ viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": "2",
    "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true" })) svg.setAttribute(k, v);
  const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
  p.setAttribute("d", ICONS[name]);
  svg.append(p);
  return svg;
}
// 静态 HTML 里用 data-icon 声明的图标
for (const b of document.querySelectorAll("[data-icon]")) b.prepend(icon(b.dataset.icon));

function parseDate(iso) { const [y, m, d] = iso.split("-").map(Number); return new Date(y, m - 1, d); }
function addDays(d, n) { const x = new Date(d); x.setDate(x.getDate() + n); return x; }
function startOfDay(d) { return new Date(d.getFullYear(), d.getMonth(), d.getDate()); }
function sameDay(a, b) { return !!a && !!b && startOfDay(a).getTime() === startOfDay(b).getTime(); }
function dowIndex(d) { return (d.getDay() + 6) % 7; }
function md(d) { return `${d.getMonth() + 1}/${d.getDate()}`; }
function toMin(s) { if (!s) return null; const [h, m] = s.split(":").map(Number); return h * 60 + m; }
function hm(min) { return `${Math.floor(min / 60)}:${String(min % 60).padStart(2, "0")}`; }
function nowMin() { const d = new Date(); return d.getHours() * 60 + d.getMinutes(); }
function fmtTime(ts) {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  return `${md(d)} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}
function relTime(ts) {
  if (!ts) return "从未";
  const s = Date.now() / 1000 - ts;
  if (s < 60) return "刚刚";
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`;
  return fmtTime(ts);
}
function colorFor(name) {
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.codePointAt(0)) >>> 0;
  return PALETTE[h % PALETTE.length];
}
// 头像显示名字：三个字及以上取后两个字（常见的“名”），否则原样
function initial(name) { const n = (name || "").trim(); return !n ? "我" : n.length >= 3 ? n.slice(-2) : n; }
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* 隐私模式等：只是记不住视图偏好 */ } },
};

/* =========================================================
 * API
 * ======================================================= */
const state = { me: null, tt: null, week: 1, maxWeek: 16, semId: null, view: "week", day: 0 };

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json" };
  if (state.me) headers["X-CSRF-Token"] = state.me.csrf;
  const res = await fetch(path, { credentials: "same-origin", ...opts, headers });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(typeof body.detail === "string" ? body.detail : `请求失败（${res.status}）`);
    err.status = res.status;
    throw err;
  }
  return body;
}
const post = (path, data) => api(path, { method: "POST", body: JSON.stringify(data || {}) });

/* =========================================================
 * 提示 / 确认框（不用原生 confirm：部分内置浏览器会直接屏蔽）
 * ======================================================= */
function ask(message, okText = "确定", danger = false) {
  const dlg = $("confirmDlg");
  $("confirmMsg").textContent = message;
  $("confirmOk").textContent = okText;
  $("confirmOk").className = danger ? "btn danger" : "btn primary";
  dlg.returnValue = "";
  dlg.showModal();
  return new Promise((resolve) => dlg.addEventListener("close", () => resolve(dlg.returnValue === "ok"), { once: true }));
}
let toastTimer;
function toast(message) {
  const t = $("toast");
  t.textContent = message;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 3600);
}
// 点遮罩关闭
for (const d of [$("detail"), $("settings")]) {
  d.addEventListener("click", (e) => { if (e.target === d) d.close(); });
}

/* =========================================================
 * 视图切换 / 启动
 * ======================================================= */
function show(view) {
  $("viewLogin").hidden = view !== "login";
  $("viewTable").hidden = view !== "table";
  $("btnSettings").hidden = view !== "table";
  if (view === "login") showLoginStep("form");
}

async function boot() {
  try {
    state.me = await api("api/me");
  } catch {
    state.me = null;
    show("login");
    return;
  }
  renderUser();
  show("table");
  renderBanner();
  fillSemesters();
  await loadTimetable(state.semId || state.me.current_semester_id, true);
}

function renderUser() {
  $("whoName").textContent = state.me.name || "已登录";
  $("whoId").textContent = state.me.student_id;
  $("avatar").textContent = initial(state.me.name);
}

/* =========================================================
 * 登录：账号密码 / 扫码 / 图形验证码 / 短信
 * ======================================================= */
let loginMode = "pwd";
let flowId = null;
const INTRO = {
  pwd: "服务器代你登录统一身份认证（authserver.nufe.edu.cn），只读取课表。",
  qr: "用学校官方 App 扫码并在手机上确认，与统一身份认证页面上的“扫码登录”相同。不需要也不会保存密码。",
};

function loginOptions() {
  if (!$("agreeTerms").checked) throw new Error("请先阅读并勾选同意说明");
  return { agree_terms: true, keep_login: $("keepLogin").checked };
}

$("loginTabs").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-mode]");
  if (!b || b.dataset.mode === loginMode) return;
  loginMode = b.dataset.mode;
  for (const t of $("loginTabs").children) t.setAttribute("aria-selected", String(t.dataset.mode === loginMode));
  stopQr();
  flowId = null;
  showLoginStep("form");
});

function showLoginStep(step) {
  const first = step === "form";
  const qr = step === "qr" || (first && loginMode === "qr");
  $("loginTabs").hidden = !(first || step === "qr");
  $("loginIntro").hidden = !(first || step === "qr");
  $("loginIntro").textContent = INTRO[loginMode];
  $("loginForm").hidden = !(first && loginMode === "pwd");
  $("pwdActions").hidden = !(first && loginMode === "pwd");
  $("loginOptions").hidden = !first;
  $("qrPanel").hidden = !qr;
  $("captchaForm").hidden = step !== "captcha";
  $("smsForm").hidden = step !== "mfa";
  if (first && loginMode === "qr") resetQr();
}

$("loginForm").addEventListener("change", (e) => {
  if (e.target.name === "remember_password") $("riskNote").hidden = !e.target.checked;
});
$("loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const err = $("loginError");
  err.textContent = "";
  const sid = f.student_id.value.trim();
  if (!/^[0-9A-Za-z]{4,20}$/.test(sid)) { err.textContent = "请输入正确的学号"; f.student_id.focus(); return; }
  if (!f.password.value) { err.textContent = "请输入密码"; f.password.focus(); return; }
  let opts;
  try { opts = loginOptions(); } catch (x) { err.textContent = x.message; return; }
  const btn = $("btnLogin");
  btn.disabled = true;
  btn.textContent = "正在登录统一身份认证…";
  try {
    const r = await post("api/login", {
      student_id: sid, password: f.password.value, remember_password: f.remember_password.checked, ...opts,
    });
    f.password.value = "";
    await handleLoginStep(r);
  } catch (x) {
    err.textContent = x.message;
  } finally {
    btn.disabled = false;
    btn.textContent = "登录并获取课表";
  }
});

async function handleLoginStep(r) {
  if (r.ok) {
    flowId = null;
    stopQr();
    state.me = null;
    await boot();
    return;
  }
  flowId = r.flow_id;
  showLoginStep(r.need);
  if (r.need === "qr") { renderQr(r); return; }
  stopQr();
  const form = r.need === "captcha" ? $("captchaForm") : $("smsForm");
  form.querySelector("[data-error]").textContent = r.error || "";
  if (r.need === "captcha") {
    $("captchaImg").src = r.captcha_image;
    form.captcha.value = "";
    form.captcha.focus();
  } else {
    $("smsPhone").textContent = r.phone || "绑定的手机";
    form.code.value = "";
  }
}

async function stepCall(form, path, data) {
  const btn = form.querySelector("button[type=submit]");
  form.querySelector("[data-error]").textContent = "";
  btn.disabled = true;
  try {
    await handleLoginStep(await post(path, { flow_id: flowId, ...data }));
  } catch (x) {
    form.querySelector("[data-error]").textContent = x.message;
    if (/过期|过多/.test(x.message)) setTimeout(() => { flowId = null; showLoginStep("form"); }, 1600);
  } finally {
    btn.disabled = false;
  }
}

$("captchaForm").addEventListener("submit", (e) => {
  e.preventDefault();
  stepCall(e.target, "api/login/captcha", { captcha: e.target.captcha.value.trim() });
});
$("btnCaptchaRefresh").addEventListener("click", async () => {
  try {
    $("captchaImg").src = (await post("api/login/captcha-image", { flow_id: flowId })).captcha_image;
  } catch (x) {
    $("captchaForm").querySelector("[data-error]").textContent = x.message;
  }
});

let smsTimer;
$("btnSmsSend").addEventListener("click", async () => {
  const btn = $("btnSmsSend");
  btn.disabled = true;
  try {
    $("smsInfo").textContent = (await post("api/login/sms/send", { flow_id: flowId })).message;
    let left = 60;
    clearInterval(smsTimer);
    smsTimer = setInterval(() => {
      left -= 1;
      btn.textContent = `${left} 秒后重发`;
      if (left <= 0) { clearInterval(smsTimer); btn.disabled = false; btn.textContent = "重新发送"; }
    }, 1000);
    $("smsForm").code.focus();
  } catch (x) {
    $("smsForm").querySelector("[data-error]").textContent = x.message;
    btn.disabled = false;
  }
});
$("smsForm").addEventListener("submit", (e) => {
  e.preventDefault();
  stepCall(e.target, "api/login/sms/verify", { code: e.target.code.value.trim(), trust_device: e.target.trust_device.checked });
});
for (const b of document.querySelectorAll("[data-cancel]")) {
  b.addEventListener("click", () => { flowId = null; stopQr(); showLoginStep("form"); });
}

/* ---- 扫码 ---- */
let qrTimer = null;
function stopQr() { clearTimeout(qrTimer); qrTimer = null; }
function resetQr() {
  $("qrBox").hidden = true;
  $("qrOverlay").hidden = true;
  $("btnQrStart").hidden = false;
  $("btnQrRefresh").hidden = true;
  $("qrStatus").textContent = "";
  $("qrError").textContent = "";
}
function renderQr(r) {
  $("btnQrStart").hidden = true;
  $("qrBox").hidden = false;
  if (r.qr_image) $("qrImg").src = r.qr_image;
  $("qrError").textContent = r.error || "";
  const s = r.qr_status;
  $("btnQrRefresh").hidden = s !== "expired";
  $("qrOverlay").hidden = s !== "scanned" && s !== "expired";
  if (s === "scanned") {
    $("qrOverlay").textContent = "已扫描，请在手机上确认登录";
    $("qrStatus").textContent = "等待手机确认…";
  } else if (s === "expired") {
    $("qrOverlay").textContent = "二维码已失效";
    $("qrStatus").textContent = "点击“刷新二维码”重新获取";
  } else {
    $("qrStatus").textContent = "请用学校官方 App 扫描二维码";
  }
  stopQr();
  if (s !== "expired") qrTimer = setTimeout(pollQr, 2000);
}
async function pollQr() {
  const id = flowId;
  if (!id || $("qrPanel").hidden) return;
  try {
    const r = await post("api/login/qr/poll", { flow_id: id });
    if (id === flowId) await handleLoginStep(r);
  } catch (x) {
    if (id !== flowId) return;
    stopQr();
    if (/过期/.test(x.message)) { flowId = null; resetQr(); } else { $("btnQrRefresh").hidden = false; }
    $("qrError").textContent = x.message;
  }
}
$("btnQrStart").addEventListener("click", async () => {
  const btn = $("btnQrStart");
  $("qrError").textContent = "";
  let opts;
  try { opts = loginOptions(); } catch (x) { $("qrError").textContent = x.message; return; }
  btn.disabled = true;
  btn.textContent = "正在获取二维码…";
  try {
    await handleLoginStep(await post("api/login/qr/start", opts));
  } catch (x) {
    $("qrError").textContent = x.message;
  } finally {
    btn.disabled = false;
    btn.textContent = "显示二维码";
  }
});
$("btnQrRefresh").addEventListener("click", async () => {
  if (!flowId) { resetQr(); return; }
  try {
    await handleLoginStep(await post("api/login/qr/refresh", { flow_id: flowId }));
  } catch (x) {
    $("qrError").textContent = x.message;
    flowId = null;
    resetQr();
  }
});

/* =========================================================
 * 状态横幅 / 学期 / 课表加载
 * ======================================================= */
function renderBanner() {
  const me = state.me;
  const b = $("statusBanner");
  if (me.status === "ok") { b.hidden = true; return; }
  const text = {
    needs_rebind: `需要重新登录：${me.last_error || "登录态已过期"}`,
    captcha: me.last_error || "统一身份认证要求验证码，已暂停自动刷新。",
    error: me.last_error || "上次刷新失败，稍后会自动重试。",
  }[me.status] || me.status;
  b.hidden = false;
  b.className = "banner" + (me.status === "error" && me.fail_count < 3 ? "" : " err");
  $("bannerText").textContent = text;
  $("bannerAction").hidden = !["needs_rebind", "captcha"].includes(me.status);
  $("bannerAction").textContent = "重新登录";
}
$("bannerAction").addEventListener("click", () => signOut("api/logout"));

function fillSemesters() {
  const sel = $("semSelect");
  const sems = [...state.me.semesters].sort((a, b) => b.id - a.id);
  if (!sems.length && state.me.current_semester_id) sems.push({ id: state.me.current_semester_id, name: "本学期" });
  sel.replaceChildren(...sems.map((s) => el("option", {
    value: s.id, text: s.name + (s.id === state.me.current_semester_id ? "（当前）" : ""),
  })));
  sel.value = state.semId || state.me.current_semester_id;
}
$("semSelect").addEventListener("change", (e) => loadTimetable(Number(e.target.value), true));

function weekOf(date) {
  if (!state.tt?.week1_monday) return null;
  return Math.floor((startOfDay(date) - parseDate(state.tt.week1_monday)) / 86400000 / 7) + 1;
}
function clampWeek(w) { return Math.min(Math.max(w, 1), state.maxWeek); }
function weekMonday(w) { return state.tt?.week1_monday ? addDays(parseDate(state.tt.week1_monday), (w - 1) * 7) : null; }

function jumpToToday() {
  state.week = clampWeek(weekOf(new Date()) || 1);
  const mon = weekMonday(state.week);
  const today = new Date();
  state.day = mon && sameDay(addDays(mon, dowIndex(today)), today) ? dowIndex(today) : 0;
}

async function loadTimetable(semId, jump) {
  state.semId = semId;
  try {
    applyTimetable(await api(`api/timetable?semester_id=${semId}`), jump);
  } catch (x) {
    if (x.status !== 404) { toast(x.message); return; }
    state.tt = null;
    render();
  }
}
function applyTimetable(tt, jump) {
  state.tt = tt;
  if (tt) {
    state.maxWeek = Math.max(16, ...tt.activities.flatMap((a) => a.weekIndexes));
    if (jump) jumpToToday();
  }
  render();
}

/* ---- 周 / 日切换、翻周 ---- */
function setView(v, persist = true) {
  state.view = v;
  for (const b of $("viewMode").children) b.setAttribute("aria-selected", String(b.dataset.view === v));
  if (persist) store.set("kb_view", v);
  if (state.tt !== null || persist) render();
}
$("viewMode").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-view]");
  if (b) setView(b.dataset.view);
});
$("prevWeek").addEventListener("click", () => { state.week = clampWeek(state.week - 1); render(); });
$("nextWeek").addEventListener("click", () => { state.week = clampWeek(state.week + 1); render(); });
$("weekLabel").addEventListener("click", () => { if (state.tt) { jumpToToday(); render(); } });
document.addEventListener("keydown", (e) => {
  if ($("viewTable").hidden || document.querySelector("dialog[open]") || /INPUT|SELECT|TEXTAREA/.test(e.target.tagName)) return;
  if (e.key === "ArrowLeft") $("prevWeek").click();
  if (e.key === "ArrowRight") $("nextWeek").click();
});

/* =========================================================
 * 渲染
 * ======================================================= */
function lessonsOfWeek(week) {
  const acts = state.tt.activities;
  // 从有时间的课推出每节的起止时间，给缺时间的课兜底
  const s = {}, e = {};
  for (const a of acts) {
    if (a.startTime && !(a.startUnit in s)) s[a.startUnit] = toMin(a.startTime);
    if (a.endTime && !(a.endUnit in e)) e[a.endUnit] = toMin(a.endTime);
  }
  const out = [];
  for (const a of acts) {
    if (!a.weekIndexes.includes(week)) continue;
    let start = toMin(a.startTime) ?? s[a.startUnit];
    let end = toMin(a.endTime) ?? e[a.endUnit];
    if (start == null) start = 480 + (a.startUnit - 1) * 55;  // 最后兜底：8:00 起每节 55 分钟
    if (end == null || end <= start) end = start + (a.endUnit - a.startUnit + 1) * 45;
    out.push({ a, start, end, day: a.weekday - 1, color: colorFor(a.courseName) });
  }
  return out.sort((x, y) => x.day - y.day || x.start - y.start);
}

function showEmpty(title, text) {
  $("emptyNote").hidden = false;
  $("emptyNote").replaceChildren(el("strong", { text: title }), text);
}

function render() {
  const tt = state.tt;
  $("emptyNote").hidden = true;
  if (!tt) {
    $("weekView").hidden = true;
    $("dayView").hidden = true;
    $("weekTitle").textContent = "—";
    $("weekRange").textContent = "";
    $("metaLine").textContent = "";
    if (state.me) showEmpty("这个学期还没有课表", "点击右上角的刷新按钮，从教务系统获取。");
    return;
  }
  const week = state.week;
  const mon = weekMonday(week);
  const curWeek = tt.semester_id === state.me.current_semester_id ? weekOf(new Date()) : null;
  $("weekTitle").textContent = `第 ${week} 周`;
  $("weekRange").textContent = mon ? `${md(mon)} – ${md(addDays(mon, 6))}` : "未配置校历";
  $("weekLabel").classList.toggle("current", curWeek === week);
  $("prevWeek").disabled = week <= 1;
  $("nextWeek").disabled = week >= state.maxWeek;

  const parts = [tt.semester_name, `${relTime(tt.fetched_at)}更新`];
  if (state.me.next_refresh_at) parts.push(`下次自动刷新 ${fmtTime(state.me.next_refresh_at)}`);
  if (!tt.week1_monday) parts.push("未配置该学期第 1 周的日期");
  $("metaLine").textContent = parts.join(" · ");

  const items = lessonsOfWeek(week);
  if (state.view === "day") {
    $("weekView").hidden = true;
    $("dayView").hidden = false;
    renderDay(items, mon);
  } else {
    $("dayView").hidden = true;
    $("weekView").hidden = !items.length;
    if (items.length) renderWeek(items, mon);
    else showEmpty("这周没有课", "好好休息，或者切换到其他周看看。");
  }
}

/* ---- 周视图 ---- */
function layoutLanes(items) {
  // 重叠的课分道并排：按开始时间扫描成簇，簇内贪心分配车道
  items.sort((a, b) => a.start - b.start || b.end - a.end);
  let cluster = [], clusterEnd = -1;
  const flush = () => {
    const lanes = [];
    for (const it of cluster) {
      let i = lanes.findIndex((end) => end <= it.start);
      if (i < 0) { i = lanes.length; lanes.push(0); }
      lanes[i] = it.end;
      it.lane = i;
    }
    for (const it of cluster) it.lanes = lanes.length;
    cluster = [];
  };
  for (const it of items) {
    if (it.start >= clusterEnd && cluster.length) flush();
    cluster.push(it);
    clusterEnd = Math.max(clusterEnd, it.end);
  }
  if (cluster.length) flush();
  return items;
}

function renderWeek(items, mon) {
  const grid = $("grid");
  grid.replaceChildren();
  const days = items.some((i) => i.day >= 5) ? 7 : 5;  // 周末没课就不占列
  grid.style.setProperty("--days", days);

  let lo = 8 * 60, hi = 18 * 60;
  for (const i of items) { lo = Math.min(lo, i.start); hi = Math.max(hi, i.end); }
  lo = Math.floor(lo / 60) * 60;
  hi = Math.ceil(hi / 60) * 60;
  const ppm = window.innerWidth < 680 ? 0.9 : 1.15;
  const height = (hi - lo) * ppm;
  const y = (m) => (m - lo) * ppm;

  const today = new Date();
  const todayCol = mon ? Math.round((startOfDay(today) - mon) / 86400000) : -1;
  const now = nowMin();
  const showNow = todayCol >= 0 && todayCol < days && now >= lo && now <= hi;

  grid.append(el("div", { class: "corner" }));
  for (let d = 0; d < days; d++) {
    const date = mon ? addDays(mon, d) : null;
    grid.append(el("div", { class: "head" + (d === todayCol ? " today" : "") },
      el("span", { class: "dow", text: DOW[d] }),
      date ? el("span", { class: "date", text: date.getDate() }) : null));
  }

  const gutter = el("div", { class: "gutter" });
  gutter.style.height = `${height}px`;
  for (let m = lo; m <= hi; m += 60) {
    if (showNow && Math.abs(m - now) < 14) continue;  // 给当前时间标签让位
    const t = el("div", { class: "tick", text: hm(m) });
    t.style.top = `${Math.max(y(m), 8)}px`;
    gutter.append(t);
  }
  if (showNow) {
    const tag = el("div", { class: "nowtag", text: hm(now), title: "当前时间" });
    tag.style.top = `${y(now)}px`;
    gutter.append(tag);
  }
  grid.append(gutter);

  const byDay = Array.from({ length: days }, () => []);
  for (const it of items) if (it.day < days) byDay[it.day].push(it);

  for (let d = 0; d < days; d++) {
    const col = el("div", { class: "col" + (d === todayCol ? " today" : "") });
    col.style.height = `${height}px`;
    for (let m = lo + 30; m < hi; m += 30) {
      const l = el("div", { class: "hline" + (m % 60 ? " half" : "") });
      l.style.top = `${y(m)}px`;
      col.append(l);
    }
    if (d === todayCol && showNow) {
      // 画在课程色块下面，不压住文字
      const nl = el("div", { class: "nowline" });
      nl.style.top = `${y(now)}px`;
      col.append(nl);
    }
    for (const it of layoutLanes(byDay[d])) {
      const live = d === todayCol && showNow && it.start <= now && now < it.end;
      col.append(blockEl(it, y, ppm, live));
    }
    grid.append(col);
  }
}

function blockEl(it, y, ppm, live) {
  const a = it.a;
  const h = (it.end - it.start) * ppm - 3;
  const w = 100 / it.lanes;
  const b = el("button", {
    type: "button",
    class: "block" + (live ? " ongoing" : "") + (h < 64 ? " short" : ""),
    title: `${a.courseName}\n${hm(it.start)}–${hm(it.end)}  ${a.room || ""}\n${a.teachers.join("、")}`,
    onclick: () => openDetail(it),
  },
  live ? el("span", { class: "badge live", text: "上课中" }) : null,
  el("span", { class: "t", text: a.courseName }),
  a.room || a.building ? el("span", { class: "s", text: a.room || a.building }) : null,
  a.teachers.length ? el("span", { class: "s teacher", text: a.teachers.join("、") }) : null);
  b.style.setProperty("--c", it.color);
  b.style.top = `${y(it.start) + 1.5}px`;
  b.style.height = `${h}px`;
  b.style.left = `calc(${it.lane * w}% + 3px)`;
  b.style.width = `calc(${w}% - 6px)`;
  return b;
}

/* ---- 日视图 ---- */
function renderDay(items, mon) {
  const today = new Date();
  $("dayStrip").replaceChildren(...DOW_SHORT.map((label, d) => {
    const date = mon ? addDays(mon, d) : null;
    return el("button", {
      type: "button", role: "tab",
      class: "day" + (items.some((i) => i.day === d) ? " has" : "") + (sameDay(date, today) ? " today" : ""),
      "aria-selected": String(d === state.day),
      "aria-label": `${DOW[d]}${date ? ` ${md(date)}` : ""}`,
      onclick: () => { state.day = d; render(); },
    }, el("span", { class: "dow", text: label }), el("span", { class: "date", text: date ? date.getDate() : "" }));
  }));

  const list = $("dayList");
  const date = mon ? addDays(mon, state.day) : null;
  const isToday = sameDay(date, today);
  const now = nowMin();
  const dayItems = items.filter((i) => i.day === state.day);
  if (!dayItems.length) {
    list.replaceChildren(el("li", { class: "empty" }, el("strong", { text: `${DOW[state.day]}没有课` }), "去做点自己喜欢的事吧。"));
    return;
  }
  const nextIdx = isToday ? dayItems.findIndex((i) => i.start > now) : -1;
  list.replaceChildren(...dayItems.map((it, idx) => {
    const a = it.a;
    const live = isToday && it.start <= now && now < it.end;
    const past = isToday && it.end <= now;
    let progress = null;
    if (live) {
      const bar = el("i");
      bar.style.width = `${Math.round(((now - it.start) / (it.end - it.start)) * 100)}%`;
      progress = el("div", { class: "progress" }, bar);
    }
    const where = [a.campus, a.room || a.building].filter(Boolean).join(" · ");
    const li = el("li", {
      class: "lesson" + (live ? " ongoing" : "") + (past ? " past" : ""),
      tabindex: "0", role: "button",
      onclick: () => openDetail(it),
      onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openDetail(it); } },
    },
    el("div", { class: "when" }, el("b", { text: hm(it.start) }), el("span", { text: hm(it.end) })),
    el("div", { class: "what" },
      el("div", { class: "title" },
        el("strong", { text: a.courseName }),
        live ? el("span", { class: "badge live", text: "上课中" }) : null,
        idx === nextIdx && !live ? el("span", { class: "badge next", text: "下一节" }) : null),
      el("div", { class: "sub" },
        where ? el("span", {}, icon("pin"), where) : null,
        a.teachers.length ? el("span", {}, icon("user"), a.teachers.join("、")) : null,
        el("span", { text: `第 ${a.startUnit}–${a.endUnit} 节` })),
      progress));
    li.style.setProperty("--c", it.color);
    return li;
  }));
}

// 日视图左右滑动切换日期（跨周时自动翻周）
(() => {
  let x0 = null, y0 = null;
  const view = $("dayView");
  view.addEventListener("touchstart", (e) => { x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; }, { passive: true });
  view.addEventListener("touchend", (e) => {
    if (x0 == null) return;
    const dx = e.changedTouches[0].clientX - x0, dy = e.changedTouches[0].clientY - y0;
    x0 = null;
    if (Math.abs(dx) < 60 || Math.abs(dx) < Math.abs(dy) * 1.5) return;
    if (dx < 0) {
      if (state.day < 6) state.day += 1; else if (state.week < state.maxWeek) { state.week += 1; state.day = 0; }
    } else if (state.day > 0) state.day -= 1; else if (state.week > 1) { state.week -= 1; state.day = 6; }
    render();
  }, { passive: true });
})();

/* ---- 详情 ---- */
function openDetail(it) {
  const a = it.a;
  $("dTitle").textContent = a.courseName;
  $("dSwatch").style.background = it.color;
  const rows = [
    ["时间", `${DOW[a.weekday - 1]} ${hm(it.start)}–${hm(it.end)}（第 ${a.startUnit}–${a.endUnit} 节）`],
    ["周次", a.weeksStr ? `${a.weeksStr} 周` : ""],
    ["地点", [a.campus, a.building, a.room].filter(Boolean).join(" · ")],
    ["教师", a.teachers.join("、")],
    ["学分", a.credits ?? ""],
  ];
  $("dBody").replaceChildren(...rows.filter(([, v]) => v !== "" && v != null)
    .flatMap(([k, v]) => [el("dt", { text: k }), el("dd", { text: String(v) })]));
  $("detail").showModal();
}

let resizeTimer;
window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { if (state.tt) render(); }, 150); });
// 每分钟重绘一次，让“当前时间”和“上课中”跟着走
setInterval(() => { if (!$("viewTable").hidden && state.tt && !document.hidden) render(); }, 60_000);

/* =========================================================
 * 刷新
 * ======================================================= */
$("btnRefresh").addEventListener("click", async () => {
  const btn = $("btnRefresh");
  if (btn.disabled) return;
  btn.disabled = true;
  btn.classList.add("spinning");
  try {
    const r = await post("api/refresh", { semester_id: state.semId });
    if (r.account) state.me = { ...state.me, ...r.account };
    renderUser();
    renderBanner();
    fillSemesters();
    if (r.timetable) applyTimetable(r.timetable, !state.tt);
    else render();
    toast(r.ok === false ? r.message : "课表已更新");
  } catch (x) {
    toast(x.message);
  } finally {
    btn.disabled = false;
    btn.classList.remove("spinning");
  }
});

/* =========================================================
 * 设置
 * ======================================================= */
$("btnSettings").addEventListener("click", openSettings);

async function openSettings() {
  try {
    state.me = await api("api/me");
  } catch (x) {
    toast(x.message);
    return;
  }
  const me = state.me;
  $("sAvatar").textContent = initial(me.name);
  $("sName").textContent = me.name || "未命名";
  $("sId").textContent = `学号 ${me.student_id}`;
  $("credMode").textContent = me.cred_mode === "password"
    ? "已保存加密的密码：登录态过期时会自动重新登录。"
    : "只保存登录态，不保存密码。登录态过期后需要你重新登录。";
  $("btnForget").hidden = me.cred_mode !== "password";
  renderRefreshInfo(me);
  renderHours(me);
  renderIcs(me.ics_url);
  $("icsInfo").textContent = "";
  $("refreshLog").replaceChildren();
  $("settings").showModal();
  try {
    const log = await api("api/refresh-log");
    const triggers = { login: "登录", manual: "手动", scheduled: "定时" };
    $("refreshLog").replaceChildren(...(log.length ? log.map((r) => el("li", { class: r.ok ? "" : "bad" },
      el("span", { class: "dot" }),
      el("span", { text: `${fmtTime(r.at)} · ${triggers[r.trigger] || r.trigger} · ${r.message || ""}` })))
      : [el("li", { text: "暂无记录" })]));
  } catch { /* 记录拿不到不影响其他设置 */ }
}

function renderRefreshInfo(me) {
  const statusText = { ok: "正常", error: "最近一次失败，稍后重试", captcha: "已暂停（需要验证码）", needs_rebind: "已暂停（需重新登录）" };
  const rows = [
    ["状态", statusText[me.status] || me.status],
    ["上次成功", fmtTime(me.last_success_at)],
    ["下次计划", me.next_refresh_at ? fmtTime(me.next_refresh_at) : "已暂停"],
  ];
  $("refreshInfo").replaceChildren(...rows.flatMap(([k, v]) => [el("dt", { text: k }), el("dd", { text: v })]));
}

function renderHours(me) {
  const box = $("refreshHours");
  box.replaceChildren(...me.refresh_allowed_hours.map((h) => {
    const b = el("button", {
      type: "button", "aria-pressed": String(me.refresh_hours.includes(h)), "data-hour": h,
      text: `${String(h).padStart(2, "0")}:00`,
    });
    b.addEventListener("click", () => {
      const on = b.getAttribute("aria-pressed") !== "true";
      if (on && box.querySelectorAll('[aria-pressed="true"]').length >= me.refresh_max_times) {
        toast(`每天最多选 ${me.refresh_max_times} 个时间`);
        return;
      }
      b.setAttribute("aria-pressed", String(on));
    });
    return b;
  }));
  $("refreshHint").textContent =
    `选择每天自动刷新的时间（最多 ${me.refresh_max_times} 个，会在该小时内随机几分钟开始）。` +
    `不选则约每 ${me.refresh_default_interval_hours} 小时一次。为减轻学校服务器压力，只开放夜间和午间。`;
}

$("btnSaveHours").addEventListener("click", async () => {
  const hours = [...$("refreshHours").querySelectorAll('[aria-pressed="true"]')].map((b) => Number(b.dataset.hour));
  try {
    const r = await post("api/settings/refresh-hours", { hours });
    state.me.refresh_hours = hours;
    state.me.next_refresh_at = r.next_refresh_at;
    renderRefreshInfo(state.me);
    toast(hours.length ? `已保存，下次自动刷新约 ${fmtTime(r.next_refresh_at)}` : "已恢复默认刷新频率");
    if (state.tt) render();
  } catch (x) {
    toast(x.message);
  }
});

function renderIcs(url) {
  $("icsUrl").value = url || "";
  $("btnIcs").textContent = url ? "重新生成链接" : "生成链接";
  $("btnIcsOff").hidden = !url;
  $("btnCopyIcs").disabled = !url;
}
$("btnIcs").addEventListener("click", async () => {
  const had = !!$("icsUrl").value;
  if (had && !await ask("重新生成后旧链接立即失效，已订阅的日历需要换成新链接。继续？", "重新生成")) return;
  try {
    renderIcs((await post("api/ics/rotate")).ics_url);
    $("icsInfo").textContent = had ? "已生成新链接，旧链接已失效。" : "链接已生成，复制后在日历 App 中“添加订阅”。";
  } catch (x) {
    toast(x.message);
  }
});
$("btnIcsOff").addEventListener("click", async () => {
  if (!await ask("停用后当前订阅链接立即失效。继续？", "停用")) return;
  try {
    await post("api/ics/disable");
    renderIcs(null);
    $("icsInfo").textContent = "订阅已停用。";
  } catch (x) {
    toast(x.message);
  }
});
$("btnCopyIcs").addEventListener("click", async () => {
  const v = $("icsUrl").value;
  if (!v) return;
  try {
    await navigator.clipboard.writeText(v);
    toast("已复制订阅链接");
  } catch {
    $("icsUrl").select();
    toast("请手动复制（已选中）");
  }
});

$("btnForget").addEventListener("click", async () => {
  if (!await ask("删除已保存的密码？之后登录态过期需要你手动重新登录。", "删除")) return;
  try {
    await post("api/credentials/forget");
    toast("已删除保存的密码");
    openSettings();
  } catch (x) {
    toast(x.message);
  }
});

async function signOut(path, message) {
  await post(path).catch(() => {});
  if ($("settings").open) $("settings").close();
  state.me = null; state.tt = null;
  show("login");
  if (message) toast(message);
}
$("btnLogout").addEventListener("click", () => signOut("api/logout"));
$("btnLogoutAll").addEventListener("click", async () => {
  if (!await ask("在所有设备上退出登录？手机、电脑等其他浏览器都需要重新登录。", "全部退出")) return;
  signOut("api/logout-all", "已在所有设备上退出");
});
$("btnDelete").addEventListener("click", async () => {
  if (!await ask("确定删除账号？本站保存的你的登录态、加密密码、课表、订阅链接都会被立即删除，不可恢复。", "删除账号", true)) return;
  try {
    await post("api/account/delete");
  } catch (x) {
    toast(x.message);
    return;
  }
  // 账号已删除：会话随之失效，不必再请求退出
  if ($("settings").open) $("settings").close();
  state.me = null; state.tt = null;
  show("login");
  toast("已删除你的账号与全部数据。");
});

/* =========================================================
 * 启动
 * ======================================================= */
setView(store.get("kb_view") || (window.matchMedia("(max-width: 680px)").matches ? "day" : "week"), false);
boot();
