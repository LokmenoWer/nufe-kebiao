/** 服务器管理员页：概况 / 排期 / 手机与安卓 / 刷新记录 / 日志。只用 dom.js 构建节点（CSP 开了 Trusted Types）。 */
import { showToast } from "../js/components/toast.js";
import { h, replace } from "../js/dom.js";
import { icon } from "../js/icons.js";

const POLL_MS = 10_000;
const app = document.getElementById("app");
let csrf = null;
let timer = null;

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

const fmtInterval = (min) => (min < 60 ? `${min} 分钟` : min < 1440 ? `${+(min / 60).toFixed(1)} 小时` : `${+(min / 1440).toFixed(1)} 天`);
const fmtSize = (bytes) => (bytes ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : "—");

const VIA = { web: "网页", widget: "iOS 小组件", mobile: "安卓 App", ics: "日历" };
const TRIGGER = { widget: "手机代抓", assist: "App 协助", scheduled: "后台", manual: "手动", login: "登录" };
const RELOAD_TRIGGER = { sighup: "SIGHUP 信号", admin: "后台手动", watch: "文件监听", manual: "手动" };
const STATUS_PILL = {
  ok: ["正常", "pill-ok"],
  error: ["出错退避", "pill-warn"],
  captcha: ["需验证码", "pill-danger"],
  needs_rebind: ["需重新登录", "pill-danger"],
};

// ---------- 小部件 ----------

const pill = (label, cls = "", title) => h("span", { class: `pill ${cls}`, title }, label);

function statusPill(status) {
  const [label, cls] = STATUS_PILL[status] || [status, ""];
  return pill(label, cls);
}

const tile = (label, value, sub) => h("div", { class: "adm-tile" },
  h("div", { class: "adm-tile-label" }, label),
  h("div", { class: "adm-tile-value" }, String(value)),
  sub ? h("div", { class: "adm-tile-sub" }, sub) : null);

const tiles = (...items) => h("div", { class: "adm-tiles" }, items);
const section = (title) => h("h2", { class: "adm-section-title" }, title);

/** 单元格带列名：窄屏上表格变成卡片，列名由 CSS 从 data-label 取。 */
const td = (label, cls, ...children) => h("td", { class: cls, attrs: { "data-label": label } }, children);

function table(head, rows, empty = "暂无数据") {
  if (!rows.length) return h("div", { class: "adm-table-wrap" }, h("div", { class: "adm-empty" }, empty));
  return h("div", { class: "adm-table-wrap" },
    h("table", { class: "adm-table" },
      h("thead", h("tr", head.map((c) => h("th", c)))),
      h("tbody", rows)));
}

function kv(...pairs) {
  return h("dl", { class: "adm-kv" }, pairs.map(([k, v]) => [h("dt", k), h("dd", v)]));
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

// ---------- 概况 ----------

function attention(o) {
  const out = [];
  const sc = o.scheduler;
  const st = o.user_status;
  if (!sc.enabled) out.push(["warn", "后台刷新已在配置中关闭（SCHEDULER_ENABLED=false）"]);
  else if (!sc.alive["refresh-scheduler"]) out.push(["danger", "后台刷新线程未运行，请重启服务"]);
  if (sc.upstream_cooldown > 0) out.push(["danger", `学校服务器要求暂停请求，剩余 ${sc.upstream_cooldown} 秒`]);
  else if (sc.upstream_penalty > 0) out.push(["warn", `学校服务器响应异常，请求已附加 ${sc.upstream_penalty} 秒退避间隔`]);
  const paused = (st.captcha || 0) + (st.needs_rebind || 0);
  if (paused) out.push(["warn", `${paused} 名用户的自动刷新已暂停，等待用户重新登录`]);
  if (st.error) out.push(["warn", `${st.error} 名用户刷新出错，正处于退避重试阶段`]);
  if (o.phone.failing) out.push(["warn", `${o.phone.failing} 名用户的手机代抓近期失败，已转由服务器兜底刷新`]);
  if (o.release_sync.failing) out.push(["warn", `同步 GitHub Releases 失败（${o.release_sync.last_error}），当前返回已缓存的旧列表`]);
  if (o.reload && o.reload.needs_restart) {
    out.push(["warn", `热重载中途失败（${o.reload.last_reload_error}），模块可能新旧混杂，请尽快重启服务`]);
  } else if (o.reload && o.reload.last_reload_error) {
    out.push(["warn", `最近一次热重载失败（${o.reload.last_reload_error}），当前仍以第 ${o.reload.generation} 代代码运行`]);
  } else if (o.reload && !o.reload.in_sync) {
    out.push(["warn", "磁盘代码与当前运行版本不一致，请执行热重载或重启服务以生效"]);
  }
  return out;
}

function attentionBox(o) {
  const items = attention(o);
  if (!items.length) {
    return h("div", { class: "adm-alert adm-alert-ok", attrs: { role: "status" } }, icon("check"), "系统运行正常，暂无待处理事项");
  }
  return h("div", { class: "adm-alerts", attrs: { role: "status" } }, items.map(([kind, text]) =>
    h("div", { class: `adm-alert adm-alert-${kind}` }, icon("alert"), text)));
}

function refreshBars(r) {
  const rows = ["widget", "assist", "scheduled", "manual", "login"].map((t) => ({
    key: t, ok: r[`${t}_ok`] || 0, fail: r[`${t}_fail`] || 0,
  }));
  const max = Math.max(1, ...rows.map((x) => x.ok + x.fail));
  return h("div", { class: "adm-bars" }, rows.map((x) => {
    const text = `成功 ${x.ok} · 失败 ${x.fail}`;
    return h("div", { class: "adm-bar-row", title: `${TRIGGER[x.key]}：${text}` },
      h("span", { class: "adm-bar-label" }, TRIGGER[x.key]),
      h("span", { class: "adm-bar-track" },
        x.ok ? h("span", { class: "adm-bar-ok", style: { width: `${(x.ok / max) * 100}%` } }) : null,
        x.fail ? h("span", { class: "adm-bar-fail", style: { width: `${(x.fail / max) * 100}%` } }) : null),
      h("span", { class: "adm-bar-value" }, text));
  }));
}

/** 手机（代抓 + App 协助）占后台刷新的比例：越高，服务器 IP 打学校越少。 */
function phoneShare(r) {
  const phone = (r.widget_ok || 0) + (r.assist_ok || 0);
  const server = (r.scheduled_ok || 0) + (r.scheduled_fail || 0);
  return phone + server ? `${Math.round((phone / (phone + server)) * 100)}%` : "—";
}

function reloadText(rel) {
  if (!rel) return "—";
  const parts = [`第 ${rel.generation} 代`, `代码 ${rel.code_version}`];
  if (rel.last_reload_at) {
    const via = RELOAD_TRIGGER[rel.last_reload_trigger] || rel.last_reload_trigger || "";
    parts.push(`最近重载于 ${fmtTime(rel.last_reload_at)}（${via ? `${via} · ` : ""}耗时 ${rel.last_reload_ms} ms）`);
  } else {
    parts.push("启动以来未重载");
  }
  if (rel.watcher_alive) parts.push("文件监听运行中");
  return parts.join(" · ");
}

function renderOverview(o) {
  const on = o.online;
  const via = Object.entries(on.by_via).map(([k, v]) => `${VIA[k] || k} ${v}`).join(" · ");
  const st = o.user_status;
  const r = o.refresh_24h;
  const count = (suffix) => Object.entries(r).filter(([k]) => k.endsWith(suffix)).reduce((a, [, v]) => a + v, 0);
  return [
    attentionBox(o),
    section(`在线（${Math.round(on.window_seconds / 60)} 分钟内有请求）`),
    tiles(
      tile("在线用户", on.users, via || "—"),
      tile("访客（不同 IP）", on.visitors, "含未登录"),
      tile("1 小时内活跃", on.active_1h),
      tile("24 小时内活跃", on.active_24h),
      tile("7 天内活跃", on.active_7d),
      tile("有效登录会话", on.sessions)),
    section("用户与渠道"),
    tiles(
      tile("注册用户", o.users, `正常 ${st.ok || 0} · 退避 ${st.error || 0} · 暂停 ${(st.captcha || 0) + (st.needs_rebind || 0)}`),
      tile("存了密码", o.password_users, `扫码短效 ${o.sso_short_users}`),
      tile("小组件 / App 链接", o.widget_users, `允许代抓 ${o.phone.enabled}`),
      tile("绑定的安卓设备", o.device_stats.devices, `${o.device_stats.users} 人 · 7 天内活跃 ${o.device_stats.active_7d} 台`),
      tile("安卓 App 2.0 装机", o.app.total, `7 天内活跃 ${o.app.active_7d} · 开协助 ${o.app.assist_7d}`),
      tile("日历订阅", o.ics_subscribers),
      tile("邮件通知", o.email_users)),
    section("24 小时刷新"),
    tiles(
      tile("刷新次数", count("_ok") + count("_fail"), `成功 ${count("_ok")} · 失败 ${count("_fail")}`),
      tile("手机分担占比", phoneShare(r), "（手机代抓 + App 协助）÷（它们 + 后台）")),
    refreshBars(r),
    section("服务器"),
    kv(
      ["已运行", `${fmtDuration(o.now - o.started_at)}（启动于 ${fmtTime(o.started_at)}）`],
      // Windows 上没有峰值内存（max_rss_mb 为 null），只显示 PID
      ["进程", `PID ${o.pid}${o.max_rss_mb == null ? "" : ` · 峰值内存 ${o.max_rss_mb} MB`}`],
      ["热重载", reloadText(o.reload)],
      ["数据库", `${o.db_mb} MB`],
      ["Python", `${o.python} · ${o.platform}`]),
  ];
}

// ---------- 排期 ----------

const SCHEDULE_FILTERS = [
  ["all", "全部", () => true],
  ["queued", "排队中", (i) => i.score !== null],
  ["phone", "等手机", (i) => i.phone.waiting || i.phone.running],
  ["problem", "有异常", (i) => i.status !== "ok" || i.phone.fails > 0 || !!i.sso_lost_at],
];
const scheduleView = { filter: "all", q: "", data: null, list: null, seg: null };

function keepaliveText(i) {
  const sso = i.sso_lost_at
    ? `认证中心会话已失效（${fmtRel(i.sso_lost_at)}检测到，上次有效于 ${fmtRel(i.sso_touched_at)}）`
    : i.sso_touched_at ? `认证中心上次有效 ${fmtRel(i.sso_touched_at)}` : "认证中心尚未确认";
  return `保活：${i.keepalive_at ? `上次 ${fmtRel(i.keepalive_at)}` : "尚未执行"} · ${sso}`;
}

function nextRefreshCell(i) {
  if (i.score !== null) return pill(`排队中 ${i.score}`, "pill-warn");
  if (i.phone.running) return pill("手机代抓中", "pill-ok", "代抓任务已下发至手机，期间服务器暂不重复抓取");
  if (i.phone.waiting) return pill("等待手机代抓", "pill-ok", "已到达刷新时间，优先预留窗口等待手机端拉取，超时后由服务器兜底");
  return i.next_refresh_at ? `${fmtTime(i.next_refresh_at)}（${fmtRel(i.next_refresh_at)}）` : "暂停";
}

function phoneCell(p) {
  if (!p.linked) return "未生成链接";
  if (!p.enabled) return "用户已关闭";
  return [
    p.fails ? pill(`失败 ×${p.fails}`, "pill-warn") : null,
    p.fails ? " " : null,
    p.ok_at ? `成功 ${fmtRel(p.ok_at)}` : p.fails ? null : "尚无成功记录",
  ];
}

function requestRefresh(i) {
  return h("button", { class: "btn btn-sm", type: "button", onclick: async (e) => {
    e.currentTarget.disabled = true;
    try {
      await api("api/admin/request-refresh", { method: "POST", body: { uid: i.uid } });
      showToast("已排到队首，仍受全站预算限制", { type: "success" });
      load();
    } catch (err) {
      showToast(err.message, { type: "error" });
    }
  } }, "排队刷新");
}

function scheduleRow(i) {
  return h("tr",
    td("用户", "mono", i.student, i.name ? ` ${i.name}` : ""),
    td("状态", "adm-badges", statusPill(i.status), i.fail_count ? h("span", { class: "muted" }, `×${i.fail_count}`) : null,
      i.sso_short ? pill("扫码·短效", "pill-warn", "扫码登录只有会话级 CASTGC，几小时后无法静默续期") : null,
      i.devices ? pill(`安卓 ${i.devices} 台`) : null),
    td("下次刷新", "num", nextRefreshCell(i)),
    td("原因", "wrap", i.pending_reason ? `${i.reason}（${i.pending_reason}）` : i.reason,
      ` · 约每 ${fmtInterval(i.target_minutes)}`,
      i.last_error && i.status !== "ok" ? h("div", `上次错误：${i.last_error}`) : null,
      i.keepalive_at || i.sso_lost_at ? h("div", { class: "muted" }, keepaliveText(i)) : null),
    td("手机代抓", "num", phoneCell(i.phone)),
    td("上次成功", "num", fmtRel(i.last_success_at)),
    td("下一节课", "num", i.next_class_at ? fmtTime(i.next_class_at) : "—"),
    td("最近使用", "num", fmtRel(i.last_seen_at)),
    td("", "adm-action", i.status === "ok" && i.score === null && !i.phone.running ? requestRefresh(i) : null));
}

function renderScheduleList() {
  const v = scheduleView;
  if (!v.data || !v.list) return;
  const q = v.q.toLowerCase();
  const items = v.data.items.filter((i) => !q || `${i.student} ${i.name} ${i.reason} ${i.last_error || ""}`.toLowerCase().includes(q));
  for (const [key, label, match] of SCHEDULE_FILTERS) {
    const b = v.seg.querySelector(`[data-filter="${key}"]`);
    b.textContent = `${label} ${items.filter(match).length}`;
    b.setAttribute("aria-pressed", String(key === v.filter));
  }
  const match = SCHEDULE_FILTERS.find(([key]) => key === v.filter)[2];
  replace(v.list, table(["用户", "状态", "下次刷新", "原因", "手机代抓", "上次成功", "下一节课", "最近使用", ""],
    items.filter(match).map(scheduleRow), v.data.items.length ? "没有符合条件的用户" : "还没有用户"));
}

function buildSchedule() {
  const v = scheduleView;
  const summary = h("div");
  v.list = h("div");
  v.seg = h("div", { class: "seg" }, SCHEDULE_FILTERS.map(([key, label]) =>
    h("button", { type: "button", dataset: { filter: key }, onclick: () => { v.filter = key; renderScheduleList(); } }, label)));
  const search = h("input", { type: "search", placeholder: "搜索学号、原因、错误…", value: v.q, attrs: { "aria-label": "搜索" }, oninput: (e) => {
    v.q = e.target.value.trim();
    renderScheduleList();
  } });
  replace(body, summary, section("每个人的下次刷新"), h("div", { class: "adm-toolbar" }, v.seg, search), v.list);
  return (s) => {
    const sc = s.scheduler;
    const threads = Object.entries(sc.alive).map(([k, a]) => `${k} ${a ? "运行中" : "已停止"}`).join(" · ") || "未启动";
    const lt = sc.last_tick;
    replace(summary, kv(
      ["后台刷新", sc.enabled ? threads : "已在配置中关闭（SCHEDULER_ENABLED=false）"],
      ["预算", `${sc.budget_rps} 次/秒（上限 ${sc.max_rps}）· 令牌 ${sc.tokens} · 每 ${sc.tick_seconds} 秒一轮`],
      ["学校健康", sc.upstream_cooldown > 0 ? `暂停中，还剩 ${sc.upstream_cooldown} 秒`
        : sc.upstream_penalty > 0 ? `额外间隔 ${sc.upstream_penalty} 秒` : "正常"],
      ["上一轮", lt ? `${fmtTime(lt.at)}：到期 ${lt.due} 人，刷新 ${lt.refreshed} 人，保活 ${lt.kept_alive ?? 0} 人` : "还没跑过"],
      ["当前排队", `${s.due} 人`]));
    v.data = s;
    renderScheduleList();
  };
}

// ---------- 手机与安卓 ----------

function assistText(s) {
  if (!s.enabled) return "未开启（服务端 .env 设 ASSIST_ENABLED=1 后才会向 App 派发任务）";
  return `已开启 · 正在协助 ${s.active}/${s.max_sessions} 台 · 暂由服务器直连的用户 ${s.skipped_users} 名`;
}

function installState(x) {
  if (x.assist_block_until && x.assist_block_until * 1000 > Date.now()) {
    return pill("协助冷却中", "pill-warn", "连续中继失败，暂停派活");
  }
  if (x.assist) return pill("开协助", "pill-ok");
  return x.has_timetable ? pill("已导入课表") : pill("未导入课表");
}

function renderAndroid(a) {
  const p = a.phone;
  const d = a.device_stats;
  const rel = a.releases;
  const app = a.app;
  return [
    section("安卓 App 2.0（本地优先，匿名统计）"),
    tiles(
      tile("装机", app.total, `近 7 天新增 ${app.new_7d}`),
      tile("活跃", app.active_1d, `24 小时 · 7 天 ${app.active_7d} · 30 天 ${app.active_30d}`),
      tile("已导入课表", app.with_timetable_7d, "7 天内活跃的安装"),
      tile("开协助刷新", app.assist_7d, `累计成功 ${app.assist_ok} · 中继失败 ${app.assist_fail}`)),
    kv(
      ["协助刷新", assistText(a.assist)],
      ["30 天内版本", app.versions_30d.length ? app.versions_30d.map((v) => `${v.version} × ${v.n}`).join(" · ") : "—"]),
    h("div", { class: "adm-gap" }),
    table(["安装", "版本", "系统", "首次", "最近", "状态", "协助"], a.installs.map((x) => h("tr",
      td("安装", "mono", x.id),
      td("版本", "mono", x.version || "—"),
      td("系统", "num", x.sdk ? `API ${x.sdk}` : "—"),
      td("首次", "num", fmtTime(x.first_seen_at)),
      td("最近", "num", fmtRel(x.last_seen_at)),
      td("状态", null, installState(x)),
      td("协助", "num", x.assist_ok || x.assist_fail ? `成功 ${x.assist_ok} · 失败 ${x.assist_fail}` : "—"))),
    "还没有 App 上报过心跳"),
    h("p", { class: "hint adm-gap" }, "App 只上报随机安装 id（本站仅存其哈希，这里显示前 8 位）、版本、系统版本与开关状态，不含学号、姓名或课表。"),
    section("手机代抓（旧版 App / iOS 小组件）"),
    tiles(
      tile("有链接的用户", p.linked, `允许代抓 ${p.enabled}`),
      tile("代抓正常", p.healthy, "24 小时内成功且之后没失败"),
      tile("最近失败", p.failing, "由服务器兜底刷新"),
      tile("正在代抓", p.running)),
    section("绑定的设备"),
    tiles(
      tile("设备", d.devices, `${d.users} 人 · 每人最多 ${a.max_devices} 台`),
      tile("7 天内活跃", d.active_7d),
      tile("等待配对", a.pending_pairings, "配对码还没过期")),
    table(["用户", "设备", "绑定时间", "最近使用"], a.devices.map((x) => h("tr",
      td("用户", "mono", x.student),
      td("设备", "wrap", x.name),
      td("绑定时间", "num", fmtTime(x.created_at)),
      td("最近使用", "num", fmtRel(x.last_seen_at)))), "还没有绑定的设备"),
    section("安装包镜像"),
    kv(
      ["GitHub 同步", rel.failing ? `最近同步失败（${rel.last_error}，${fmtRel(rel.last_error_at)}），当前返回已缓存的旧列表`
        : rel.last_ok_at ? `正常，${fmtRel(rel.last_ok_at)}同步` : "尚未同步"],
      ["最新正式版", rel.latest_stable || "—"]),
    h("div", { class: "adm-gap" }),
    table(["版本", "类型", "文件", "大小", "本站镜像"], rel.releases.map((x) => h("tr",
      td("版本", "mono", x.tag),
      td("类型", null, x.prerelease ? pill("预发布", "pill-warn") : pill("正式版", "pill-ok")),
      td("文件", "wrap", x.name),
      td("大小", "num", fmtSize(x.size)),
      td("本站镜像", null, x.mirrored ? pill("已缓存", "pill-ok") : pill("首次下载时按需缓存")))), "暂无可用版本"),
    h("p", { class: "hint adm-gap" }, "设备名称为客户端上报的机型；此处仅作展示，撤销设备请由用户本人在设置页操作。"),
  ];
}

// ---------- 刷新记录 ----------

const refreshFilter = { trigger: "", failed: false };

function buildRefreshes() {
  const list = h("div");
  const trigger = h("select", { class: "select", attrs: { "aria-label": "触发方式" }, onchange: (e) => { refreshFilter.trigger = e.target.value; load(); } },
    h("option", { value: "" }, "全部来源"),
    Object.entries(TRIGGER).map(([v, l]) => h("option", { value: v, selected: v === refreshFilter.trigger }, l)));
  const failed = h("label", { class: "check" },
    h("input", { type: "checkbox", checked: refreshFilter.failed, onchange: (e) => { refreshFilter.failed = e.target.checked; load(); } }), "只看失败");
  replace(body, h("div", { class: "adm-toolbar" }, trigger, failed), list);
  return (rows) => replace(list, table(["时间", "用户", "来源", "结果", "说明"], rows.map((r) => h("tr",
    td("时间", "num", fmtTime(r.at)),
    td("用户", "mono", r.student),
    td("来源", null, TRIGGER[r.trigger] || r.trigger),
    td("结果", null, pill(r.ok ? "成功" : "失败", r.ok ? "pill-ok" : "pill-danger")),
    td("说明", "wrap", r.message || ""))), "没有符合条件的刷新记录"));
}

function refreshesUrl() {
  const params = new URLSearchParams({ limit: "200" });
  if (refreshFilter.trigger) params.set("trigger", refreshFilter.trigger);
  if (refreshFilter.failed) params.set("failed", "true");
  return `api/admin/refreshes?${params}`;
}

// ---------- 日志 ----------

const logFilter = { level: "", q: "" };
let logFollow = true;
let logSeq = 0;
let logBox = null;

function logLine(r) {
  return h("div", { class: `adm-log-line lv-${r.level}` },
    h("time", fmtTime(r.at)), h("span", { class: "lg" }, `${r.level[0]} ${r.logger || "root"}`), r.message);
}

function buildLogs() {
  const restart = () => { logSeq = 0; load(); };
  const level = h("select", { class: "select", attrs: { "aria-label": "级别" }, onchange: (e) => {
    logFilter.level = e.target.value;
    restart();
  } }, [["", "全部级别"], ["WARNING", "警告及以上"], ["ERROR", "仅错误"]].map(([v, l]) =>
    h("option", { value: v, selected: v === logFilter.level }, l)));
  let debounce;
  const search = h("input", { type: "search", placeholder: "搜索日志…", value: logFilter.q, attrs: { "aria-label": "搜索日志" }, oninput: (e) => {
    window.clearTimeout(debounce);
    debounce = window.setTimeout(() => { logFilter.q = e.target.value.trim(); restart(); }, 300);
  } });
  const follow = h("label", { class: "check" },
    h("input", { type: "checkbox", checked: logFollow, onchange: (e) => { logFollow = e.target.checked; } }), "跟随最新");
  logSeq = 0;
  logBox = h("div", { class: "adm-log", attrs: { role: "log", "aria-live": "off" } });
  replace(body, h("div", { class: "adm-toolbar" }, level, search, follow),
    logBox, h("p", { class: "hint adm-gap" }, "进程内存仅保留最近 2000 条日志，服务重启后清空；更早的历史日志请在服务器上执行 journalctl -u kebiao 查看。"));
  return (data) => {
    if (logSeq === 0 || data.seq < logSeq) logBox.replaceChildren();
    logBox.querySelector(".adm-empty")?.remove();
    logBox.append(...data.items.map(logLine));
    while (logBox.childElementCount > 2000) logBox.firstElementChild.remove();
    if (!logBox.childElementCount) logBox.append(h("div", { class: "adm-empty" }, "没有匹配的日志"));
    logSeq = data.seq;
    if (logFollow) logBox.scrollTop = logBox.scrollHeight;
  };
}

function logsUrl() {
  const params = new URLSearchParams({ after: String(logSeq), limit: "1000" });
  if (logFilter.level) params.set("level", logFilter.level);
  if (logFilter.q) params.set("q", logFilter.q);
  return `api/admin/logs?${params}`;
}

// ---------- 主界面 ----------

/** url：每次加载请求什么；build：搭好不随轮询重建的部分（筛选框等），返回填数据的函数；没有 build 的整页重画。 */
const TABS = {
  overview: { label: "概况", url: () => "api/admin/overview", render: renderOverview },
  schedule: { label: "排期计划", url: () => "api/admin/schedule", build: buildSchedule },
  android: { label: "手机与安卓", url: () => "api/admin/android", render: renderAndroid },
  refreshes: { label: "刷新记录", url: refreshesUrl, build: buildRefreshes },
  logs: { label: "服务器日志", url: logsUrl, build: buildLogs },
};

let tab = TABS[window.location.hash.slice(1)] ? window.location.hash.slice(1) : "overview";
let body;
let stamp;
let fill;
let latestLoad = 0;
let inFlight = 0;

function enterTab(key) {
  tab = key;
  window.history.replaceState(null, "", `#${key}`);
  const view = TABS[key];
  fill = view.build ? view.build() : (data) => replace(body, view.render(data));
  if (!view.build) replace(body, h("div", { class: "adm-empty" }, "加载中…"));
  load();
}

function showDashboard() {
  body = h("div");
  stamp = h("span", { class: "muted adm-stamp", attrs: { "aria-live": "polite" } });
  const tabs = h("div", { class: "adm-tabs tabs", attrs: { role: "tablist" } }, Object.entries(TABS).map(([key, { label }]) =>
    h("button", { type: "button", attrs: { role: "tab", "aria-selected": String(key === tab) }, onclick: (e) => {
      for (const b of tabs.children) b.setAttribute("aria-selected", String(b === e.currentTarget));
      enterTab(key);
    } }, label)));
  const logout = h("button", { class: "btn btn-sm", type: "button", onclick: async () => {
    try { await api("api/admin/logout", { method: "POST" }); } catch { /* 已失效也回登录页 */ }
    showLogin();
  } }, "退出");
  const refresh = h("button", { class: "btn btn-sm", type: "button", onclick: () => load() }, "刷新");
  const hotReload = h("button", { class: "btn btn-sm", type: "button", onclick: async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      const res = await api("api/admin/reload", { method: "POST" });
      showToast(`热重载完成：第 ${res.generation} 代（${res.last_reload_ms} ms）`, { type: "success" });
      load();
    } catch (err) {
      showToast(err.message, { type: "error" });
      load();
    } finally {
      btn.disabled = false;
    }
  } }, "热重载");
  replace(app, h("div", { class: "adm" },
    h("header", { class: "adm-head" }, h("h1", "服务器管理"), stamp, refresh, hotReload, logout),
    tabs, body));
  enterTab(tab);
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

async function load(background = false) {
  // 上一次还没回来时，轮询不叠加；用户切换标签或筛选要立刻生效，旧响应回来后丢弃
  if (background && inFlight) return;
  const id = ++latestLoad;
  const render = fill;
  inFlight += 1;
  try {
    const data = await api(TABS[tab].url());
    if (id !== latestLoad || !csrf) return;
    render(data);
    stamp.textContent = `更新于 ${fmtTime(Date.now() / 1000, false)} · 每 ${POLL_MS / 1000} 秒自动刷新`;
  } catch (err) {
    if (id === latestLoad && err.status !== 401) stamp.textContent = `加载失败：${err.message}`;
  } finally {
    inFlight -= 1;
  }
}

document.addEventListener("visibilitychange", () => { if (!document.hidden && timer) load(true); });

// ---------- 启动 ----------

(async () => {
  try {
    csrf = (await api("api/admin/me")).csrf;
    showDashboard();
  } catch {
    showLogin();
  }
})();
