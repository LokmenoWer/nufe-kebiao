/**
 * 小组件版面预览：在 Scriptable 替身里把周视图 / 铺满按各机型的小组件尺寸画一遍，输出一张 HTML。
 * 用浏览器（macOS 上就是 iOS 同一套 SF / 苹方字体）打开，页面会把每一处会被截成“…”或切掉的文字标红，
 * 顶上汇总个数——没有 iPhone 也能检查各机型、各种课表下有没有字放不下。
 *
 *   node scripts/widget_preview.mjs [输出路径，默认 debug/widget-preview.html]
 *
 * 课表用几组典型的一周；仓库根目录有 my.json（本人课表导出，不进 git）时再加上它里课最多的那周。
 */
import fs from "node:fs";
import path from "node:path";

import { runWidgetScript } from "../tests/js/scriptable-shim.js";

const OUT = process.argv[2] || "debug/widget-preview.html";
const NOW = Date.UTC(2026, 9, 26, 2, 30); // 2026-10-26 周一 10:30 北京时间
const DATES = ["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30", "2026-10-31", "2026-11-01"];
const COLORS = ["#4b63e0", "#0f8b8d", "#c2410c", "#7c3aed", "#be185d", "#15803d", "#a16207"];

// 南财节次表（开始 / 结束 → 第几节）
const UNIT_START = { "08:20": 1, "09:05": 2, "10:00": 3, "10:45": 4, "11:30": 5, "13:30": 6, "14:15": 7, "15:10": 8, "15:55": 9, "16:45": 10, "18:30": 11, "19:15": 12, "20:05": 13 };
const UNIT_END = { "09:00": 1, "09:45": 2, "10:40": 3, "11:25": 4, "12:15": 5, "14:10": 6, "14:55": 7, "15:50": 8, "16:35": 9, "17:25": 10, "19:10": 11, "20:00": 12, "20:50": 13 };
const units = (s, e) => (UNIT_START[s] && UNIT_END[e] ? (UNIT_START[s] === UNIT_END[e] ? `${UNIT_START[s]}` : `${UNIT_START[s]}–${UNIT_END[e]}`) : "");
let colorIndex = 0;
const L = (name, start, end, room, teachers = "张老师") => ({
  name, start, end, room, teachers, units: units(start, end), color: COLORS[colorIndex++ % COLORS.length],
});

function weekOf(byDay) {
  return { week: 8, monday: DATES[0], days: DATES.map((date, i) => ({ date, weekday: i + 1, week: 8, lessons: byDay[i + 1] || [] })) };
}

const WEEKS = {
  "截图那周（下午晚上）": weekOf({
    1: [L("中外古典园林史", "18:30", "20:50", "")],
    2: [L("创新创业实践：财会视角", "13:30", "14:55", "德业楼Z4-102"), L("财务审计", "15:10", "17:25", "德业楼Z4-116")],
    4: [L("经济学前沿", "13:30", "14:55", "德经楼Z1-113"), L("信用管理学", "15:10", "17:25", "博文楼JA205")],
    5: [L("成本管理会计", "14:15", "16:35", "德济楼Z2-113")],
    6: [L("财务审计", "14:15", "16:35", "博文楼JA212")],
  }),
  "从早到晚排满": weekOf({
    1: [L("高等数学", "08:20", "09:45", "德业楼Z4-106"), L("概率论与数理统计", "10:00", "12:15", "德正楼Z3-117"), L("大学英语", "14:15", "16:35", "博文楼JA108"), L("微观经济学", "18:30", "20:50", "德经楼Z1-101")],
    2: [L("宏观经济学", "10:00", "12:15", "德经楼Z1-113"), L("统计学", "15:10", "16:35", "德济楼Z2S206"), L("会计学原理", "18:30", "20:50", "德业楼Z4-201")],
    3: [L("线性代数", "09:05", "12:15", "德正楼Z3-201"), L("金融学", "14:15", "16:35", "博远楼JC105")],
    4: [L("管理学", "13:30", "16:35", "德业楼Z4-116")],
    5: [L("税法", "10:00", "11:25", "德济楼Z2-113"), L("经济法", "13:30", "14:55", "博文楼JA212"), L("体育", "18:30", "20:00", "体育馆")],
  }),
  "周末有课（7 列）+ 撞课": weekOf({
    1: [L("食品营养学（双语）", "13:30", "14:55", "德正楼Z3-117"), L("食品化学", "13:30", "16:35", "食工楼203")],
    3: [L("数据结构", "08:20", "09:45", "德业楼Z4-106")],
    6: [L("选修：中国传统文化", "08:20", "09:45", "博文楼JA108")],
    7: [L("形势与政策", "13:30", "14:55", "博远楼JC105")],
  }),
};

// my.json：本人的教务导出（不进 git）。取课最多的那一周
if (fs.existsSync("my.json")) {
  const acts = JSON.parse(fs.readFileSync("my.json", "utf8")).activities || [];
  const count = new Map();
  for (const a of acts) for (const w of a.weekIndexes || []) count.set(w, (count.get(w) || 0) + 1);
  const best = [...count.entries()].sort((a, b) => b[1] - a[1])[0];
  if (best) {
    const pad = (t) => String(t).padStart(5, "0");
    const byDay = {};
    for (const a of acts.filter((x) => (x.weekIndexes || []).includes(best[0]))) {
      const l = L(a.courseName, pad(a.startTime), pad(a.endTime), a.room || a.building || "", (a.teachers || []).join("、"));
      l.units = a.endUnit !== a.startUnit ? `${a.startUnit}–${a.endUnit}` : `${a.startUnit}`;
      (byDay[a.weekday] ||= []).push(l);
    }
    for (const d of Object.values(byDay)) d.sort((a, b) => a.start.localeCompare(b.start));
    WEEKS[`my.json 第 ${best[0]} 周`] = weekOf(byDay);
  }
}

const PHONES = [[440, 956, "16/17 Pro Max"], [430, 932, "15 Plus / 14 Pro Max"], [420, 912, "Air"], [414, 896, "11 / XR"], [414, 736, "8 Plus"], [402, 874, "16/17 Pro"], [393, 852, "15 / 16"], [390, 844, "13 / 14"], [375, 812, "X / 11 Pro"], [375, 667, "SE / 8"], [360, 780, "13 mini"], [320, 693, "放大显示"]];
const PADS = [[1024, 1366, "iPad Pro 12.9"], [820, 1180, "iPad Air"], [744, 1133, "iPad mini"]];

const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const color = (c) => {
  if (!c) return "";
  const x = c.light || c;
  if (!x.hex) return "";
  const hex = x.hex.replace("#", "");
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16));
  return `rgba(${r},${g},${b},${x.alpha ?? 1})`;
};
const WEIGHTS = { boldSystemFont: 700, semiboldSystemFont: 600, mediumSystemFont: 500, systemFont: 400 };

/** 替身里的节点 → HTML。尺寸为 0 的一边不定死（Scriptable 里 0 表示按内容）。 */
function html(node) {
  if (node.kind === "spacer") {
    return node.length === undefined ? '<i class="sp flex"></i>' : `<i class="sp" style="flex:0 0 ${node.length}px"></i>`;
  }
  if (node.kind === "text") {
    const f = node.font || { name: "systemFont", size: 12 };
    const lines = node.lineLimit || 0;
    const style = [`font-size:${f.size}px`, `font-weight:${WEIGHTS[f.name] || 400}`, `color:${color(node.textColor) || "#1a1d24"}`];
    if (lines) style.push(`-webkit-line-clamp:${lines}`, `max-height:${lines * 2}em`);
    return `<span class="t${lines ? " clamp" : ""}" data-lines="${lines}" style="${style.join(";")}">${esc(node.text)}</span>`;
  }
  if (node.kind === "date") return `<span class="t" style="font-size:12px">0:42:00</span>`;
  const vertical = node.kind === "widget" || node.vertical;
  const style = [`flex-direction:${vertical ? "column" : "row"}`, `gap:${node.spacing || 0}px`];
  const size = node.size;
  const fixed = size && (size.w || size.h);
  if (size && size.w) style.push(`width:${size.w}px`, "flex-shrink:0");
  if (size && size.h) style.push(`height:${size.h}px`);
  if (node.padding) style.push(`padding:${node.padding.map((p) => `${p}px`).join(" ")}`);
  if (node.backgroundColor) style.push(`background:${color(node.backgroundColor)}`);
  if (node.cornerRadius) style.push(`border-radius:${node.cornerRadius}px`);
  if (node.borderWidth) style.push(`box-shadow:inset 0 0 0 ${node.borderWidth}px ${color(node.borderColor)}`);
  // 横排默认竖直居中；竖排靠左。定死尺寸的格子里内容整体居中（SwiftUI frame 的行为）
  style.push(`align-items:${vertical ? "flex-start" : node.align === "top" ? "flex-start" : "center"}`);
  if (fixed) style.push("justify-content:center");
  const kids = (node.children || []).map((c) => html(c)).join("");
  return `<div class="s${fixed ? " fixed" : ""}" style="${style.join(";")}">${kids}</div>`;
}

/** 一张课程卡片里的字（卡片是带圆角和底色的定尺寸竖排）。 */
function chipTexts(widget) {
  const chips = [];
  const walk = (n) => {
    if (n.kind === "stack" && n.vertical && n.cornerRadius === 6 && n.backgroundColor) chips.push(n.texts());
    for (const c of n.children || []) walk(c);
  };
  walk(widget);
  return chips;
}

/**
 * 写全了没有：每节课要有一张卡片写着完整课名、起止时间、教室编号（楼名放得下才写，不算缺）。
 * 撞课被记成“+1”的那节不算（它画不出来）。返回缺了什么的清单。
 */
function missingInfo(widget, week) {
  const chips = chipTexts(widget);
  const out = [];
  const days = week.days.filter((d, i) => i < 5 || week.days.slice(i).some((x) => x.lessons.length));
  for (const d of days) {
    for (const l of d.lessons) {
      // 撞课（和同一天别的课时间重叠）：只画得出一张，另一节记成“+1”，不算
      if (d.lessons.some((o) => o !== l && o.start < l.end && l.start < o.end)) continue;
      const code = l.room.replace(/^[\u4e00-\u9fff]+(?=[A-Za-z])/, "");
      const i = chips.findIndex((t) => t.some((x) => x === l.name) && (!code || t.some((x) => x.endsWith(code))));
      const j = i >= 0 ? i : chips.findIndex((t) => t.some((x) => x === l.name));
      if (j < 0) { out.push(`${l.name}：没有卡片 / 课名不全`); continue; }
      const [chip] = chips.splice(j, 1);
      const lack = [];
      // “–09:45”：开始时间在时间轴上和卡片顶边对齐，也算写全
      if (!chip.some((x) => (x.startsWith(l.start) && x.includes(l.end)) || x.startsWith(`–${l.end}`))) lack.push("起止时间");
      if (l.room && !chip.some((x) => x.endsWith(code))) lack.push("教室");
      if (lack.length) out.push(`${l.name}：缺${lack.join("、")}`);
    }
  }
  return out;
}

const sections = [];
const report = {};
for (const [weekName, week] of Object.entries(WEEKS)) {
  const cards = [];
  for (const [w, h, label] of [...PHONES, ...PADS]) {
    const families = w >= 600 ? ["large", "extraLarge"] : ["medium", "large"];
    for (const family of families) {
      for (const parameter of ["week", "full"]) {
        const payload = {
          v: 1, status: "ok", semester: "2026-2027-1", semester_id: 161, updated_at: NOW / 1000 - 600,
          script_version: "sha-000000000001", term: { first_day: "2026-09-07", last_day: "2027-01-08" },
          today: { date: DATES[0], weekday: 1, week: 8, lessons: week.days[0].lessons }, tomorrow: week.days[1], week,
        };
        const { widget } = await runWidgetScript({
          payload, family, parameter, url: "https://kb.example.com/widget/tok.json", nowMs: NOW, screen: { width: w, height: h },
        });
        const missing = missingInfo(widget, week);
        const key = `${family} ${parameter}`;
        report[key] ||= { layouts: 0, incomplete: 0, cards: 0, examples: [] };
        report[key].layouts += 1;
        if (missing.length) {
          report[key].incomplete += 1;
          report[key].cards += missing.length;
          if (report[key].examples.length < (process.env.ALL ? 1e9 : 6)) report[key].examples.push(`${weekName} · ${label} → ${missing.join("；")}`);
        }
        cards.push(`<figure data-name="${esc(`${weekName} · ${label} ${w}×${h} · ${family} · ${parameter}`)}" data-missing="${esc(missing.join("；"))}">
<figcaption>${esc(label)} ${w}×${h} · ${family} · ${parameter}${missing.length ? ` · <b style="color:#e8590c">没写全 ${missing.length} 张</b>` : ""}</figcaption>
<div class="widget" data-family="${family}" data-screen="${w}x${h}">${html(widget)}</div></figure>`);
      }
    }
  }
  sections.push(`<h2>${esc(weekName)}</h2><div class="grid">${cards.join("\n")}</div>`);
}

// 小组件尺寸：和脚本同一张表（从脚本里取，免得两处不一致）
const ctx = { console };
ctx.globalThis = ctx;
const vm = await import("node:vm");
vm.runInNewContext(fs.readFileSync("scriptable/nufe-kebiao-widget.js", "utf8"), ctx);
const sizeFor = ctx.__nufeWidget.widgetSizeFor;

const page = `<!doctype html><meta charset="utf-8"><title>小组件版面预览</title>
<style>
body{font-family:system-ui,-apple-system,"PingFang SC",sans-serif;margin:16px;background:#e9ebf0;color:#1a1d24}
#sum{position:sticky;top:0;background:#fff;padding:8px 12px;border-radius:8px;z-index:9;font-size:14px;box-shadow:0 1px 4px #0002}
.grid{display:flex;flex-wrap:wrap;gap:16px}
figure{margin:0}figcaption{font-size:12px;color:#5b6372;margin-bottom:4px}
.widget{background:#f6f7fb;border-radius:20px;overflow:hidden;display:flex;flex-direction:column;justify-content:center}
.widget>.s{width:100%;height:100%}
.s{display:flex;box-sizing:border-box;min-width:0;min-height:0}
.s.fixed{overflow:hidden}
.sp{display:block;flex-shrink:0}.sp.flex{flex:1 0 8px}
.t{display:block;white-space:pre-wrap;line-height:normal;overflow:hidden;flex-shrink:0;max-width:100%;overflow-wrap:anywhere}
.t.clamp{display:-webkit-box;-webkit-box-orient:vertical}
.bad{outline:2px solid #e03131;background:#ffe3e3}
</style>
<div id="sum">检查中…</div>
${sections.join("\n")}
<script>
const SIZES = ${JSON.stringify(Object.fromEntries([...PHONES, ...PADS].flatMap(([w, h]) => ["medium", "large", "extraLarge"].map((f) => [`${w}x${h}/${f}`, { ...sizeFor({ w, h }, f) }]))))};
for (const el of document.querySelectorAll(".widget")) {
  const s = SIZES[el.dataset.screen + "/" + el.dataset.family];
  el.style.width = s.w + "px"; el.style.height = s.h + "px";
}
const bad = [];
for (const fig of document.querySelectorAll("figure")) {
  for (const t of fig.querySelectorAll(".t")) {
    // 被行数限制截掉、横向放不下、或者整个格子比它矮（文字被切）
    const box = t.getBoundingClientRect();
    let clipped = t.scrollHeight > t.clientHeight + 1 || t.scrollWidth > t.clientWidth + 1;
    for (let p = t.parentElement; p && !p.classList.contains("widget"); p = p.parentElement) {
      if (!p.classList.contains("fixed")) continue;
      const r = p.getBoundingClientRect();
      if (box.bottom > r.bottom + 1 || box.right > r.right + 1 || box.top < r.top - 1) clipped = true;
    }
    if (clipped) { t.classList.add("bad"); bad.push(fig.dataset.name + " → " + t.textContent.replace(/\\n/g, "⏎")); }
  }
}
window.__bad = bad;
document.getElementById("sum").textContent = bad.length ? "有 " + bad.length + " 处字放不下（标红）" : "全部放得下 ✓（" + document.querySelectorAll("figure").length + " 个版面）";
</script>`;
for (const [key, r] of Object.entries(report)) {
  console.log(`${key}：${r.layouts} 个版面里 ${r.incomplete} 个有没写全的卡片（共 ${r.cards} 张）`);
  for (const e of r.examples) console.log(`    ${e}`);
}
fs.mkdirSync(path.dirname(OUT), { recursive: true });
fs.writeFileSync(OUT, page);
console.log(`写好了 ${OUT}`);
