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
 *   锁屏小组件同样可用：显示下一节课。
 *
 * 下面的链接本身就是凭证（只读，能看到你的课表）。泄露了就去网站设置里“重新生成”，再重新复制脚本。
 */
const WIDGET_URL = "__WIDGET_URL__";

const TZ_OFFSET_MINUTES = 8 * 60; // 北京时间，学校课表按它算
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

function shortDate(iso) {
  const [, m, d] = String(iso || "").split("-");
  return m && d ? `${Number(m)}/${Number(d)}` : "";
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
 * today 模式下当天课上完了，会顺带预告明天，避免晚上看到一块空白。
 */
function buildPlan(payload, mode, now) {
  const { today, tomorrow } = resolveDays(payload, now);
  if (mode === "tomorrow") {
    const day = tomorrow;
    const plan = {
      title: "明天",
      day,
      lessons: day ? annotate(day.lessons, -1, false) : [],
      empty: day ? (day.week ? "明天没有课" : "假期中") : "数据过旧，点开刷新",
    };
    const next = day && !plan.lessons.length ? nextClassDay(payload, now, null) : null;
    if (next) plan.preview = { title: dayLabel(next, now), lessons: annotate(next.lessons, -1, false) };
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
    empty: !today.week ? "假期中" : all.length ? "今天的课都上完了" : "今天没有课",
  };
  const next = nextClassDay(payload, now, tomorrow);
  if (next) plan.preview = { title: dayLabel(next, now), lessons: annotate(next.lessons, -1, false) };
  return plan;
}

function weekColumns(payload, now) {
  const days = (payload && payload.week && payload.week.days) || [];
  const weekend = days.slice(5).some((d) => d.lessons && d.lessons.length);
  return days.slice(0, weekend ? 7 : 5).map((d) => ({
    ...d,
    isToday: d.date === now.date,
    lessons: annotate(d.lessons, d.date === now.date ? now.minutes : d.date < now.date ? 24 * 60 : -1,
      d.date === now.date),
  }));
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
    chip: dyn("#ffffff", "#20232a"),
  };
}

function siteUrl() {
  return WIDGET_URL.replace(/\/widget\/[^/]+$/, "/");
}

function addText(stack, text, { size = 12, weight = "regular", color, lines = 1, scale = 0.8 } = {}) {
  const t = stack.addText(String(text));
  t.font = weight === "bold" ? Font.boldSystemFont(size)
    : weight === "semibold" ? Font.semiboldSystemFont(size)
      : weight === "medium" ? Font.mediumSystemFont(size)
        : Font.systemFont(size);
  if (color) t.textColor = color;
  t.lineLimit = lines;
  t.minimumScaleFactor = scale;
  return t;
}

function header(widget, C, plan, payload, offline, family) {
  const row = widget.addStack();
  row.centerAlignContent();
  const day = plan.day;
  addText(row, plan.title, { size: family === "small" ? 13 : 14, weight: "bold", color: C.text });
  if (day) addText(row, `  周${WEEKDAY[day.weekday]} ${shortDate(day.date)}`, { size: 11, color: C.sub });
  row.addSpacer();
  const notes = [];
  if (payload && payload.status && payload.status !== "ok") notes.push(["需重新登录", C.warn]);
  else if (offline) notes.push(["离线", C.faint]);
  else if (day && day.week && family !== "small") notes.push([`第 ${day.week} 周`, C.faint]);
  for (const [text, color] of notes) addText(row, text, { size: 10, weight: "medium", color });
}

function lessonRow(widget, C, lesson, { compact = false, showTeacher = false } = {}) {
  const row = widget.addStack();
  row.centerAlignContent();
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
    addText(row, lesson.state === "live" ? "上课中" : "下一节", {
      size: 10, weight: "semibold", color: lesson.state === "live" ? C.live : C.accent,
    });
  }
}

function emptyBlock(widget, C, text, size = 13) {
  widget.addSpacer();
  const row = widget.addStack();
  row.addSpacer();
  addText(row, text, { size, weight: "medium", color: C.sub });
  row.addSpacer();
  widget.addSpacer();
}

function renderList(widget, C, plan, family) {
  const max = family === "large" || family === "extraLarge" ? 7 : 3;
  const compact = family === "medium";
  if (plan.lessons.length) {
    widget.addSpacer(8);
    plan.lessons.slice(0, max).forEach((lesson, i) => {
      if (i) widget.addSpacer(compact ? 5 : 8);
      lessonRow(widget, C, lesson, { compact, showTeacher: !compact });
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
    plan.preview.lessons.slice(0, max - 1).forEach((lesson, i) => {
      if (i) widget.addSpacer(5);
      lessonRow(widget, C, lesson, { compact: true });
    });
    widget.addSpacer();
    return;
  }
  emptyBlock(widget, C, plan.empty);
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
    emptyBlock(widget, C, plan.empty, 12);
    return;
  }
  widget.addSpacer();
  const tag = focus.state === "live" ? "上课中" : focus.state === "next" ? "下一节" : "";
  if (tag) addText(widget, tag, { size: 11, weight: "semibold", color: focus.state === "live" ? C.live : C.accent });
  addText(widget, `${focus.start}–${focus.end}`, { size: 18, weight: "bold", color: C.text, scale: 0.6 });
  addText(widget, focus.name, { size: 14, weight: "semibold", color: new Color(focus.color || "#4b63e0"), lines: 2 });
  if (focus.room) addText(widget, focus.room, { size: 11, color: C.sub });
  const others = plan.lessons.length - 1;
  widget.addSpacer();
  if (others > 0) addText(widget, `之后还有 ${others} 节`, { size: 10, color: C.faint });
}

function renderWeek(widget, C, payload, now, family) {
  const columns = weekColumns(payload, now);
  const large = family === "large" || family === "extraLarge";
  const perDay = large ? 6 : 3;
  // 中 / 大号内容区宽约 293（iPhone SE）~ 336pt：按窄的估，宁可留白也别被截断
  const width = family === "extraLarge" ? 680 : 296;
  const colW = Math.floor((width - (columns.length - 1) * 4) / Math.max(columns.length, 1));
  widget.addSpacer(8);
  const row = widget.addStack();
  row.spacing = 4;
  row.topAlignContent();
  for (const col of columns) {
    const stack = row.addStack();
    stack.layoutVertically();
    stack.size = new Size(colW, 0);
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
    const t = widget.addText(focus ? focus.start : "—");
    t.font = Font.boldSystemFont(14);
    t.centerAlignText();
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
  const rest = plan.lessons.filter((l) => l.state === "later").length;
  if (rest) addText(widget, `之后还有 ${rest} 节`, { size: 11 });
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
  widget.url = siteUrl();
  widget.refreshAfterDate = new Date(nextRefreshMs(payload, now));
  const plan = mode === "week"
    ? { title: "本周", day: resolveDays(payload, now).today, lessons: [] }
    : buildPlan(payload, mode, now);
  if (accessory) {
    renderAccessory(widget, plan, family);
    return widget;
  }
  header(widget, C, plan, payload, offline, family);
  if (mode === "week") renderWeek(widget, C, payload, now, family);
  else if (family === "small") renderSmall(widget, C, plan);
  else renderList(widget, C, plan, family);
  return widget;
}

// ---------- 读取数据 ----------

async function load() {
  const fm = FileManager.local();
  const cachePath = fm.joinPath(fm.cacheDirectory(), "nufe-kebiao-widget.json");
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

async function main() {
  const family = config.widgetFamily || "medium";
  const mode = parseMode(args.widgetParameter, family);
  let widget;
  if (!/^https?:\/\//.test(WIDGET_URL)) {
    widget = errorWidget("请从网站「设置 → 桌面小组件」复制脚本，里面会带上你的专属链接");
  } else {
    const result = await load();
    widget = result.error ? errorWidget(result.error)
      : buildWidget(result.data, { family, mode, offline: result.offline });
  }
  if (config.runsInWidget) {
    Script.setWidget(widget);
  } else if (mode === "week") {
    await widget.presentLarge();
  } else {
    await widget.presentMedium();
  }
}

if (typeof ListWidget === "undefined") {
  // Node 测试：只导出纯逻辑
  globalThis.__nufeWidget = { parseMode, shanghaiNow, resolveDays, annotate, buildPlan, weekColumns, nextRefreshMs };
} else {
  main().catch((e) => {
    Script.setWidget(errorWidget(`出错了：${e && e.message ? e.message : e}`));
  }).finally(() => Script.complete());
}
