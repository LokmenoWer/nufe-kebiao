// Variables used by Scriptable.
// These must be at the very top of the file. Do not edit.
// icon-color: deep-blue; icon-glyph: calendar-alt;

/**
 * 南财课表 · iOS 桌面小组件（Scriptable）
 *
 * 用法：
 *   1. App Store 安装免费的 Scriptable，新建脚本，把这整段粘贴进去（网站设置里“复制脚本”已经填好你的链接）。
 *   2. 桌面长按 → 添加小组件 → Scriptable → 选尺寸 → 编辑小组件：Script 选这个脚本。
 *   3. Parameter 填 today（今天，默认）/ tomorrow（明天）/ week（本周，建议用大号或中号）/
 *      full（铺满：本周课表按时长铺满整块，信息更全，建议大号）。
 *      再加一个数字是提前几分钟上课提醒：today 15；today 0 关闭。不写则提前 10 分钟。
 *      几块小组件请写同一个数字，后刷新的那块会改掉已经安排的提醒。
 *   锁屏小组件同样可用：显示下一节课和倒计时。
 *   在 Scriptable 里直接运行这个脚本：可以预览各种尺寸、刷新课表、更新脚本、打开网站。
 *
 * 调课：服务器每节课出门前（上课前半小时）会替你向学校确认一次课表，小组件在那之后重画。
 *   改了教室 / 时间的课标“调”（附原来的安排），当天停掉的课列在“停课”里，标题栏提示“有调课”。
 *   上课提醒是本机通知，只能在 Scriptable 里运行脚本时安排（小组件进程里排通知会被系统掐掉）；
 *   课表变了而提醒还是旧的，标题栏会提示“点按更新提醒”，点一下小组件就会在 Scriptable 里重排。
 *
 * 下面的链接本身就是凭证（只读，能看到你的课表）。泄露了就去网站设置里“重新生成”，再重新复制脚本。
 */
const WIDGET_URL = "__WIDGET_URL__";
const SCRIPT_VERSION = "__SCRIPT_VERSION__"; // 服务器下发时填入脚本内容的哈希，无需手改

const TZ_OFFSET_MINUTES = 8 * 60;      // 北京时间，学校课表按它算
const STALE_SECONDS = 2 * 86400;       // 课表超过这么久没更新就提示
const CHANGE_NOTE_SECONDS = 86400;     // 一天内发现过调课：标题栏提示（看得到调课标记时一直提示）
const DEFAULT_REMIND_MINUTES = 10;
const MAX_REMIND_MINUTES = 180;
const REMIND_HORIZON_DAYS = 7;         // 安排未来这么多天的提醒；更远的等下次运行
const MAX_REMINDERS = 40;              // iOS 待发送通知上限是 64，留出余量
const REMIND_MIN_GAP_MS = 60 * 1000;
const MAX_REDRAW_MS = 30 * 60000;      // 最长 30 分钟重画一次
const PENDING_REFRESH_MS = 2 * 60000;  // 服务器正在拉课表：大约两分钟后再看
const AFTER_CHECK_MINUTES = 25;        // 服务器在上课前 30 分钟确认课表，之后重画一次把结果画上
const WEEKDAY = ["", "一", "二", "三", "四", "五", "六", "日"];
const ROW_GAP_MINUTES = 60;            // 周视图：开始时间相差不到这么久的课排在同一行
const FULL_GAP = 6;                    // 铺满模式：上午 / 下午 / 晚上之间压缩成这么高的间隔
const LABEL_H = 12;                    // 铺满模式：时间轴一个标签占的高度
// 各机型（竖屏，pt）的小组件尺寸：[屏宽, 屏高, 小号边长, 中/大号宽, 大号高]。来自 Apple 的小组件尺寸表
const WIDGET_SIZES = [
  [440, 956, 170, 364, 382], [430, 932, 170, 364, 382], [428, 926, 170, 364, 382],
  [414, 896, 169, 360, 379], [414, 736, 159, 348, 357], [402, 874, 158, 338, 354],
  [393, 852, 158, 338, 354], [390, 844, 158, 338, 354], [375, 812, 155, 329, 345],
  [375, 667, 148, 321, 324], [320, 568, 141, 292, 311],
];
const SEGMENTS = [{ first: 0, label: "上午" }, { first: 12 * 60, label: "下午" }, { first: 18 * 60, label: "晚上" }];
const MODE_ALIASES = {
  today: "today", 今天: "today", 今日: "today", "": "today",
  tomorrow: "tomorrow", 明天: "tomorrow", 明日: "tomorrow",
  week: "week", 本周: "week", 周: "week", 全部: "week", all: "week",
  full: "full", 铺满: "full", 满: "full", 课表: "full", grid: "full",
};

// ======================================================================
// 纯逻辑（不依赖 Scriptable，可在 Node 里测试）
// ======================================================================

// ---- 参数与时间 ----

function parseMode(param, family) {
  const raw = String(param || "").trim();
  const mode = MODE_ALIASES[raw.toLowerCase()] || MODE_ALIASES[raw] || "today";
  // 小号和锁屏放不下一周，退回今天
  if ((mode === "week" || mode === "full") && (family === "small" || String(family || "").startsWith("accessory"))) return "today";
  return mode;
}

/** Parameter：`today` / `tomorrow` / `week`，可选再跟提前分钟数（`today 15`，`0` 关闭提醒）。 */
function parseParam(param, family) {
  const parts = String(param || "").trim().split(/[\s,，]+/).filter(Boolean);
  let modeWord = "";
  let remind = DEFAULT_REMIND_MINUTES;
  for (const part of parts) {
    if (/^\d{1,3}$/.test(part)) remind = Math.min(MAX_REMIND_MINUTES, Number(part));
    else if (!modeWord) modeWord = part;
  }
  return { mode: parseMode(modeWord, family), remindMinutes: remind };
}

function shanghaiNow(ms = Date.now()) {
  const d = new Date(ms + TZ_OFFSET_MINUTES * 60000);
  return {
    ms,
    date: d.toISOString().slice(0, 10),
    minutes: d.getUTCHours() * 60 + d.getUTCMinutes(),
    weekday: ((d.getUTCDay() + 6) % 7) + 1,
  };
}

function toMinutes(hm) {
  const [h, m] = String(hm || "").split(":").map(Number);
  return Number.isFinite(h) && Number.isFinite(m) ? h * 60 + m : 0;
}

/** "2026-10-26" + "08:20"（北京时间）→ 绝对时刻，给倒计时用。 */
function lessonDate(isoDate, hm) {
  return new Date(`${isoDate}T${hm}:00+08:00`);
}

function shortDate(iso) {
  const [, m, d] = String(iso || "").split("-");
  return m && d ? `${Number(m)}/${Number(d)}` : "";
}

function daysBetween(fromIso, toIso) {
  return Math.round((Date.parse(`${toIso}T00:00:00Z`) - Date.parse(`${fromIso}T00:00:00Z`)) / 86400000);
}

/** 版本号是服务器上脚本内容的哈希（sha-xxxxxxxxxxxx）：只要和服务器的不一样，就是有新脚本。 */
function isOutdated(latest, current) {
  return Boolean(latest) && String(latest) !== String(current);
}

function fnv(text) {
  let h = 2166136261;
  for (const ch of String(text)) {
    h ^= ch.codePointAt(0);
    h = Math.imul(h, 16777619) >>> 0;
  }
  return h.toString(16);
}

/** 每个链接一份离线缓存：同一台手机上不同账号 / 不同脚本的小组件互不覆盖。 */
function cacheName(url) {
  return `nufe-kebiao-${fnv(url)}.json`;
}

/** 网站上对应的视图：今天 / 明天 → 日视图那一天；本周 → 周视图。 */
function siteLink(base, payload, day, view = "day") {
  const params = [];
  if (payload && payload.semester_id) params.push(`sem=${payload.semester_id}`);
  if (day && day.week) params.push(`w=${day.week}`);
  params.push(`view=${view}`);
  if (view === "day" && day && day.weekday) params.push(`d=${day.weekday - 1}`);
  return `${base}#${params.join("&")}`;
}

function refreshUrl(url) {
  return `${url}${String(url).includes("?") ? "&" : "?"}refresh=1`;
}

// ---- 要画什么 ----

/** 缓存可能是昨天拿的：跨过午夜后把“明天”当成今天用；更旧的数据不再显示具体课程。 */
function resolveDays(payload, now) {
  let today = payload && payload.today;
  let tomorrow = payload && payload.tomorrow;
  if (today && today.date !== now.date) {
    if (tomorrow && tomorrow.date === now.date) {
      today = tomorrow;
      tomorrow = null;
    } else {
      today = null;
      tomorrow = null;
    }
  }
  return { today, tomorrow };
}

/**
 * 给课程标上状态：past 已下课 / live 正在上 / next 下一节 / later 之后。
 * 只有“今天”才标 next；明天、本周其他天传 markNext=false。
 */
function annotate(lessons, nowMinutes, markNext = true) {
  let nextMarked = !markNext;
  return (lessons || []).map((lesson) => {
    const start = toMinutes(lesson.start);
    const end = toMinutes(lesson.end);
    let state = "later";
    if (end <= nowMinutes) state = "past";
    else if (start <= nowMinutes) state = "live";
    else if (!nextMarked) {
      state = "next";
      nextMarked = true;
    }
    return { ...lesson, state };
  });
}

/** 当天停掉的课；今天的只留还没到下课时间的（已经过去的停课不用再提）。 */
function cancelledOn(day, nowMinutes = -1) {
  return ((day && day.cancelled) || []).filter((c) => toMinutes(c.end) > nowMinutes);
}

/** 学期阶段：before 开学前 / after 课程已结束 / during 学期中（含中间的假期）。 */
function termState(payload, now) {
  const term = payload && payload.term;
  if (!term) return { phase: "during" };
  if (now.date < term.first_day) return { phase: "before", days: daysBetween(now.date, term.first_day), first: term.first_day };
  if (now.date > term.last_day) return { phase: "after" };
  return { phase: "during" };
}

function emptyText(day, payload, now, which) {
  const term = termState(payload, now);
  if (term.phase === "before") return `距开学还有 ${term.days} 天`;
  if (term.phase === "after") return "本学期课程已结束";
  if (!day) return "数据过旧，点开刷新";
  if (!day.week) return "假期中";
  return which === "tomorrow" ? "明天没有课" : "今天没有课";
}

/** 今天之后第一个有课的日子：明天有课就是明天；否则用服务端给的 upcoming（两周内）。 */
function nextClassDay(payload, now, tomorrow) {
  if (tomorrow && tomorrow.lessons && tomorrow.lessons.length) return tomorrow;
  const up = payload && payload.upcoming;
  return up && up.date > now.date && up.lessons && up.lessons.length ? up : null;
}

function dayLabel(day, now) {
  const tomorrowIso = shanghaiNow(now.ms + 24 * 3600000).date;
  if (day.date === tomorrowIso) return `明天 · 周${WEEKDAY[day.weekday]}`;
  return `下次上课 · 周${WEEKDAY[day.weekday]} ${shortDate(day.date)}`;
}

function previewOf(day, now) {
  return { title: dayLabel(day, now), day, lessons: annotate(day.lessons, -1, false), cancelled: cancelledOn(day) };
}

/**
 * 今天 / 明天模式要画的内容：标题、课程列表、停课、空状态文案。
 * 当天课上完 / 周末 / 假期里会顺带预告下次上课，避免看到一块空白。
 */
function buildPlan(payload, mode, now) {
  const { today, tomorrow } = resolveDays(payload, now);
  if (mode === "tomorrow") {
    const day = tomorrow;
    const plan = {
      title: "明天",
      day,
      lessons: day ? annotate(day.lessons, -1, false) : [],
      cancelled: cancelledOn(day),
      empty: emptyText(day, payload, now, "tomorrow"),
    };
    const next = day && !plan.lessons.length ? nextClassDay(payload, now, null) : null;
    if (next) plan.preview = previewOf(next, now);
    return plan;
  }
  if (!today) return { title: "今天", day: null, lessons: [], cancelled: [], empty: "数据过旧，点开刷新" };
  const all = annotate(today.lessons, now.minutes);
  const remaining = all.filter((l) => l.state !== "past");
  const cancelled = cancelledOn(today, now.minutes);
  if (remaining.length) {
    return { title: "今天", day: today, lessons: remaining, total: all.length, cancelled, empty: "" };
  }
  const plan = {
    title: "今天",
    day: today,
    lessons: [],
    cancelled,
    empty: all.length ? "今天的课都上完了" : emptyText(today, payload, now, "today"),
  };
  const next = nextClassDay(payload, now, tomorrow);
  if (next) plan.preview = previewOf(next, now);
  return plan;
}

/** week 模式看哪一周：周末本周剩下的日子都没课时，改看下周。 */
function weekToShow(payload, now) {
  const current = payload && payload.week;
  const next = payload && payload.next_week;
  const days = (current && current.days) || [];
  const restHasClass = days.some((d) => d.date >= now.date && d.lessons && d.lessons.length);
  const nextHasClass = next && next.days && next.days.some((d) => d.lessons && d.lessons.length);
  if (now.weekday >= 6 && !restHasClass && nextHasClass) return { title: "下周", block: next, isNext: true };
  return { title: "本周", block: current || { days: [] }, isNext: false };
}

/** 周一到周五总是画；周末画到最后一个有课的日子：只有周六有课就是 6 列，空着的周日不占位置。 */
function weekColumns(block, now) {
  const days = (block && block.days) || [];
  let count = 5;
  days.forEach((d, i) => {
    if (i >= 5 && ((d.lessons && d.lessons.length) || (d.cancelled && d.cancelled.length))) count = i + 1;
  });
  return days.slice(0, count).map((d) => ({
    ...d,
    isToday: d.date === now.date,
    lessons: annotate(d.lessons, d.date === now.date ? now.minutes : d.date < now.date ? 24 * 60 : -1,
      d.date === now.date),
    cancelled: d.date < now.date ? [] : cancelledOn(d, d.date === now.date ? now.minutes : -1),
  }));
}

function hm(minutes) {
  return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
}

/**
 * 周视图的行：按上课时间对齐，而不是每天从第一格往下堆——
 * 只有一节下午课的那天，这节课也画在下午那一行。
 * 开始时间相差不到 ROW_GAP_MINUTES 的归一行（08:00 和 08:20 是同一大节），行标签取最早的那个；
 * 行数放不下时退成上午 / 下午 / 晚上三行。
 */
function timeRows(columns, maxRows) {
  const starts = new Set();
  for (const col of columns) {
    for (const x of [...(col.lessons || []), ...(col.cancelled || [])]) starts.add(toMinutes(x.start));
  }
  const rows = [];
  for (const m of [...starts].sort((a, b) => a - b)) {
    if (!rows.length || m - rows[rows.length - 1].first >= ROW_GAP_MINUTES) rows.push({ first: m, label: hm(m) });
  }
  if (rows.length <= maxRows) return rows;
  return SEGMENTS.filter((seg, i) => [...starts].some((m) => m >= seg.first && (i === SEGMENTS.length - 1 || m < SEGMENTS[i + 1].first)));
}

function rowOf(rows, minutes) {
  let index = 0;
  rows.forEach((row, i) => { if (minutes >= row.first) index = i; });
  return index;
}

/** { rows, cells }：cells[列][行] = { lessons, cancelled }。同一格里有两节（撞课）时都留着。 */
function weekGrid(columns, maxRows) {
  const rows = timeRows(columns, maxRows);
  const cells = columns.map((col) => {
    const out = rows.map(() => ({ lessons: [], cancelled: [] }));
    for (const lesson of col.lessons || []) out[rowOf(rows, toMinutes(lesson.start))].lessons.push(lesson);
    for (const c of col.cancelled || []) out[rowOf(rows, toMinutes(c.start))].cancelled.push(c);
    return out;
  });
  return { rows, cells };
}

/** 本机的小组件尺寸（pt）。认不出的机型按相近屏宽估；iPad 的超大号按两倍中号宽估。 */
function widgetSizeFor(screen, family) {
  const w = screen ? Math.min(screen.w, screen.h) : 0;
  const h = screen ? Math.max(screen.w, screen.h) : 0;
  const row = WIDGET_SIZES.find((r) => r[0] === w && r[1] === h)
    || WIDGET_SIZES.find((r) => r[0] <= w) || WIDGET_SIZES[8];
  const [, , small, wide, tall] = row;
  if (family === "small") return { w: small, h: small };
  if (family === "medium") return { w: wide, h: small };
  if (family === "extraLarge") return { w: wide * 2 + 16, h: tall };
  return { w: wide, h: tall };
}

/**
 * 铺满模式的版面：纵轴是时间，课的高度和时长成正比；一周里没人上课的时段（午休、傍晚）压成 FULL_GAP。
 * 返回 { labels: [{ top, text }], cols: [[{ kind, item, top, height, more }]] }，单位 pt。
 */
function fullLayout(columns, height) {
  const spans = [];
  for (const col of columns) {
    for (const x of [...(col.lessons || []), ...(col.cancelled || [])]) {
      const s = toMinutes(x.start);
      const e = toMinutes(x.end);
      if (e > s) spans.push([s, e]);
    }
  }
  if (!spans.length) return { labels: [], cols: columns.map(() => []) };
  spans.sort((a, b) => a[0] - b[0]);
  const blocks = [];
  for (const [s, e] of spans) {
    const last = blocks[blocks.length - 1];
    if (last && s <= last[1]) last[1] = Math.max(last[1], e);
    else blocks.push([s, e]);
  }
  const total = blocks.reduce((sum, [s, e]) => sum + e - s, 0);
  const scale = (height - (blocks.length - 1) * FULL_GAP) / total;
  const y = (t) => {
    let offset = 0;
    for (const [s, e] of blocks) {
      if (t <= e) return offset + Math.max(0, t - s) * scale;
      offset += (e - s) * scale + FULL_GAP;
    }
    return offset - FULL_GAP;
  };
  const cols = columns.map((col) => {
    const items = [
      ...(col.lessons || []).map((item) => ({ kind: "lesson", item })),
      ...(col.cancelled || []).map((item) => ({ kind: "cancelled", item })),
    ].filter(({ item }) => toMinutes(item.end) > toMinutes(item.start))
      .sort((a, b) => toMinutes(a.item.start) - toMinutes(b.item.start) || (a.kind === "lesson" ? -1 : 1));
    const out = [];
    let busyUntil = -1;
    for (const it of items) {
      const s = toMinutes(it.item.start);
      const e = toMinutes(it.item.end);
      if (s < busyUntil) {
        // 撞课：画不下第二张，记在前一张上；停课的空位被别的课占了就不画
        if (it.kind === "lesson" && out.length) out[out.length - 1].more += 1;
        continue;
      }
      out.push({ ...it, top: y(s), height: y(e) - y(s), more: 0 });
      busyUntil = e;
    }
    return out;
  });
  const labels = [];
  for (const s of [...new Set(spans.map(([start]) => start))]) {
    const top = y(s);
    if (labels.length && top < labels[labels.length - 1].top + LABEL_H) continue; // 挨得太近就省掉
    labels.push({ top, text: hm(s) });
  }
  return { labels, cols };
}

/** 画面上有没有调课 / 停课标记。 */
function showsChanges(lessons, cancelled) {
  return (cancelled && cancelled.length > 0) || (lessons || []).some((l) => l.changed && l.state !== "past");
}

function planHasChanges(plan) {
  return showsChanges(plan.lessons, plan.cancelled) || Boolean(plan.preview && showsChanges(plan.preview.lessons, plan.preview.cancelled));
}

/** 课表在上次排提醒之后变过：已经排好的本机提醒可能是旧的。 */
function needsReminderResync(payload, state, remindMinutes) {
  if (!remindMinutes || !payload || !payload.changed_at) return false;
  if (!state || !state.remindSyncedAt) return false; // 从没在 Scriptable 里排过提醒
  return payload.changed_at > state.remindSyncedAt;
}

/**
 * 标题栏右侧只放一条最要紧的提示：
 * 登录失效 > 数据过旧 > 离线 > 更新中 > 提醒要重排 > 有调课 > 脚本可更新 > 周次。
 */
function headerNote(payload, offline, day, family, extra = {}) {
  if (payload && payload.status && payload.status !== "ok") return { text: "需重新登录", tone: "warn" };
  const nowSec = (extra.nowMs || Date.now()) / 1000;
  const age = payload && payload.updated_at ? nowSec - payload.updated_at : 0;
  if (age > STALE_SECONDS) return { text: `${Math.floor(age / 86400)} 天未更新`, tone: "warn" };
  if (offline) return { text: "离线", tone: "faint" };
  if (payload && payload.refresh && (payload.refresh.pending || payload.refresh.due)) {
    return { text: "更新中", tone: "accent" };
  }
  if (extra.resync) return { text: "点按更新提醒", tone: "warn" };
  const recent = payload && payload.changed_at && nowSec - payload.changed_at < CHANGE_NOTE_SECONDS;
  if (extra.changed || recent) return { text: "有调课", tone: "warn" };
  if (payload && isOutdated(payload.script_version, SCRIPT_VERSION)) return { text: "脚本可更新", tone: "accent" };
  if (day && day.week && family !== "small") return { text: `第 ${day.week} 周`, tone: "faint" };
  return null;
}

/**
 * 下次重画：最近的上下课时刻、服务器确认完下一节课之后（课前 25 分钟）、北京时间零点后一分钟、
 * 最多 30 分钟，取最早（至少 1 分钟后）。课表正在向学校拉取时改为大约 2 分钟后再看一眼。
 */
function nextRefreshMs(payload, now) {
  const candidates = [now.ms + MAX_REDRAW_MS];
  const minutesToMidnight = 24 * 60 - now.minutes;
  candidates.push(now.ms + (minutesToMidnight + 1) * 60000);
  const { today } = resolveDays(payload, now);
  for (const lesson of (today && today.lessons) || []) {
    const start = toMinutes(lesson.start);
    for (const t of [start - AFTER_CHECK_MINUTES, start, toMinutes(lesson.end)]) {
      if (t > now.minutes) candidates.push(now.ms + (t - now.minutes) * 60000);
    }
  }
  const refresh = payload && payload.refresh;
  if (refresh && (refresh.pending || refresh.due)) candidates.push(now.ms + PENDING_REFRESH_MS);
  return Math.max(now.ms + 60000, Math.min(...candidates));
}

// ---- 上课提醒（本机通知） ----

/** 未来几天有课的日子（去重）。今天 / 明天会按缓存日期纠正，避免把昨天的“今天”排进提醒。 */
function lessonDays(payload, now) {
  const seen = new Set();
  const out = [];
  const add = (day) => {
    if (!day || !day.date || seen.has(day.date)) return;
    const delta = daysBetween(now.date, day.date);
    if (!Number.isFinite(delta) || delta < 0 || delta > REMIND_HORIZON_DAYS) return;
    if (!day.lessons || !day.lessons.length) return;
    seen.add(day.date);
    out.push(day);
  };
  const { today, tomorrow } = resolveDays(payload, now);
  add(today);
  add(tomorrow);
  add(payload && payload.upcoming);
  for (const block of [payload && payload.week, payload && payload.next_week]) {
    for (const day of (block && block.days) || []) add(day);
  }
  out.sort((a, b) => (a.date < b.date ? -1 : 1));
  return out;
}

/** 提醒 ID 带上教室和下课时间：调了课，旧提醒的 ID 对不上，会被删掉重排。 */
function reminderId(date, lesson) {
  return `nufe-kb-${date}-${lesson.start}-${fnv(`${lesson.end}|${lesson.name}|${lesson.room || ""}`)}`;
}

/** 本地通知计划：每节未来的课一条，提前 leadMinutes 分钟。已经开始、或不足 1 分钟的不再响。 */
function reminderPlan(payload, now, leadMinutes, base) {
  const lead = Number(leadMinutes);
  if (!Number.isFinite(lead) || lead <= 0) return [];
  const items = [];
  const seen = new Set();
  for (const day of lessonDays(payload, now)) {
    for (const lesson of day.lessons) {
      const startMs = lessonDate(day.date, lesson.start).getTime();
      if (startMs - now.ms < REMIND_MIN_GAP_MS) continue;
      const when = Math.max(now.ms + 5000, startMs - lead * 60000);
      if (when >= startMs) continue;
      const id = reminderId(day.date, lesson);
      if (seen.has(id)) continue;
      seen.add(id);
      const mins = Math.max(1, Math.round((startMs - when) / 60000));
      const soon = mins <= 1 ? "即将上课" : `${mins} 分钟后上课`;
      items.push({
        id,
        title: lesson.name,
        subtitle: lesson.changed ? `${soon} · 已调课` : soon,
        body: [`${lesson.start}–${lesson.end}`, lesson.room, lesson.was ? `（${lesson.was}）` : ""].filter(Boolean).join(" · "),
        when,
        url: base ? siteLink(base, payload, day) : "",
      });
    }
  }
  items.sort((a, b) => a.when - b.when);
  return items.slice(0, MAX_REMINDERS);
}

function triggerMs(value) {
  const ms = new Date(value).getTime();
  return Number.isFinite(ms) ? ms : NaN;
}

function reminderUnchanged(existing, item) {
  if (!existing || existing.identifier !== item.id) return false;
  if (existing.title !== item.title || existing.subtitle !== item.subtitle || existing.body !== item.body) return false;
  const ms = triggerMs(existing.triggerDate || existing.nextTriggerDate);
  return Number.isFinite(ms) && Math.abs(ms - item.when) < 20000;
}

/** 已排的提醒和新计划对比：没变的留下，多出来的删掉，新的补上。 */
function reminderDiff(pending, plan) {
  const ours = (pending || []).filter((n) => String(n.identifier || "").startsWith("nufe-kb-"));
  const keep = new Set();
  const create = [];
  for (const item of plan) {
    const found = ours.find((n) => reminderUnchanged(n, item));
    if (found) keep.add(found.identifier);
    else create.push(item);
  }
  const remove = ours.map((n) => n.identifier).filter((id) => id && !keep.has(id));
  return { create, remove };
}

// ======================================================================
// Scriptable 渲染
// ======================================================================

function palette() {
  const dyn = (light, dark) => Color.dynamic(new Color(light), new Color(dark));
  return {
    bg: dyn("#f6f7fb", "#16181d"),
    text: dyn("#1a1d24", "#eef0f5"),
    sub: dyn("#5b6372", "#a3aab8"),
    faint: dyn("#8a91a0", "#6f7685"),
    accent: new Color("#4b63e0"),
    live: new Color("#e03131"),
    warn: new Color("#e8590c"),
  };
}

function siteUrl() {
  return WIDGET_URL.replace(/\/widget\/[^/]+$/, "/");
}

const FONTS = {
  bold: (s) => Font.boldSystemFont(s),
  semibold: (s) => Font.semiboldSystemFont(s),
  medium: (s) => Font.mediumSystemFont(s),
  regular: (s) => Font.systemFont(s),
};

function styleText(t, { size = 12, weight = "regular", color, lines = 1, scale = 0.8 } = {}) {
  t.font = (FONTS[weight] || FONTS.regular)(size);
  if (color) t.textColor = color;
  t.lineLimit = lines;
  t.minimumScaleFactor = scale;
  return t;
}

function addText(stack, text, opts) {
  return styleText(stack.addText(String(text)), opts);
}

function hstack(parent, { center = true } = {}) {
  const row = parent.addStack();
  if (center) row.centerAlignContent();
  return row;
}

function vstack(parent) {
  const col = parent.addStack();
  col.layoutVertically();
  return col;
}

/** 正在上课：距下课的计时（系统每秒自己走，不用刷新）；下一节：相对时间（“1小时后”）。 */
function addCountdown(stack, lesson, day, opts = {}) {
  if (!day || (lesson.state !== "live" && lesson.state !== "next")) return null;
  const live = lesson.state === "live";
  const row = hstack(stack);
  if (opts.alignRight) row.addSpacer();
  if (live && opts.label !== false) addText(row, "还剩 ", { ...opts, size: opts.size || 11 });
  const d = row.addDate(lessonDate(day.date, live ? lesson.end : lesson.start));
  if (live) d.applyTimerStyle();
  else d.applyRelativeStyle();
  styleText(d, { ...opts, size: opts.size || 11 });
  return row;
}

function header(widget, C, title, subtitle, note, family) {
  const row = hstack(widget);
  addText(row, title, { size: family === "small" ? 13 : 14, weight: "bold", color: C.text });
  if (subtitle) addText(row, `  ${subtitle}`, { size: 11, color: C.sub });
  row.addSpacer();
  if (note) addText(row, note.text, { size: 10, weight: "medium", color: C[note.tone] || C.faint });
}

function daySubtitle(day) {
  return day ? `周${WEEKDAY[day.weekday]} ${shortDate(day.date)}` : "";
}

function weekSubtitle(block) {
  const days = (block && block.days) || [];
  return days.length ? `${shortDate(days[0].date)}–${shortDate(days[days.length - 1].date)}` : "";
}

/** 教室；调过课的附上原来的安排：“B202（原 A101）”。 */
function roomText(lesson) {
  if (!lesson.was) return lesson.room || "";
  return lesson.room ? `${lesson.room}（${lesson.was}）` : lesson.was;
}

function lessonMeta(lesson, showTeacher) {
  return [`${lesson.start}–${lesson.end}`, roomText(lesson), showTeacher ? lesson.teachers : ""].filter(Boolean).join(" · ");
}

function lessonRow(widget, C, lesson, { compact = false, showTeacher = false, day, url } = {}) {
  const row = hstack(widget);
  if (url) row.url = url;
  const bar = row.addStack();
  bar.size = new Size(3, compact ? 26 : 32);
  bar.cornerRadius = 1.5;
  bar.backgroundColor = new Color(lesson.color || "#4b63e0");
  row.addSpacer(7);
  const body = vstack(row);
  const title = hstack(body);
  addText(title, lesson.name, { size: compact ? 12 : 13, weight: "semibold", color: C.text });
  if (lesson.changed) addText(title, " 调", { size: compact ? 10 : 11, weight: "bold", color: C.warn });
  addText(body, lessonMeta(lesson, showTeacher), { size: compact ? 10 : 11, color: lesson.changed ? C.warn : C.sub });
  row.addSpacer();
  if (lesson.state === "live" || lesson.state === "next") {
    const side = vstack(row);
    const tagRow = hstack(side);
    tagRow.addSpacer();
    addText(tagRow, lesson.state === "live" ? "上课中" : "下一节", {
      size: 10, weight: "semibold", color: lesson.state === "live" ? C.live : C.accent,
    });
    addCountdown(side, lesson, day, { size: 10, color: C.sub, label: false, alignRight: true });
  }
}

function cancelledText(list) {
  return `停课：${list.map((c) => `${c.name} ${c.start}`).join("、")}`;
}

function centered(widget, C, text, size = 13) {
  widget.addSpacer();
  const row = hstack(widget);
  row.addSpacer();
  addText(row, text, { size, weight: "medium", color: C.sub, lines: 2 });
  row.addSpacer();
  widget.addSpacer();
}

function renderList(widget, C, plan, family, payload) {
  const large = family === "large" || family === "extraLarge";
  const max = large ? 7 : 3;
  const compact = family === "medium";
  const base = siteUrl();
  const cancelled = plan.cancelled || [];
  if (plan.lessons.length) {
    // 中号放不下时停课挤掉最后一节课的位置：停课比“还有一节”更要紧
    const shown = cancelled.length && !large && plan.lessons.length >= max ? max - 1 : max;
    widget.addSpacer(8);
    plan.lessons.slice(0, shown).forEach((lesson, i) => {
      if (i) widget.addSpacer(compact ? 5 : 8);
      lessonRow(widget, C, lesson, { compact, showTeacher: !compact, day: plan.day, url: siteLink(base, payload, plan.day) });
    });
    widget.addSpacer();
    if (cancelled.length) addText(widget, cancelledText(cancelled), { size: 10, weight: "medium", color: C.warn });
    const rest = plan.lessons.length - shown;
    if (rest > 0) addText(widget, `还有 ${rest} 节`, { size: 10, color: C.faint });
    return;
  }
  if (plan.preview) {
    widget.addSpacer(6);
    addText(widget, plan.empty, { size: 12, weight: "medium", color: C.sub });
    if (cancelled.length) addText(widget, cancelledText(cancelled), { size: 10, weight: "medium", color: C.warn });
    widget.addSpacer(6);
    addText(widget, plan.preview.title, { size: 11, weight: "semibold", color: C.faint });
    widget.addSpacer(4);
    const url = siteLink(base, payload, plan.preview.day);
    plan.preview.lessons.slice(0, max - 1).forEach((lesson, i) => {
      if (i) widget.addSpacer(5);
      lessonRow(widget, C, lesson, { compact: true, url });
    });
    widget.addSpacer();
    return;
  }
  if (cancelled.length) {
    widget.addSpacer();
    addText(widget, plan.empty, { size: 13, weight: "medium", color: C.sub });
    addText(widget, cancelledText(cancelled), { size: 11, weight: "medium", color: C.warn, lines: 2 });
    widget.addSpacer();
    return;
  }
  centered(widget, C, plan.empty);
}

function renderSmall(widget, C, plan) {
  const focus = plan.lessons.find((l) => l.state === "live" || l.state === "next") || plan.lessons[0];
  const cancelled = plan.cancelled || [];
  if (!focus) {
    if (plan.preview) {
      widget.addSpacer(6);
      addText(widget, plan.empty, { size: 11, color: C.sub, lines: 2 });
      if (cancelled.length) addText(widget, cancelledText(cancelled), { size: 10, color: C.warn });
      widget.addSpacer();
      const first = plan.preview.lessons[0];
      addText(widget, `${plan.preview.title.replace(" · ", " ")} ${first.start}`, { size: 11, weight: "semibold", color: C.faint, scale: 0.6 });
      addText(widget, first.name, { size: 14, weight: "bold", color: C.text, lines: 2 });
      return;
    }
    if (cancelled.length) {
      widget.addSpacer();
      addText(widget, plan.empty, { size: 12, weight: "medium", color: C.sub });
      addText(widget, cancelledText(cancelled), { size: 11, weight: "medium", color: C.warn, lines: 3 });
      widget.addSpacer();
      return;
    }
    centered(widget, C, plan.empty, 12);
    return;
  }
  widget.addSpacer();
  const tagRow = hstack(widget);
  const tag = focus.state === "live" ? "上课中" : focus.state === "next" ? "下一节" : "";
  if (tag) addText(tagRow, tag, { size: 11, weight: "semibold", color: focus.state === "live" ? C.live : C.accent });
  if (focus.changed) addText(tagRow, tag ? " · 调课" : "调课", { size: 11, weight: "semibold", color: C.warn });
  addText(widget, `${focus.start}–${focus.end}`, { size: 18, weight: "bold", color: C.text, scale: 0.6 });
  addText(widget, focus.name, { size: 14, weight: "semibold", color: new Color(focus.color || "#4b63e0"), lines: 2 });
  if (roomText(focus)) addText(widget, roomText(focus), { size: 11, color: focus.changed ? C.warn : C.sub });
  addCountdown(widget, focus, plan.day, { size: 11, color: C.sub });
  const others = plan.lessons.length - 1;
  widget.addSpacer();
  if (cancelled.length) addText(widget, cancelledText(cancelled), { size: 10, color: C.warn });
  else if (others > 0) addText(widget, `之后还有 ${others} 节`, { size: 10, color: C.faint });
}

/** 课程色做文字：深色模式下调亮一些，免得深蓝 / 深紫的时间在黑底上看不清。 */
function mixHex(hex, other, t) {
  const parse = (h) => [1, 3, 5].map((i) => parseInt(String(h).slice(i, i + 2), 16) || 0);
  const [a, b] = [parse(hex), parse(other)];
  return `#${a.map((v, i) => Math.round(v + (b[i] - v) * t).toString(16).padStart(2, "0")).join("")}`;
}

function tintText(hex) {
  const base = /^#[0-9a-f]{6}$/i.test(String(hex)) ? hex : "#4b63e0";
  return Color.dynamic(new Color(base), new Color(mixHex(base, "#ffffff", 0.4)));
}

function weekChip(cell, C, lesson, { width, height, large, more = 0 }) {
  const tint = new Color(lesson.color || "#4b63e0");
  const chip = vstack(cell);
  chip.size = new Size(width, height);
  chip.setPadding(2, 4, 2, 2);
  chip.cornerRadius = 5;
  chip.backgroundColor = new Color(lesson.color || "#4b63e0", lesson.state === "past" ? 0.08 : 0.18);
  if (lesson.state === "live" || (lesson.changed && lesson.state !== "past")) {
    chip.borderColor = lesson.state === "live" ? tint : C.warn;
    chip.borderWidth = 1.5;
  }
  // 每张卡片顶上写自己的上课时间（大号写起止，中号格子够高时写开始），用课程色，一眼对上行
  const showTime = large || height >= 30;
  if (showTime) {
    // 起止时间约需 46pt 宽；有周末课排成 7 列时放不下，只写开始
    const when = large && width >= 46 ? `${lesson.start}–${lesson.end}` : lesson.start;
    addText(chip, lesson.changed ? `${when} 调` : when, {
      size: large ? 9 : 8.5, weight: "semibold", color: lesson.changed ? C.warn : tintText(lesson.color), scale: 0.7,
    });
  }
  // 大号格子放得下三行（时间 + 课名 + 教室）就写教室：两节小课的格子也要能看到去哪上课
  const showRoom = large && Boolean(lesson.room) && height >= 40;
  const lines = large ? (height >= (showRoom ? 58 : 46) ? 2 : 1) : (!showTime && height >= 28 ? 2 : 1);
  // 撞课：同一格里还有别的课，名字后面注“+1”
  const name = `${lesson.name}${!showTime && lesson.changed ? "·调" : ""}${more ? ` +${more}` : ""}`;
  addText(chip, name, { size: large ? 10 : 9.5, weight: "medium", color: lesson.changed ? C.warn : C.text, lines, scale: 0.6 });
  if (showRoom) addText(chip, lesson.room, { size: 8.5, color: C.sub, scale: 0.7 });
  chip.addSpacer();
}

function cancelledChip(cell, C, c, { width, height }) {
  const chip = vstack(cell);
  chip.size = new Size(width, height);
  chip.setPadding(2, 4, 2, 2);
  chip.cornerRadius = 5;
  chip.borderColor = C.warn;
  chip.borderWidth = 1;
  addText(chip, `停 ${c.name}`, { size: 9, weight: "medium", color: C.warn, lines: height >= 28 ? 2 : 1, scale: 0.6 });
  chip.addSpacer();
}

/** 周视图：左边一列是上课时间，每天一列，课画在它开始时间那一行；没课的格子留空。 */
function renderWeek(widget, C, columns, family, payload, size) {
  const large = family === "large" || family === "extraLarge";
  const { rows, cells } = weekGrid(columns, large ? 7 : 5);
  const width = size.w - 28;
  const axisW = 28;
  const gap = 3;
  const colW = Math.floor((width - axisW - columns.length * gap) / Math.max(columns.length, 1));
  // 去掉内边距、标题栏、星期行，再留 4pt 余量，剩下的给课程格
  const avail = size.h - 26 - 18 - 6 - 14 - 4 - 4;
  const rowH = Math.max(large ? 26 : 16, Math.min(large ? 64 : 32,
    Math.floor((avail - Math.max(rows.length - 1, 0) * gap) / Math.max(rows.length, 1))));
  const base = siteUrl();

  widget.addSpacer(6);
  const head = widget.addStack();
  head.spacing = gap;
  head.addSpacer(axisW);
  for (const col of columns) {
    const cell = head.addStack();
    cell.size = new Size(colW, 0);
    cell.url = siteLink(base, payload, col);
    cell.addSpacer();
    addText(cell, `周${WEEKDAY[col.weekday]}`, {
      size: 11, weight: col.isToday ? "bold" : "medium", color: col.isToday ? C.accent : C.sub,
    });
    cell.addSpacer();
  }
  widget.addSpacer(4);

  if (!rows.length) {
    centered(widget, C, "这一周没有课", 12);
    return;
  }
  rows.forEach((row, r) => {
    if (r) widget.addSpacer(gap);
    const line = widget.addStack();
    line.spacing = gap;
    line.topAlignContent();
    const axis = vstack(line);
    axis.size = new Size(axisW - gap, rowH);
    addText(axis, row.label, { size: large ? 9.5 : 8.5, weight: "semibold", color: C.sub, scale: 0.7 });
    axis.addSpacer();
    columns.forEach((col, i) => {
      const cell = vstack(line);
      cell.size = new Size(colW, rowH);
      cell.url = siteLink(base, payload, col);
      const { lessons, cancelled } = cells[i][r];
      const opts = { width: colW, height: rowH, large };
      if (lessons.length) {
        weekChip(cell, C, lessons[0], { ...opts, more: lessons.length - 1 });
      } else if (cancelled.length) {
        cancelledChip(cell, C, cancelled[0], opts);
      }
    });
  });
  widget.addSpacer();
}

function fullChip(cell, C, lesson, { width, height, large, more }) {
  const tint = new Color(lesson.color || "#4b63e0");
  const chip = vstack(cell);
  chip.size = new Size(width, height);
  chip.setPadding(3, 4, 2, 2);
  chip.cornerRadius = 6;
  chip.backgroundColor = new Color(lesson.color || "#4b63e0", lesson.state === "past" ? 0.08 : 0.2);
  if (lesson.state === "live" || (lesson.changed && lesson.state !== "past")) {
    chip.borderColor = lesson.state === "live" ? tint : C.warn;
    chip.borderWidth = 1.5;
  }
  // 按格子高度分配行：课名 → 教室 → 时间 → 课名第 2、3 行 → 教师。
  // 教室排在时间前面：左边时间轴已经有开始时间，两节小课的格子只够两行时留给教室
  const lineH = large ? 12 : 11;
  const n = Math.max(1, Math.floor((height - 5) / lineH));
  const hasRoom = Boolean(roomText(lesson));
  const showRoom = n >= 2 && hasRoom;
  const showTime = n >= (hasRoom ? 3 : 2);
  const showTeacher = large && n >= 6 && Boolean(lesson.teachers);
  const nameLines = Math.max(1, Math.min(3, n - showTime - showRoom - showTeacher));
  if (showTime) {
    const when = width >= 46 ? `${lesson.start}–${lesson.end}` : lesson.start;
    addText(chip, lesson.changed ? `${when} 调` : when, {
      size: large ? 9 : 8.5, weight: "semibold", color: lesson.changed ? C.warn : tintText(lesson.color), scale: 0.7,
    });
  }
  const name = `${lesson.name}${!showTime && lesson.changed ? "·调" : ""}${more ? ` +${more}` : ""}`;
  addText(chip, name, {
    size: large ? 10 : 9, weight: "semibold", color: lesson.changed ? C.warn : C.text, lines: nameLines, scale: 0.6,
  });
  if (showRoom) addText(chip, roomText(lesson), { size: large ? 8.5 : 8, color: C.sub, lines: n >= 7 ? 2 : 1, scale: 0.7 });
  if (showTeacher) addText(chip, lesson.teachers, { size: 8.5, color: C.faint, scale: 0.7 });
  chip.addSpacer();
}

/** 铺满模式：时间轴按真实时长铺满整块小组件，课越长格子越高，信息按格子大小尽量多放。 */
function renderFull(widget, C, columns, family, payload, size) {
  const large = family === "large" || family === "extraLarge";
  const axisW = 30;
  const gap = 3;
  const contentW = size.w - 28;
  const colW = Math.floor((contentW - axisW - columns.length * gap) / Math.max(columns.length, 1));
  // 内容区高度：去掉内边距、标题栏、星期行，再留 4pt 余量
  const gridH = Math.max(60, size.h - 26 - 18 - 6 - (large ? 26 : 15) - 4 - 4);
  const { labels, cols } = fullLayout(columns, gridH);
  const base = siteUrl();

  widget.addSpacer(6);
  const head = widget.addStack();
  head.spacing = gap;
  head.addSpacer(axisW);
  for (const col of columns) {
    const cell = vstack(head);
    cell.size = new Size(colW, 0);
    cell.url = siteLink(base, payload, col);
    const top = hstack(cell);
    top.addSpacer();
    addText(top, `周${WEEKDAY[col.weekday]}`, {
      size: 11, weight: col.isToday ? "bold" : "medium", color: col.isToday ? C.accent : C.sub,
    });
    top.addSpacer();
    if (large) {
      const sub = hstack(cell);
      sub.addSpacer();
      addText(sub, shortDate(col.date), { size: 9, color: col.isToday ? C.accent : C.faint });
      sub.addSpacer();
    }
  }
  widget.addSpacer(4);

  if (!labels.length) {
    centered(widget, C, "这一周没有课", 12);
    return;
  }
  const grid = widget.addStack();
  grid.spacing = gap;
  grid.topAlignContent();
  const axis = vstack(grid);
  axis.size = new Size(axisW - gap, gridH);
  let cursor = 0;
  for (const label of labels) {
    if (label.top - cursor >= 0.5) axis.addSpacer(label.top - cursor);
    const row = axis.addStack();
    row.size = new Size(axisW - gap, LABEL_H);
    addText(row, label.text, { size: large ? 9.5 : 8.5, weight: "semibold", color: C.sub, scale: 0.7 });
    row.addSpacer();
    cursor = label.top + LABEL_H;
  }
  axis.addSpacer();
  columns.forEach((col, i) => {
    const cell = vstack(grid);
    cell.size = new Size(colW, gridH);
    cell.url = siteLink(base, payload, col);
    let y = 0;
    for (const it of cols[i]) {
      if (it.top - y >= 0.5) cell.addSpacer(it.top - y);
      const h = Math.max(10, it.height - 1.5); // 前后两节紧挨着时留一条缝
      if (it.kind === "lesson") fullChip(cell, C, it.item, { width: colW, height: h, large, more: it.more });
      else cancelledChip(cell, C, it.item, { width: colW, height: h });
      y = it.top + h;
    }
    cell.addSpacer();
  });
}

function widgetSize(family) {
  try {
    const s = typeof Device !== "undefined" && typeof Device.screenSize === "function" ? Device.screenSize() : null;
    return widgetSizeFor(s ? { w: s.width, h: s.height } : null, family);
  } catch (_) {
    return widgetSizeFor(null, family);
  }
}

function renderAccessory(widget, plan, family) {
  const focus = plan.lessons.find((l) => l.state === "live" || l.state === "next");
  const mark = focus && focus.changed ? "（调）" : "";
  if (family === "accessoryInline") {
    widget.addText(focus ? `${focus.start} ${focus.name}${mark}${focus.room ? ` @${focus.room}` : ""}` : plan.empty || "今天没有课");
    return;
  }
  if (family === "accessoryCircular") {
    // 上课中 → 下课时间；有下一节 → 上课时间；今天没了 → 下次上课那天的第一节
    const first = !focus && plan.preview ? plan.preview.lessons[0] : null;
    const label = focus ? (focus.changed ? "调课" : focus.state === "live" ? "下课" : "上课")
      : first ? `周${WEEKDAY[plan.preview.day.weekday]}` : "";
    const value = focus ? (focus.state === "live" ? focus.end : focus.start) : first ? first.start : "—";
    if (label) addText(widget, label, { size: 10 }).centerAlignText();
    addText(widget, value, { size: 14, weight: "bold" }).centerAlignText();
    return;
  }
  if (!focus) {
    addText(widget, plan.empty || "今天没有课", { size: 13, weight: "semibold" });
    if (plan.cancelled && plan.cancelled.length) addText(widget, cancelledText(plan.cancelled), { size: 11 });
    else if (plan.preview) {
      addText(widget, plan.preview.title, { size: 11 });
      addText(widget, `${plan.preview.lessons[0].start} ${plan.preview.lessons[0].name}`, { size: 12 });
    }
    return;
  }
  addText(widget, `${focus.state === "live" ? "上课中" : focus.start} ${focus.name}${mark}`, { size: 13, weight: "semibold" });
  addText(widget, [focus.room, focus.was, `${focus.start}–${focus.end}`].filter(Boolean).join(" · "), { size: 12 });
  addCountdown(widget, focus, plan.day, { size: 12 });
}

function errorWidget(message) {
  const C = palette();
  const widget = new ListWidget();
  widget.backgroundColor = C.bg;
  addText(widget, "南财课表", { size: 13, weight: "bold", color: C.text });
  widget.addSpacer();
  addText(widget, message, { size: 12, color: C.sub, lines: 4 });
  widget.addSpacer();
  widget.refreshAfterDate = new Date(Date.now() + MAX_REDRAW_MS);
  if (/^https?:\/\//.test(WIDGET_URL)) widget.url = siteUrl();
  return widget;
}

/** 在 Scriptable 里运行本脚本的链接（点按小组件 → 重排提醒）。 */
function runScriptUrl() {
  const name = typeof Script !== "undefined" && typeof Script.name === "function" ? Script.name() : "";
  return name ? `scriptable:///run/${encodeURIComponent(name)}` : "";
}

/** opts.resync：课表变了而本机提醒还是旧的——提示，并让点按直接回到 Scriptable 重排。 */
function buildWidget(payload, { family, mode, offline, resync = false, nowMs = Date.now() }) {
  const C = palette();
  const now = shanghaiNow(nowMs);
  const widget = new ListWidget();
  const accessory = String(family).startsWith("accessory");
  if (!accessory) {
    widget.backgroundColor = C.bg;
    widget.setPadding(14, 14, 12, 14);
  }
  widget.refreshAfterDate = new Date(nextRefreshMs(payload, now));
  const base = siteUrl();
  const rerun = resync ? runScriptUrl() : "";

  if (mode === "week" || mode === "full") {
    const { title, block } = weekToShow(payload, now);
    const columns = weekColumns(block, now);
    const changed = columns.some((col) => showsChanges(col.lessons, col.cancelled));
    widget.url = rerun || siteLink(base, payload, block.days && block.days[0], "week");
    header(widget, C, title, weekSubtitle(block),
      headerNote(payload, offline, block.days && block.days[0], family, { changed, resync: Boolean(rerun), nowMs }), family);
    if (mode === "full") renderFull(widget, C, columns, family, payload, widgetSize(family));
    else renderWeek(widget, C, columns, family, payload, widgetSize(family));
    return widget;
  }

  const plan = buildPlan(payload, mode, now);
  widget.url = rerun || siteLink(base, payload, plan.lessons.length ? plan.day : (plan.preview && plan.preview.day) || plan.day);
  if (accessory) {
    renderAccessory(widget, plan, family);
    return widget;
  }
  header(widget, C, plan.title, daySubtitle(plan.day),
    headerNote(payload, offline, plan.day, family, { changed: planHasChanges(plan), resync: Boolean(rerun), nowMs }), family);
  if (family === "small") renderSmall(widget, C, plan);
  else renderList(widget, C, plan, family, payload);
  return widget;
}

// ======================================================================
// 读写数据：课表 JSON、离线缓存、本机状态
// ======================================================================

function localFile(name) {
  const fm = FileManager.local();
  return { fm, path: fm.joinPath(fm.cacheDirectory(), name) };
}

function readJsonFile(name) {
  const { fm, path } = localFile(name);
  if (!fm.fileExists(path)) return null;
  try {
    return JSON.parse(fm.readString(path));
  } catch (_) {
    return null; // 坏了就当没有
  }
}

function writeJsonFile(name, data) {
  const { fm, path } = localFile(name);
  fm.writeString(path, JSON.stringify(data));
}

const CACHE_FILE = () => cacheName(WIDGET_URL);
const STATE_FILE = () => cacheName(WIDGET_URL).replace(/\.json$/, ".state.json");

/** 只请求一次：快上课时的确认由服务器按“出门前检查点”安排，脚本不必再追加请求。 */
async function readFeed(url) {
  const req = new Request(url);
  req.timeoutInterval = 12;
  req.headers = { "Cache-Control": "no-cache" };
  const text = await req.loadString();
  const status = req.response && req.response.statusCode;
  if (status === 404) return { error: "小组件链接已失效：请到网站设置里重新生成，并重新复制脚本" };
  let data = null;
  try {
    data = JSON.parse(text);
  } catch (_) {
    // 反向代理的错误页之类：按连不上处理，退回缓存
  }
  if (!data || data.v !== 1) throw new Error(`unexpected response ${status}`);
  return { data };
}

async function load() {
  try {
    const got = await readFeed(WIDGET_URL);
    if (got.error) return got;
    writeJsonFile(CACHE_FILE(), got.data);
    return { data: got.data, offline: false };
  } catch (_) {
    const cached = readJsonFile(CACHE_FILE());
    if (cached) return { data: cached, offline: true };
    return { error: "暂时连不上南财课表，稍后会自动重试" };
  }
}

/** 按最新课表改本地通知。lead 为 0 时清空本脚本的提醒。记下排好的时刻，小组件据此判断提醒是否过期。 */
async function syncReminders(payload, now, leadMinutes, base) {
  if (typeof Notification === "undefined") return { scheduled: 0, created: 0, removed: 0 };
  const plan = reminderPlan(payload, now, leadMinutes, base);
  const pending = typeof Notification.allPending === "function" ? await Notification.allPending() : [];
  const { create, remove } = reminderDiff(pending, plan);
  if (remove.length && typeof Notification.removePending === "function") await Notification.removePending(remove);
  for (const item of create) {
    const n = new Notification();
    n.identifier = item.id;
    n.title = item.title;
    n.subtitle = item.subtitle;
    n.body = item.body;
    n.sound = "default";
    n.threadIdentifier = "nufe-kebiao";
    if (item.url) n.openURL = item.url;
    n.setTriggerDate(new Date(item.when));
    await n.schedule();
  }
  writeJsonFile(STATE_FILE(), { remindSyncedAt: leadMinutes > 0 ? now.ms / 1000 : null, lead: leadMinutes });
  return { scheduled: plan.length, created: create.length, removed: remove.length };
}

function remindSummary(leadMinutes, result, error) {
  if (error) return `上课提醒未能设置：${error && error.message ? error.message : error}`;
  if (!result) return "";
  if (!leadMinutes) return "上课提醒：已关闭";
  return `上课提醒：提前 ${leadMinutes} 分钟，已安排 ${result.scheduled} 条`;
}

/** 桌面小组件进程。widgetFamily 有值就一定是小组件，避免只看 runsInWidget 漏判。 */
function inWidget() {
  return Boolean(config.runsInWidget || config.widgetFamily);
}

// ======================================================================
// 在 Scriptable 里直接运行：预览 / 刷新 / 更新 / 打开网站
// ======================================================================

async function updateScript() {
  const req = new Request(WIDGET_URL.replace(/\.json$/, ".js"));
  req.timeoutInterval = 15;
  const code = await req.loadString();
  const status = req.response && req.response.statusCode;
  if (status !== 200 || !/const SCRIPT_VERSION = "sha-[0-9a-f]+"/.test(code) || !code.includes(WIDGET_URL)) {
    throw new Error(status === 404 ? "链接已失效，请到网站重新复制脚本" : `下载失败（${status || "网络错误"}）`);
  }
  const version = (code.match(/const SCRIPT_VERSION = "([^"]+)"/) || [])[1] || "?";
  // 脚本可能放在 iCloud 里；没开 iCloud 时 FileManager.iCloud() 会抛错
  let fm = FileManager.local();
  try {
    const icloud = FileManager.iCloud();
    if (icloud.isFileStoredIniCloud(module.filename)) fm = icloud;
  } catch (_) {
    // 用本地
  }
  fm.writeString(module.filename, code);
  return version;
}

async function say(title, message) {
  const a = new Alert();
  a.title = title;
  a.message = message;
  a.addAction("好");
  await a.present();
}

async function refreshNow() {
  const got = await readFeed(refreshUrl(WIDGET_URL));
  if (got.error) throw new Error(got.error);
  writeJsonFile(CACHE_FILE(), got.data);
  const refresh = got.data.refresh || {};
  if (got.data.status && got.data.status !== "ok") {
    await say("需要重新登录", "自动刷新已暂停。打开网站登录一次后，小组件才能继续更新。");
  } else if (refresh.pending) {
    await say("已安排刷新", "正在向学校拉取课表，小组件大约两分钟后会换成新的。");
  } else if (refresh.fresh) {
    await say("刚刚更新过", "两分钟内刚同步过，先用现在的课表。");
  } else {
    await say("没能刷新", "服务器没有安排刷新，稍后再试。");
  }
}

async function inAppMenu(result, mode, remindNote) {
  const alert = new Alert();
  alert.title = "南财课表小组件";
  const latest = result.data && result.data.script_version;
  const versionNote = `脚本版本 ${SCRIPT_VERSION}${isOutdated(latest, SCRIPT_VERSION) ? `，可更新到 ${latest}` : ""}`;
  alert.message = remindNote ? `${versionNote}\n${remindNote}` : versionNote;
  const preview = (family, m) => buildWidget(result.data, { family, mode: m, offline: result.offline });
  const actions = [
    ["预览今天（中号）", () => preview("medium", "today").presentMedium()],
    ["预览明天（中号）", () => preview("medium", "tomorrow").presentMedium()],
    ["预览本周（大号）", () => preview("large", "week").presentLarge()],
    ["预览铺满（大号）", () => preview("large", "full").presentLarge()],
    ["预览小号", () => preview("small", mode).presentSmall()],
    ["刷新课表", refreshNow],
    ["更新脚本", async () => {
      const version = await updateScript();
      await say("已更新", `脚本已更新到 ${version}，小组件下次刷新时生效。`);
    }],
    ["打开网站", () => Safari.open(siteUrl())],
  ];
  if (result.error) actions.splice(0, actions.findIndex(([label]) => label === "预览小号") + 1);
  for (const [label] of actions) alert.addAction(label);
  alert.addCancelAction("取消");
  const index = await alert.presentSheet();
  if (index >= 0 && actions[index]) await actions[index][1]();
}

async function main() {
  const family = config.widgetFamily || "medium";
  const param = parseParam(args.widgetParameter, family);
  if (!/^https?:\/\//.test(WIDGET_URL)) {
    const widget = errorWidget("请从网站「设置 → 桌面小组件」复制脚本，里面会带上你的专属链接");
    if (inWidget()) Script.setWidget(widget);
    else await widget.presentMedium();
    return;
  }
  const result = await load();
  // 先把小组件交出去再结束。桌面进程里调用 Notification 经常不返回，
  // 系统会杀掉脚本，桌面就留下 “Call Script.setWidget()”。
  if (inWidget()) {
    const resync = needsReminderResync(result.data, readJsonFile(STATE_FILE()), param.remindMinutes);
    Script.setWidget(result.error ? errorWidget(result.error)
      : buildWidget(result.data, { family, mode: param.mode, offline: result.offline, resync }));
    return;
  }
  let remindNote = "";
  if (result.data) {
    try {
      const synced = await syncReminders(result.data, shanghaiNow(), param.remindMinutes, siteUrl());
      remindNote = remindSummary(param.remindMinutes, synced);
    } catch (e) {
      remindNote = remindSummary(param.remindMinutes, null, e);
    }
  }
  try {
    await inAppMenu(result, param.mode, remindNote);
  } catch (e) {
    await say("出错了", String(e && e.message ? e.message : e));
  }
}

if (typeof ListWidget === "undefined") {
  // Node 测试：只导出纯逻辑
  globalThis.__nufeWidget = {
    SCRIPT_VERSION, parseMode, parseParam, shanghaiNow, lessonDate, daysBetween, isOutdated, cacheName, siteLink,
    resolveDays, annotate, cancelledOn, termState, buildPlan, weekToShow, weekColumns, timeRows, weekGrid, widgetSizeFor, fullLayout, showsChanges, planHasChanges,
    needsReminderResync, headerNote, nextRefreshMs, lessonDays, refreshUrl, reminderPlan, reminderDiff,
  };
} else {
  main().catch((e) => {
    if (inWidget()) {
      try { Script.setWidget(errorWidget(`出错了：${e && e.message ? e.message : e}`)); } catch (_) { /* 已经在收尾 */ }
    }
  }).finally(() => Script.complete());
}
