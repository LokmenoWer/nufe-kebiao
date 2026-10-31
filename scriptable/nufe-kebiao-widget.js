// Variables used by Scriptable.
// These must be at the very top of the file. Do not edit.
// icon-color: deep-blue; icon-glyph: calendar-alt;

/**
 * 南财课表 · iOS 桌面小组件（Scriptable）
 *
 * 用法：
 *   1. App Store 安装免费的 Scriptable，新建脚本，把这整段粘贴进去（网站设置里“复制脚本”已经填好你的链接）。
 *   2. 桌面长按 → 添加小组件 → Scriptable → 选尺寸 → 编辑小组件：Script 选这个脚本。
 *   3. Parameter 填 today（今天，默认）/ tomorrow（明天）/ week（本周，建议用大号或中号）。
 *   锁屏小组件同样可用：显示下一节课和倒计时。
 *   在 Scriptable 里直接运行这个脚本：可以预览各种尺寸、更新脚本、打开网站。
 *
 * 下面的链接本身就是凭证（只读，能看到你的课表）。泄露了就去网站设置里“重新生成”，再重新复制脚本。
 */
const WIDGET_URL = "__WIDGET_URL__";
const SCRIPT_VERSION = "2026.11.01";

const TZ_OFFSET_MINUTES = 8 * 60; // 北京时间，学校课表按它算
const STALE_SECONDS = 2 * 86400;  // 课表超过这么久没更新就提示
const WEEKDAY = ["", "一", "二", "三", "四", "五", "六", "日"];
const MODE_ALIASES = {
  today: "today", 今天: "today", 今日: "today", "": "today",
  tomorrow: "tomorrow", 明天: "tomorrow", 明日: "tomorrow",
  week: "week", 本周: "week", 周: "week", 全部: "week", all: "week",
};

// ---------- 纯逻辑（不依赖 Scriptable，可在 Node 里测试） ----------

function parseMode(param, family) {
  const raw = String(param || "").trim();
  const mode = MODE_ALIASES[raw.toLowerCase()] || MODE_ALIASES[raw] || "today";
  // 小号和锁屏放不下一周，退回今天
  if (mode === "week" && (family === "small" || String(family || "").startsWith("accessory"))) return "today";
  return mode;
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

/** 版本号形如 2026.11.01，按字符串比较即可。 */
function isNewer(latest, current) {
  return Boolean(latest) && String(latest) > String(current);
}

/** 每个链接一份离线缓存：同一台手机上不同账号 / 不同脚本的小组件互不覆盖。 */
function cacheName(url) {
  let h = 2166136261;
  for (const ch of String(url)) {
    h ^= ch.codePointAt(0);
    h = Math.imul(h, 16777619) >>> 0;
  }
  return `nufe-kebiao-${h.toString(16)}.json`;
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

/**
 * 要画的内容：标题、课程列表、空状态文案。
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
      empty: emptyText(day, payload, now, "tomorrow"),
    };
    const next = day && !plan.lessons.length ? nextClassDay(payload, now, null) : null;
    if (next) plan.preview = { title: dayLabel(next, now), day: next, lessons: annotate(next.lessons, -1, false) };
    return plan;
  }
  if (!today) return { title: "今天", day: null, lessons: [], empty: "数据过旧，点开刷新" };
  const all = annotate(today.lessons, now.minutes);
  const remaining = all.filter((l) => l.state !== "past");
  if (remaining.length) {
    return { title: "今天", day: today, lessons: remaining, total: all.length, empty: "" };
  }
  const plan = {
    title: "今天",
    day: today,
    lessons: [],
    empty: all.length ? "今天的课都上完了" : emptyText(today, payload, now, "today"),
  };
  const next = nextClassDay(payload, now, tomorrow);
  if (next) plan.preview = { title: dayLabel(next, now), day: next, lessons: annotate(next.lessons, -1, false) };
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

function weekColumns(block, now) {
  const days = (block && block.days) || [];
  const weekend = days.slice(5).some((d) => d.lessons && d.lessons.length);
  return days.slice(0, weekend ? 7 : 5).map((d) => ({
    ...d,
    isToday: d.date === now.date,
    lessons: annotate(d.lessons, d.date === now.date ? now.minutes : d.date < now.date ? 24 * 60 : -1,
      d.date === now.date),
  }));
}

/** 标题栏右侧只放一条最要紧的提示。 */
function headerNote(payload, offline, day, family) {
  if (payload && payload.status && payload.status !== "ok") return { text: "需重新登录", tone: "warn" };
  const age = payload && payload.updated_at ? Date.now() / 1000 - payload.updated_at : 0;
  if (age > STALE_SECONDS) return { text: `${Math.floor(age / 86400)} 天未更新`, tone: "warn" };
  if (offline) return { text: "离线", tone: "faint" };
  if (payload && isNewer(payload.script_version, SCRIPT_VERSION)) return { text: "脚本可更新", tone: "accent" };
  if (day && day.week && family !== "small") return { text: `第 ${day.week} 周`, tone: "faint" };
  return null;
}

/** 下次刷新：最近的上下课时刻、北京时间零点后一分钟、最多 30 分钟，三者取最早（至少 1 分钟后）。 */
function nextRefreshMs(payload, now) {
  const candidates = [now.ms + 30 * 60000];
  const minutesToMidnight = 24 * 60 - now.minutes;
  candidates.push(now.ms + (minutesToMidnight + 1) * 60000);
  const { today } = resolveDays(payload, now);
  for (const lesson of (today && today.lessons) || []) {
    for (const t of [toMinutes(lesson.start), toMinutes(lesson.end)]) {
      if (t > now.minutes) candidates.push(now.ms + (t - now.minutes) * 60000);
    }
  }
  return Math.max(now.ms + 60000, Math.min(...candidates));
}

// ---------- Scriptable 渲染 ----------

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

function styleText(t, { size = 12, weight = "regular", color, lines = 1, scale = 0.8 } = {}) {
  t.font = weight === "bold" ? Font.boldSystemFont(size)
    : weight === "semibold" ? Font.semiboldSystemFont(size)
      : weight === "medium" ? Font.mediumSystemFont(size)
        : Font.systemFont(size);
  if (color) t.textColor = color;
  t.lineLimit = lines;
  t.minimumScaleFactor = scale;
  return t;
}

function addText(stack, text, opts) {
  return styleText(stack.addText(String(text)), opts);
}

/** 正在上课：距下课的计时（系统每秒自己走，不用刷新）；下一节：相对时间（“1小时后”）。 */
function addCountdown(stack, lesson, day, opts = {}) {
  if (!day || (lesson.state !== "live" && lesson.state !== "next")) return null;
  const live = lesson.state === "live";
  const row = stack.addStack();
  row.centerAlignContent();
  if (opts.alignRight) row.addSpacer();
  if (live && opts.label !== false) addText(row, "还剩 ", { ...opts, size: opts.size || 11 });
  const d = row.addDate(lessonDate(day.date, live ? lesson.end : lesson.start));
  if (live) d.applyTimerStyle();
  else d.applyRelativeStyle();
  styleText(d, { ...opts, size: opts.size || 11 });
  return row;
}

function header(widget, C, title, subtitle, note, family) {
  const row = widget.addStack();
  row.centerAlignContent();
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

function lessonRow(widget, C, lesson, { compact = false, showTeacher = false, day, url } = {}) {
  const row = widget.addStack();
  row.centerAlignContent();
  if (url) row.url = url;
  const bar = row.addStack();
  bar.size = new Size(3, compact ? 26 : 32);
  bar.cornerRadius = 1.5;
  bar.backgroundColor = new Color(lesson.color || "#4b63e0");
  row.addSpacer(7);
  const body = row.addStack();
  body.layoutVertically();
  addText(body, lesson.name, { size: compact ? 12 : 13, weight: "semibold", color: C.text });
  const meta = [`${lesson.start}–${lesson.end}`, lesson.room, showTeacher ? lesson.teachers : ""].filter(Boolean).join(" · ");
  addText(body, meta, { size: compact ? 10 : 11, color: C.sub });
  row.addSpacer();
  if (lesson.state === "live" || lesson.state === "next") {
    const side = row.addStack();
    side.layoutVertically();
    const tagRow = side.addStack();
    tagRow.addSpacer();
    addText(tagRow, lesson.state === "live" ? "上课中" : "下一节", {
      size: 10, weight: "semibold", color: lesson.state === "live" ? C.live : C.accent,
    });
    addCountdown(side, lesson, day, { size: 10, color: C.sub, label: false, alignRight: true });
  }
}

function centered(widget, C, text, size = 13) {
  widget.addSpacer();
  const row = widget.addStack();
  row.addSpacer();
  addText(row, text, { size, weight: "medium", color: C.sub, lines: 2 });
  row.addSpacer();
  widget.addSpacer();
}

function renderList(widget, C, plan, family, payload) {
  const max = family === "large" || family === "extraLarge" ? 7 : 3;
  const compact = family === "medium";
  const base = siteUrl();
  if (plan.lessons.length) {
    widget.addSpacer(8);
    plan.lessons.slice(0, max).forEach((lesson, i) => {
      if (i) widget.addSpacer(compact ? 5 : 8);
      lessonRow(widget, C, lesson, { compact, showTeacher: !compact, day: plan.day, url: siteLink(base, payload, plan.day) });
    });
    const rest = plan.lessons.length - max;
    widget.addSpacer();
    if (rest > 0) addText(widget, `还有 ${rest} 节`, { size: 10, color: C.faint });
    return;
  }
  if (plan.preview) {
    widget.addSpacer(6);
    addText(widget, plan.empty, { size: 12, weight: "medium", color: C.sub });
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
  centered(widget, C, plan.empty);
}

function renderSmall(widget, C, plan) {
  const focus = plan.lessons.find((l) => l.state === "live" || l.state === "next") || plan.lessons[0];
  if (!focus) {
    if (plan.preview) {
      widget.addSpacer(6);
      addText(widget, plan.empty, { size: 11, color: C.sub, lines: 2 });
      widget.addSpacer();
      const first = plan.preview.lessons[0];
      addText(widget, `${plan.preview.title.replace(" · ", " ")} ${first.start}`, { size: 11, weight: "semibold", color: C.faint, scale: 0.6 });
      addText(widget, first.name, { size: 14, weight: "bold", color: C.text, lines: 2 });
      return;
    }
    centered(widget, C, plan.empty, 12);
    return;
  }
  widget.addSpacer();
  const tag = focus.state === "live" ? "上课中" : focus.state === "next" ? "下一节" : "";
  if (tag) addText(widget, tag, { size: 11, weight: "semibold", color: focus.state === "live" ? C.live : C.accent });
  addText(widget, `${focus.start}–${focus.end}`, { size: 18, weight: "bold", color: C.text, scale: 0.6 });
  addText(widget, focus.name, { size: 14, weight: "semibold", color: new Color(focus.color || "#4b63e0"), lines: 2 });
  if (focus.room) addText(widget, focus.room, { size: 11, color: C.sub });
  addCountdown(widget, focus, plan.day, { size: 11, color: C.sub });
  const others = plan.lessons.length - 1;
  widget.addSpacer();
  if (others > 0) addText(widget, `之后还有 ${others} 节`, { size: 10, color: C.faint });
}

function renderWeek(widget, C, block, now, family, payload) {
  const columns = weekColumns(block, now);
  const large = family === "large" || family === "extraLarge";
  const perDay = large ? 6 : 3;
  // 中 / 大号内容区宽约 293（iPhone SE）~ 336pt：按窄的估，宁可留白也别被截断
  const width = family === "extraLarge" ? 680 : 296;
  const colW = Math.floor((width - (columns.length - 1) * 4) / Math.max(columns.length, 1));
  const base = siteUrl();
  widget.addSpacer(8);
  const row = widget.addStack();
  row.spacing = 4;
  row.topAlignContent();
  for (const col of columns) {
    const stack = row.addStack();
    stack.layoutVertically();
    stack.size = new Size(colW, 0);
    stack.url = siteLink(base, payload, col);
    const head = stack.addStack();
    head.addSpacer();
    addText(head, `周${WEEKDAY[col.weekday]}`, {
      size: 11, weight: col.isToday ? "bold" : "medium", color: col.isToday ? C.accent : C.sub,
    });
    head.addSpacer();
    stack.addSpacer(4);
    if (!col.lessons.length) {
      const free = stack.addStack();
      free.addSpacer();
      addText(free, "—", { size: 11, color: C.faint });
      free.addSpacer();
    }
    col.lessons.slice(0, perDay).forEach((lesson, i) => {
      if (i) stack.addSpacer(3);
      const chip = stack.addStack();
      chip.layoutVertically();
      chip.setPadding(3, 4, 3, 2);
      chip.cornerRadius = 5;
      chip.size = new Size(colW, 0);
      const base = new Color(lesson.color || "#4b63e0");
      chip.backgroundColor = new Color(lesson.color || "#4b63e0", lesson.state === "past" ? 0.08 : 0.18);
      if (lesson.state === "live") {
        chip.borderColor = base;
        chip.borderWidth = 1.5;
      }
      addText(chip, lesson.start, { size: 9, weight: "semibold", color: base });
      addText(chip, lesson.name, { size: large ? 10 : 9.5, weight: "medium", color: C.text, lines: large ? 2 : 1 });
      if (large && lesson.room) addText(chip, lesson.room, { size: 8.5, color: C.sub });
    });
    if (col.lessons.length > perDay) {
      stack.addSpacer(2);
      addText(stack, `+${col.lessons.length - perDay}`, { size: 9, color: C.faint });
    }
  }
  widget.addSpacer();
}

function renderAccessory(widget, plan, family) {
  const focus = plan.lessons.find((l) => l.state === "live" || l.state === "next");
  if (family === "accessoryInline") {
    widget.addText(focus ? `${focus.start} ${focus.name}${focus.room ? ` @${focus.room}` : ""}` : plan.empty || "今天没有课");
    return;
  }
  if (family === "accessoryCircular") {
    // 上课中 → 下课时间；有下一节 → 上课时间；今天没了 → 下次上课那天的第一节
    const first = !focus && plan.preview ? plan.preview.lessons[0] : null;
    const label = focus ? (focus.state === "live" ? "下课" : "上课") : first ? `周${WEEKDAY[plan.preview.day.weekday]}` : "";
    const value = focus ? (focus.state === "live" ? focus.end : focus.start) : first ? first.start : "—";
    if (label) addText(widget, label, { size: 10 }).centerAlignText();
    addText(widget, value, { size: 14, weight: "bold" }).centerAlignText();
    return;
  }
  if (!focus) {
    addText(widget, plan.empty || "今天没有课", { size: 13, weight: "semibold" });
    if (plan.preview) {
      addText(widget, plan.preview.title, { size: 11 });
      addText(widget, `${plan.preview.lessons[0].start} ${plan.preview.lessons[0].name}`, { size: 12 });
    }
    return;
  }
  addText(widget, `${focus.state === "live" ? "上课中" : focus.start} ${focus.name}`, { size: 13, weight: "semibold" });
  addText(widget, [focus.room, `${focus.start}–${focus.end}`].filter(Boolean).join(" · "), { size: 12 });
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
  widget.refreshAfterDate = new Date(Date.now() + 30 * 60000);
  if (/^https?:\/\//.test(WIDGET_URL)) widget.url = siteUrl();
  return widget;
}

function buildWidget(payload, { family, mode, offline, nowMs = Date.now() }) {
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

  if (mode === "week") {
    const { title, block } = weekToShow(payload, now);
    widget.url = siteLink(base, payload, block.days && block.days[0], "week");
    header(widget, C, title, weekSubtitle(block), headerNote(payload, offline, block.days && block.days[0], family), family);
    renderWeek(widget, C, block, now, family, payload);
    return widget;
  }

  const plan = buildPlan(payload, mode, now);
  widget.url = siteLink(base, payload, plan.lessons.length ? plan.day : (plan.preview && plan.preview.day) || plan.day);
  if (accessory) {
    renderAccessory(widget, plan, family);
    return widget;
  }
  header(widget, C, plan.title, daySubtitle(plan.day), headerNote(payload, offline, plan.day, family), family);
  if (family === "small") renderSmall(widget, C, plan);
  else renderList(widget, C, plan, family, payload);
  return widget;
}

// ---------- 读取数据 ----------

async function load() {
  const fm = FileManager.local();
  const cachePath = fm.joinPath(fm.cacheDirectory(), cacheName(WIDGET_URL));
  try {
    const req = new Request(WIDGET_URL);
    req.timeoutInterval = 10;
    const data = await req.loadJSON();
    const status = req.response && req.response.statusCode;
    if (status === 404) return { error: "小组件链接已失效：请到网站设置里重新生成，并重新复制脚本" };
    if (!data || data.v !== 1) throw new Error(`unexpected response ${status}`);
    fm.writeString(cachePath, JSON.stringify(data));
    return { data, offline: false };
  } catch (e) {
    if (fm.fileExists(cachePath)) {
      try {
        return { data: JSON.parse(fm.readString(cachePath)), offline: true };
      } catch (_) {
        // 缓存坏了就当没有
      }
    }
    return { error: "暂时连不上南财课表，稍后会自动重试" };
  }
}

// ---------- 在 Scriptable 里直接运行：预览 / 更新 / 打开网站 ----------

async function updateScript() {
  const req = new Request(WIDGET_URL.replace(/\.json$/, ".js"));
  req.timeoutInterval = 15;
  const code = await req.loadString();
  const status = req.response && req.response.statusCode;
  if (status !== 200 || !code.includes("const SCRIPT_VERSION") || !code.includes(WIDGET_URL)) {
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

async function inAppMenu(result, mode) {
  const alert = new Alert();
  alert.title = "南财课表小组件";
  const latest = result.data && result.data.script_version;
  alert.message = `脚本版本 ${SCRIPT_VERSION}${isNewer(latest, SCRIPT_VERSION) ? `，可更新到 ${latest}` : ""}`;
  const actions = [
    ["预览今天（中号）", () => buildWidget(result.data, { family: "medium", mode: "today", offline: result.offline }).presentMedium()],
    ["预览明天（中号）", () => buildWidget(result.data, { family: "medium", mode: "tomorrow", offline: result.offline }).presentMedium()],
    ["预览本周（大号）", () => buildWidget(result.data, { family: "large", mode: "week", offline: result.offline }).presentLarge()],
    ["预览小号", () => buildWidget(result.data, { family: "small", mode, offline: result.offline }).presentSmall()],
    ["更新脚本", async () => {
      const version = await updateScript();
      const done = new Alert();
      done.title = "已更新";
      done.message = `脚本已更新到 ${version}，小组件下次刷新时生效。`;
      done.addAction("好");
      await done.present();
    }],
    ["打开网站", () => Safari.open(siteUrl())],
  ];
  if (result.error) actions.splice(0, 4);
  for (const [label] of actions) alert.addAction(label);
  alert.addCancelAction("取消");
  const index = await alert.presentSheet();
  if (index >= 0 && actions[index]) await actions[index][1]();
}

async function main() {
  const family = config.widgetFamily || "medium";
  const mode = parseMode(args.widgetParameter, family);
  if (!/^https?:\/\//.test(WIDGET_URL)) {
    const widget = errorWidget("请从网站「设置 → 桌面小组件」复制脚本，里面会带上你的专属链接");
    if (config.runsInWidget) Script.setWidget(widget);
    else await widget.presentMedium();
    return;
  }
  const result = await load();
  if (config.runsInWidget) {
    Script.setWidget(result.error ? errorWidget(result.error)
      : buildWidget(result.data, { family, mode, offline: result.offline }));
    return;
  }
  try {
    await inAppMenu(result, mode);
  } catch (e) {
    const fail = new Alert();
    fail.title = "出错了";
    fail.message = String(e && e.message ? e.message : e);
    fail.addAction("好");
    await fail.present();
  }
}

if (typeof ListWidget === "undefined") {
  // Node 测试：只导出纯逻辑
  globalThis.__nufeWidget = {
    SCRIPT_VERSION, parseMode, shanghaiNow, lessonDate, daysBetween, isNewer, cacheName, siteLink, resolveDays,
    annotate, termState, buildPlan, weekToShow, weekColumns, headerNote, nextRefreshMs,
  };
} else {
  main().catch((e) => {
    if (config.runsInWidget) Script.setWidget(errorWidget(`出错了：${e && e.message ? e.message : e}`));
  }).finally(() => Script.complete());
}
