/** 服务器管理员页：概况 / 排期 / 刷新记录 / 日志。只用 dom.js 构建节点（CSP 开了 Trusted Types）。 */
import { h, replace } from "../js/dom.js";

const POLL_MS = 10_000;
const app = document.getElementById("app");
let csrf = null;
let tab = "overview";
let timer = null;
let logSeq = 0;
let logFollow = true;
const logFilter = { level: "", q: "" };

// ---------- 请求 ----------

class HttpError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function api(path, { method = "GET", body } = {}) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && csrf) headers["X-CSRF-Token"] = csrf;
  const resp = await fetch(path, {
    method, headers, credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data = null;
  try { data = await resp.json(); } catch { /* 空响应 */ }
  if (!resp.ok) {
    if (resp.status === 401 && path !== "api/admin/login") showLogin();
    throw new HttpError((data && typeof data.detail === "string" && data.detail) || `请求失败（${resp.status}）`, resp.status);
  }
  return data;
}

// ---------- 格式化 ----------

const pad = (n) => String(n).padStart(2, "0");

function fmtTime(ts, withDate = true) {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  const t = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  return withDate ? `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${t}` : t;
}

function fmtRel(ts) {
  if (!ts) return "—";
  const diff = ts - Date.now() / 1000;
  const abs = Math.abs(diff);
  let s;
  if (abs < 60) s = `${Math.round(abs)} 秒`;
  else if (abs < 3600) s = `${Math.round(abs / 60)} 分钟`;
  else if (abs < 86400) s = `${(abs / 3600).toFixed(1)} 小时`;
  else s = `${(abs / 86400).toFixed(1)} 天`;
  return diff >= 0 ? `${s}后` : `${s}前`;
}

function fmtDuration(sec) {
  const d = Math.floor(sec / 86400);
  const hr = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  return d ? `${d} 天 ${hr} 小时` : hr ? `${hr} 小时 ${m} 分` : `${m} 分`;
}

const STATUS_PILL = {
  ok: ["正常", "pill-ok"],
  error: ["出错退避", "pill-warn"],
  captcha: ["需验证码", "pill-danger"],
  needs_rebind: ["需重新登录", "pill-danger"],
};

function statusPill(status) {
  const [label, cls] = STATUS_PILL[status] || [status, ""];
  return h("span", { class: `pill ${cls}` }, label);
}

const tile = (label, value, sub) => h("div", { class: "adm-tile" },
  h("div", { class: "adm-tile-label" }, label),
  h("div", { class: "adm-tile-value" }, String(value)),
  sub ? h("div", { class: "adm-tile-sub" }, sub) : null);

function table(head, rows, empty = "暂无数据") {
  if (!rows.length) return h("div", { class: "adm-table-wrap" }, h("div", { class: "adm-empty" }, empty));
  return h("div", { class: "adm-table-wrap" },
    h("table", { class: "adm-table" },
      h("thead", h("tr", head.map((c) => h("th", c)))),
      h("tbody", rows)));
}

// ---------- 登录 ----------

function showLogin() {
  stopPolling();
  csrf = null;
  const error = h("p", { class: "form-error", attrs: { role: "alert" } });
  const input = h("input", { type: "password", autocomplete: "current-password", required: true, attrs: { "aria-label": "管理员密码" } });
  const button = h("button", { class: "btn btn-primary btn-block", type: "submit" }, "登录");
  const form = h("form", { class: "adm-login", onsubmit: async (e) => {
    e.preventDefault();
    button.disabled = true;
    error.textContent = "";
    try {
      csrf = (await api("api/admin/login", { method: "POST", body: { password: input.value } })).csrf;
      showDashboard();
    } catch (err) {
      error.textContent = err.message;
      input.select();
    } finally {
      button.disabled = false;
    }
  } },
  h("h1", "服务器管理"),
  h("label", { class: "field" }, h("span", { class: "field-label" }, "管理员密码"), input),
  error, button);
  replace(app, form);
  input.focus();
}

// ---------- 主界面 ----------

const TABS = [["overview", "概况"], ["schedule", "排期计划"], ["refreshes", "刷新记录"], ["logs", "服务器日志"]];
let body;
let stamp;

function showDashboard() {
  body = h("div");
  stamp = h("span", { class: "muted" });
  const tabs = h("div", { class: "tabs", attrs: { role: "tablist" } }, TABS.map(([key, label]) =>
    h("button", { type: "button", attrs: { role: "tab", "aria-selected": String(key === tab) }, onclick: (e) => {
      tab = key;
      for (const b of tabs.children) b.setAttribute("aria-selected", String(b === e.currentTarget));
      logSeq = 0;
      load();
    } }, label)));
  const logout = h("button", { class: "btn btn-sm", type: "button", onclick: async () => {
    try { await api("api/admin/logout", { method: "POST" }); } catch { /* 已失效也回登录页 */ }
    showLogin();
  } }, "退出");
  const refresh = h("button", { class: "btn btn-sm", type: "button", onclick: () => load() }, "刷新");
  replace(app, h("div", { class: "adm" },
    h("header", { class: "adm-head" }, h("h1", "服务器管理"), stamp, refresh, logout),
    tabs, body));
  load();
  startPolling();
}

function startPolling() {
  stopPolling();
  timer = window.setInterval(() => { if (!document.hidden) load(true); }, POLL_MS);
}

function stopPolling() {
  if (timer) window.clearInterval(timer);
  timer = null;
}

let loading = false;

async function load(background = false) {
  if (loading) return;
  loading = true;
  const current = tab;
  try {
    if (current === "overview") renderOverview(await api("api/admin/overview"));
    else if (current === "schedule") renderSchedule(await api("api/admin/schedule"));
    else if (current === "refreshes") renderRefreshes(await api("api/admin/refreshes"));
    else await loadLogs(background);
    stamp.textContent = `更新于 ${fmtTime(Date.now() / 1000, false)} · 每 ${POLL_MS / 1000} 秒自动刷新`;
  } catch (err) {
    if (err.status !== 401) stamp.textContent = `加载失败：${err.message}`;
  } finally {
    loading = false;
  }
}

function renderOverview(o) {
  if (tab !== "overview") return;
  const on = o.online;
  const via = Object.entries(on.by_via).map(([k, v]) => `${{ web: "网页", widget: "小组件", ics: "日历" }[k] || k} ${v}`).join(" · ");
  const st = o.user_status;
  const r = o.refresh_24h;
  const sum = (suffix) => Object.entries(r).filter(([k]) => k.endsWith(suffix)).reduce((a, [, v]) => a + v, 0);
  const triggers = ["scheduled", "manual", "login"].map((t) =>
    `${{ scheduled: "后台", manual: "手动", login: "登录" }[t]} ${r[`${t}_ok`] || 0}/${(r[`${t}_ok`] || 0) + (r[`${t}_fail`] || 0)}`).join(" · ");
  replace(body,
    h("div", { class: "adm-section-title" }, `在线（${Math.round(on.window_seconds / 60)} 分钟内有请求）`),
    h("div", { class: "adm-tiles" },
      tile("在线用户", on.users, via || "—"),
      tile("访客（不同 IP）", on.visitors, "含未登录"),
      tile("1 小时内活跃", on.active_1h),
      tile("24 小时内活跃", on.active_24h),
      tile("7 天内活跃", on.active_7d),
      tile("有效登录会话", on.sessions)),
    h("div", { class: "adm-section-title" }, "用户与刷新"),
    h("div", { class: "adm-tiles" },
      tile("注册用户", o.users, `正常 ${st.ok || 0} · 退避 ${st.error || 0} · 暂停 ${(st.captcha || 0) + (st.needs_rebind || 0)}`),
      tile("24 小时刷新", sum("_ok") + sum("_fail"), `成功 ${sum("_ok")} · 失败 ${sum("_fail")}`),
      tile("日历订阅", o.ics_subscribers),
      tile("小组件", o.widget_users),
      tile("邮件通知", o.email_users)),
    h("div", { class: "adm-section-title" }, "服务器"),
    h("dl", { class: "adm-kv" },
      h("dt", "已运行"), h("dd", `${fmtDuration(o.now - o.started_at)}（启动于 ${fmtTime(o.started_at)}）`),
      h("dt", "24 小时刷新成功/总数"), h("dd", triggers),
      //使用条件分支处理max_rss_mb为None的情况,防止undefined行为
      h("dd", `PID ${o.pid}${o.max_rss_mb == null ? "" : ` · 峰值内存 ${o.max_rss_mb} MB`}`),
      h("dt", "数据库"), h("dd", `${o.db_mb} MB`),
      h("dt", "Python"), h("dd", `${o.python} · ${o.platform}`)));
}

function renderSchedule(s) {
  if (tab !== "schedule") return;
  const sc = s.scheduler;
  const threads = Object.entries(sc.alive).map(([k, v]) => `${k} ${v ? "运行中" : "已停止"}`).join(" · ") || "未启动";
  const lt = sc.last_tick;
  const rows = s.items.map((i) => h("tr",
    h("td", { class: "mono" }, i.student, i.name ? ` ${i.name}` : ""),
    h("td", statusPill(i.status), i.fail_count ? h("span", { class: "muted" }, ` ×${i.fail_count}`) : null),
    h("td", { class: "num" }, i.score !== null ? h("span", { class: "pill pill-warn" }, `排队中 ${i.score}`)
      : i.next_refresh_at ? `${fmtTime(i.next_refresh_at)}（${fmtRel(i.next_refresh_at)}）` : "暂停"),
    h("td", { class: "wrap" }, i.pending_reason ? `${i.reason}（${i.pending_reason}）` : i.reason,
      i.last_error && i.status !== "ok" ? h("div", `上次错误：${i.last_error}`) : null),
    h("td", { class: "num" }, `${i.target_minutes} 分钟`),
    h("td", { class: "num" }, fmtRel(i.last_success_at)),
    h("td", { class: "num" }, i.next_class_at ? fmtTime(i.next_class_at) : "—"),
    h("td", { class: "num" }, fmtRel(i.last_seen_at)),
    h("td", i.status === "ok" && i.score === null
      ? h("button", { class: "btn btn-sm", type: "button", onclick: async (e) => {
        e.currentTarget.disabled = true;
        try { await api("api/admin/request-refresh", { method: "POST", body: { uid: i.uid } }); load(); } catch (err) { window.alert(err.message); }
      } }, "排队刷新") : null)));
  replace(body,
    h("dl", { class: "adm-kv" },
      h("dt", "后台刷新"), h("dd", sc.enabled ? threads : "已在配置中关闭（SCHEDULER_ENABLED=false）"),
      h("dt", "预算"), h("dd", `${sc.budget_rps} 次/秒（上限 ${sc.max_rps}）· 令牌 ${sc.tokens} · 每 ${sc.tick_seconds} 秒一轮`),
      h("dt", "学校健康"), h("dd", sc.upstream_cooldown > 0 ? `暂停中，还剩 ${sc.upstream_cooldown} 秒`
        : sc.upstream_penalty > 0 ? `额外间隔 ${sc.upstream_penalty} 秒` : "正常"),
      h("dt", "上一轮"), h("dd", lt ? `${fmtTime(lt.at)}：到期 ${lt.due} 人，刷新 ${lt.refreshed} 人` : "还没跑过"),
      h("dt", "当前排队"), h("dd", `${s.due} 人`)),
    h("div", { class: "adm-section-title" }, `每个人的下次刷新（共 ${s.items.length} 人）`),
    table(["用户", "状态", "下次刷新", "原因", "目标间隔", "上次成功", "下一节课", "最近使用", ""], rows, "还没有用户"));
}

const TRIGGER = { scheduled: "后台", manual: "手动", login: "登录" };

function renderRefreshes(list) {
  if (tab !== "refreshes") return;
  replace(body, table(["时间", "用户", "触发", "结果", "说明"], list.map((r) => h("tr",
    h("td", { class: "num" }, fmtTime(r.at)),
    h("td", { class: "mono" }, r.student),
    h("td", TRIGGER[r.trigger] || r.trigger),
    h("td", h("span", { class: `pill ${r.ok ? "pill-ok" : "pill-danger"}` }, r.ok ? "成功" : "失败")),
    h("td", { class: "wrap" }, r.message || ""))), "还没有刷新记录"));
}

// ---------- 日志 ----------

let logBox = null;

function logLine(r) {
  return h("div", { class: `adm-log-line lv-${r.level}` },
    h("time", fmtTime(r.at)), h("span", { class: "lg" }, `${r.level[0]} ${r.logger || "root"}`), r.message);
}

function buildLogView() {
  const level = h("select", { class: "select", attrs: { "aria-label": "级别" }, onchange: (e) => {
    logFilter.level = e.target.value; logSeq = 0; load();
  } }, [["", "全部级别"], ["WARNING", "警告及以上"], ["ERROR", "仅错误"]].map(([v, l]) =>
    h("option", { value: v, selected: v === logFilter.level }, l)));
  let debounce;
  const search = h("input", { type: "search", placeholder: "搜索日志…", value: logFilter.q, oninput: (e) => {
    window.clearTimeout(debounce);
    debounce = window.setTimeout(() => { logFilter.q = e.target.value.trim(); logSeq = 0; load(); }, 300);
  } });
  const follow = h("label", { class: "check" },
    h("input", { type: "checkbox", checked: logFollow, onchange: (e) => { logFollow = e.target.checked; } }), "跟随最新");
  logBox = h("div", { class: "adm-log", attrs: { role: "log", "aria-live": "off" } });
  replace(body, h("div", { class: "adm-toolbar" }, level, search, follow),
    logBox, h("p", { class: "hint" }, "进程内只保留最近的日志，服务重启后清空；更早的请在服务器上运行 journalctl -u kebiao 查看。"));
}

async function loadLogs(background) {
  if (!background || !logBox || !body.contains(logBox) || logSeq === 0) {
    if (!logBox || !body.contains(logBox)) buildLogView();
    logSeq = 0;
  }
  const params = new URLSearchParams({ after: String(logSeq), limit: "1000" });
  if (logFilter.level) params.set("level", logFilter.level);
  if (logFilter.q) params.set("q", logFilter.q);
  const data = await api(`api/admin/logs?${params}`);
  if (tab !== "logs") return;
  if (logSeq === 0) logBox.replaceChildren();
  logBox.append(...data.items.map(logLine));
  while (logBox.childElementCount > 2000) logBox.firstElementChild.remove();
  if (!logBox.childElementCount) logBox.append(h("div", { class: "adm-empty" }, "没有匹配的日志"));
  else logBox.querySelector(".adm-empty")?.remove();
  logSeq = data.seq;
  if (logFollow) logBox.scrollTop = logBox.scrollHeight;
}

// ---------- 启动 ----------

(async () => {
  try {
    csrf = (await api("api/admin/me")).csrf;
    showDashboard();
  } catch {
    showLogin();
  }
})();
