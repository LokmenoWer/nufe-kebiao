import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

import { runWidgetScript } from "./scriptable-shim.js";

// 纯逻辑：脚本在没有 ListWidget 的环境里只导出函数
const ctx = { console };
ctx.globalThis = ctx;
vm.runInNewContext(fs.readFileSync(new URL("../../scriptable/nufe-kebiao-widget.js", import.meta.url), "utf8"), ctx);
const W = ctx.__nufeWidget;

// 2026-10-26（周一）10:30 北京时间
const NOW = Date.UTC(2026, 9, 26, 2, 30);
const L = (name, start, end, room = "A101", color = "#4b63e0") => ({ name, start, end, room, units: "", teachers: "教师A", color });
const day = (date, weekday, lessons, week = 8) => ({ date, weekday, week, lessons });
const PAYLOAD = {
  v: 1,
  status: "ok",
  semester: "2026-2027-1",
  semester_id: 161,
  updated_at: NOW / 1000 - 3600,
  script_version: "2026.11.01",
  term: { first_day: "2026-09-07", last_day: "2027-01-08" },
  today: day("2026-10-26", 1, [
    L("高等数学", "08:20", "09:45"), L("概率论", "10:00", "12:15", "B202", "#0f8b8d"),
    L("大学英语", "14:15", "16:35"), L("微观经济学", "18:30", "20:50"),
  ]),
  tomorrow: day("2026-10-27", 2, [L("宏观经济学", "10:00", "12:15", "德经楼Z1-101")]),
  week: {
    week: 8,
    monday: "2026-10-26",
    days: [
      day("2026-10-26", 1, [L("高等数学", "08:20", "09:45"), L("概率论", "10:00", "12:15")]),
      day("2026-10-27", 2, [L("宏观经济学", "10:00", "12:15")]),
      day("2026-10-28", 3, []), day("2026-10-29", 4, []), day("2026-10-30", 5, [L("金融学", "13:30", "14:55")]),
      day("2026-10-31", 6, []), day("2026-11-01", 7, []),
    ],
  },
  next_week: {
    week: 9,
    monday: "2026-11-02",
    days: [
      day("2026-11-02", 1, [L("高等数学", "08:20", "09:45")], 9), day("2026-11-03", 2, [], 9),
      day("2026-11-04", 3, [], 9), day("2026-11-05", 4, [], 9), day("2026-11-06", 5, [], 9),
      day("2026-11-07", 6, [], 9), day("2026-11-08", 7, [], 9),
    ],
  },
};

test("参数解析与尺寸兜底", () => {
  assert.equal(W.parseMode("", "medium"), "today");
  assert.equal(W.parseMode("明天", "medium"), "tomorrow");
  assert.equal(W.parseMode(" WEEK ", "large"), "week");
  assert.equal(W.parseMode("本周", "small"), "today"); // 小号放不下一周
  assert.equal(W.parseMode("week", "accessoryRectangular"), "today");
  assert.equal(W.parseMode("乱填", "large"), "today");
});

test("北京时间与课程状态", () => {
  const now = W.shanghaiNow(NOW);
  assert.equal(now.date, "2026-10-26");
  assert.equal(now.minutes, 10 * 60 + 30);
  assert.equal(now.weekday, 1);
  const states = W.annotate(PAYLOAD.today.lessons, now.minutes).map((l) => l.state);
  assert.deepEqual(states, ["past", "live", "next", "later"]);
});

test("今天：只列还没上完的；上完了预告明天", () => {
  const plan = W.buildPlan(PAYLOAD, "today", W.shanghaiNow(NOW));
  assert.deepEqual(plan.lessons.map((l) => l.name), ["概率论", "大学英语", "微观经济学"]);
  const evening = W.buildPlan(PAYLOAD, "today", W.shanghaiNow(Date.UTC(2026, 9, 26, 13, 0))); // 21:00
  assert.equal(evening.lessons.length, 0);
  assert.equal(evening.empty, "今天的课都上完了");
  assert.equal(evening.preview.lessons[0].name, "宏观经济学");
});

test("缓存跨过午夜：明天变今天；更旧就不显示过期的课", () => {
  const nextDay = W.shanghaiNow(Date.UTC(2026, 9, 27, 1, 0)); // 10-27 09:00
  const plan = W.buildPlan(PAYLOAD, "today", nextDay);
  assert.equal(plan.day.date, "2026-10-27");
  assert.equal(plan.lessons[0].name, "宏观经济学");
  const stale = W.buildPlan(PAYLOAD, "today", W.shanghaiNow(Date.UTC(2026, 9, 29, 1, 0)));
  assert.equal(stale.lessons.length, 0);
  assert.match(stale.empty, /过旧/);
});

test("周末：今天明天都没课时预告下次上课", () => {
  const sat = W.shanghaiNow(Date.UTC(2026, 9, 31, 2, 0)); // 10-31 周六 10:00
  const payload = {
    ...PAYLOAD,
    today: day("2026-10-31", 6, []),
    tomorrow: day("2026-11-01", 7, []),
    upcoming: day("2026-11-02", 1, [L("高等数学", "08:20", "09:45")], 9),
  };
  const plan = W.buildPlan(payload, "today", sat);
  assert.equal(plan.empty, "今天没有课");
  assert.equal(plan.preview.title, "下次上课 · 周一 11/2");
  assert.equal(plan.preview.lessons[0].state, "later");
  const tmr = W.buildPlan(payload, "tomorrow", sat);
  assert.equal(tmr.empty, "明天没有课");
  assert.equal(tmr.preview.title, "下次上课 · 周一 11/2");
});

test("下次刷新：最近的上下课时刻，最多 30 分钟", () => {
  const now = W.shanghaiNow(NOW);
  assert.equal(W.nextRefreshMs(PAYLOAD, now), NOW + 30 * 60000); // 12:15 下课还远，30 分钟后先刷一次
  const near = W.shanghaiNow(Date.UTC(2026, 9, 26, 4, 10)); // 12:10，12:15 下课
  assert.equal(W.nextRefreshMs(PAYLOAD, near), near.ms + 5 * 60000);
});

test("本周：没有周末课时只显示 5 天，今天高亮", () => {
  const cols = W.weekColumns(PAYLOAD.week, W.shanghaiNow(NOW));
  assert.equal(cols.length, 5);
  assert.equal(cols[0].isToday, true);
  assert.equal(cols[0].lessons[0].state, "past");
  assert.equal(cols[1].lessons[0].state, "later");
});

// ---- 整个脚本在 Scriptable 替身里跑一遍 ----

const URL_OK = "https://kb.example.com/widget/tok.json";

test("各尺寸 / 模式都能渲染并设置刷新时间与点击链接", async () => {
  for (const family of ["small", "medium", "large", "extraLarge", "accessoryRectangular", "accessoryInline", "accessoryCircular"]) {
    for (const parameter of ["today", "tomorrow", "week"]) {
      const { widget } = await runWidgetScript({ payload: PAYLOAD, family, parameter, url: URL_OK, nowMs: NOW });
      assert.ok(widget, `${family}/${parameter} 没有设置小组件`);
      assert.ok(widget.refreshAfterDate > new Date(NOW), `${family}/${parameter} 刷新时间不对`);
      assert.ok(widget.url.startsWith("https://kb.example.com/#sem=161"), widget.url);
      assert.ok(widget.texts().length > 0, `${family}/${parameter} 没有内容`);
    }
  }
});

test("明天模式不标“下一节”", async () => {
  const { widget } = await runWidgetScript({ payload: PAYLOAD, parameter: "tomorrow", url: URL_OK, nowMs: NOW });
  const t = widget.texts();
  assert.ok(t.includes("明天") && t.includes("宏观经济学") && !t.includes("下一节"));
});

test("今天（中号）：显示正在上和下一节，并带倒计时", async () => {
  const { widget } = await runWidgetScript({ payload: PAYLOAD, family: "medium", url: URL_OK, nowMs: NOW });
  const t = widget.texts();
  assert.ok(t.includes("今天") && t.includes("第 8 周"));
  assert.ok(t.includes("概率论") && t.includes("上课中") && t.includes("下一节"));
  assert.ok(!t.includes("高等数学")); // 已下课的不占位置
  // 正在上：距 12:15 下课的计时；下一节：14:15 开始的相对时间
  assert.ok(t.includes("[timer→2026-10-26T04:15:00.000Z]"), t.join("|"));
  assert.ok(t.includes("[relative→2026-10-26T06:15:00.000Z]"));
  // 点小组件 / 点某节课：打开网站当天的日视图
  assert.equal(widget.url, "https://kb.example.com/#sem=161&w=8&view=day&d=0");
  assert.ok(widget.urls().every((u) => u.startsWith("https://kb.example.com/#sem=161")));
});

test("小号与锁屏也有倒计时", async () => {
  const small = await runWidgetScript({ payload: PAYLOAD, family: "small", url: URL_OK, nowMs: NOW });
  assert.ok(small.widget.texts().includes("还剩 ") && small.widget.texts().includes("[timer→2026-10-26T04:15:00.000Z]"));
  const rect = await runWidgetScript({ payload: PAYLOAD, family: "accessoryRectangular", url: URL_OK, nowMs: NOW });
  assert.ok(rect.widget.texts().includes("[timer→2026-10-26T04:15:00.000Z]"));
  const circ = await runWidgetScript({ payload: PAYLOAD, family: "accessoryCircular", url: URL_OK, nowMs: NOW });
  assert.deepEqual(circ.widget.texts(), ["下课", "12:15"]);
});

test("本周（大号）：每天一列，课程名和教室都在", async () => {
  const { widget } = await runWidgetScript({ payload: PAYLOAD, family: "large", parameter: "week", url: URL_OK, nowMs: NOW });
  const t = widget.texts();
  for (const label of ["周一", "周二", "周三", "周四", "周五"]) assert.ok(t.includes(label), label);
  assert.ok(!t.includes("周六"));
  assert.ok(t.includes("金融学") && t.includes("宏观经济学"));
});

test("锁屏：显示下一节 / 正在上的课", async () => {
  const { widget } = await runWidgetScript({ payload: PAYLOAD, family: "accessoryInline", url: URL_OK, nowMs: NOW });
  assert.deepEqual(widget.texts(), ["10:00 概率论 @B202"]);
});

test("需要重新登录时在标题栏提示", async () => {
  const { widget } = await runWidgetScript({ payload: { ...PAYLOAD, status: "needs_rebind" }, url: URL_OK, nowMs: NOW });
  assert.ok(widget.texts().includes("需重新登录"));
});

test("离线时用缓存并标注；链接失效 / 没填链接时给出说明", async () => {
  const cache = new Map();
  await runWidgetScript({ payload: PAYLOAD, url: URL_OK, nowMs: NOW, cache });
  const offline = await runWidgetScript({ payload: PAYLOAD, url: URL_OK, nowMs: NOW, cache, fail: true });
  assert.ok(offline.widget.texts().includes("离线") && offline.widget.texts().includes("概率论"));
  const none = await runWidgetScript({ payload: PAYLOAD, url: URL_OK, nowMs: NOW, fail: true });
  assert.ok(none.widget.texts().some((x) => x.includes("连不上")));
  const gone = await runWidgetScript({ payload: { detail: "Not Found" }, status: 404, url: URL_OK, nowMs: NOW });
  assert.ok(gone.widget.texts().some((x) => x.includes("已失效")));
  const blank = await runWidgetScript({ payload: PAYLOAD, nowMs: NOW }); // 没经过网站替换链接
  assert.ok(blank.widget.texts().some((x) => x.includes("复制脚本")));
});


test("学期阶段：开学前倒计时、结课后提示", () => {
  const before = W.shanghaiNow(Date.UTC(2026, 8, 1, 2, 0)); // 9-1
  const empty = { ...PAYLOAD, today: day("2026-09-01", 2, [], null), tomorrow: day("2026-09-02", 3, [], null), upcoming: null };
  assert.deepEqual({ ...W.termState(PAYLOAD, before) }, { phase: "before", days: 6, first: "2026-09-07" });
  assert.equal(W.buildPlan(empty, "today", before).empty, "距开学还有 6 天");
  const after = W.shanghaiNow(Date.UTC(2027, 0, 20, 2, 0));
  const done = { ...PAYLOAD, today: day("2027-01-20", 3, [], 20), tomorrow: day("2027-01-21", 4, [], 20), upcoming: null };
  assert.equal(W.buildPlan(done, "today", after).empty, "本学期课程已结束");
  assert.equal(W.buildPlan(done, "tomorrow", after).empty, "本学期课程已结束");
});

test("周末的 week 模式改看下周；工作日看本周", () => {
  const sat = W.shanghaiNow(Date.UTC(2026, 9, 31, 2, 0));
  assert.equal(W.weekToShow(PAYLOAD, sat).title, "下周");
  assert.equal(W.weekToShow(PAYLOAD, W.shanghaiNow(NOW)).title, "本周");
  // 下周也没课：仍然显示本周
  const quiet = { ...PAYLOAD, next_week: { ...PAYLOAD.next_week, days: PAYLOAD.next_week.days.map((d) => ({ ...d, lessons: [] })) } };
  assert.equal(W.weekToShow(quiet, sat).title, "本周");
});

test("标题栏提示的优先级：登录失效 > 数据过旧 > 离线 > 可更新 > 周次", () => {
  const d = PAYLOAD.today;
  assert.equal(W.headerNote({ ...PAYLOAD, status: "captcha" }, true, d, "medium").text, "需重新登录");
  const old = { ...PAYLOAD, updated_at: Date.now() / 1000 - 3 * 86400 };
  assert.equal(W.headerNote(old, true, d, "medium").text, "3 天未更新");
  const fresh = { ...PAYLOAD, updated_at: Date.now() / 1000 };
  assert.equal(W.headerNote(fresh, true, d, "medium").text, "离线");
  assert.equal(W.headerNote({ ...fresh, script_version: "2099.01.01" }, false, d, "medium").text, "脚本可更新");
  assert.equal(W.headerNote(fresh, false, d, "medium").text, "第 8 周");
  assert.equal(W.headerNote(fresh, false, d, "small"), null);
});

test("工具函数：版本比较、缓存文件名、网站深链接", () => {
  assert.ok(W.isNewer("2026.11.02", "2026.11.01") && !W.isNewer("2026.11.01", "2026.11.01") && !W.isNewer(null, "x"));
  assert.notEqual(W.cacheName("https://a/widget/1.json"), W.cacheName("https://a/widget/2.json"));
  assert.match(W.cacheName("x"), /^nufe-kebiao-[0-9a-f]+\.json$/);
  assert.equal(W.siteLink("https://s/", { semester_id: 161 }, { week: 9, weekday: 3 }), "https://s/#sem=161&w=9&view=day&d=2");
  assert.equal(W.siteLink("https://s/", { semester_id: 161 }, { week: 9, weekday: 1 }, "week"), "https://s/#sem=161&w=9&view=week");
  assert.equal(W.lessonDate("2026-10-26", "08:20").toISOString(), "2026-10-26T00:20:00.000Z");
});

test("周末大号 week：显示下周，每天一列可点进当天", async () => {
  const sat = Date.UTC(2026, 9, 31, 2, 0);
  const payload = { ...PAYLOAD, today: day("2026-10-31", 6, []), tomorrow: day("2026-11-01", 7, []) };
  const { widget } = await runWidgetScript({ payload, family: "large", parameter: "week", url: URL_OK, nowMs: sat });
  assert.equal(widget.texts()[0], "下周");
  assert.equal(widget.texts()[1], "  11/2–11/8");
  const circ = await runWidgetScript({ payload: { ...payload, upcoming: PAYLOAD.next_week.days[0] }, family: "accessoryCircular", url: URL_OK, nowMs: sat });
  assert.deepEqual(circ.widget.texts(), ["周一", "08:20"]);
  assert.equal(widget.url, "https://kb.example.com/#sem=161&w=9&view=week");
  assert.ok(widget.urls().includes("https://kb.example.com/#sem=161&w=9&view=day&d=0"));
});

test("多个账号 / 脚本各自缓存，互不覆盖", async () => {
  const cache = new Map();
  await runWidgetScript({ payload: PAYLOAD, url: "https://kb.example.com/widget/a.json", nowMs: NOW, cache });
  await runWidgetScript({ payload: { ...PAYLOAD, semester: "另一个" }, url: "https://kb.example.com/widget/b.json", nowMs: NOW, cache });
  assert.equal(cache.size, 2);
});

test("在 Scriptable 里运行：菜单可预览，也能更新脚本", async () => {
  const preview = await runWidgetScript({ payload: PAYLOAD, url: URL_OK, nowMs: NOW, inApp: true, choose: ["预览本周（大号）"] });
  assert.equal(preview.presented.length, 1);
  assert.equal(preview.presented[0].presented, "large");
  assert.ok(preview.alerts[0].actions.includes("更新脚本") && preview.alerts[0].actions.includes("打开网站"));

  const newer = `// x\nconst WIDGET_URL = "${URL_OK}";\nconst SCRIPT_VERSION = "2099.01.01";\n`;
  const cache = new Map();
  const up = await runWidgetScript({
    payload: { ...PAYLOAD, script_version: "2099.01.01" }, url: URL_OK, nowMs: NOW, inApp: true,
    choose: ["更新脚本"], scriptBody: newer, cache,
  });
  assert.match(up.alerts[0].message, /可更新到 2099\.01\.01/);
  assert.equal(cache.get("/Documents/南财课表.js"), newer); // 没开 iCloud：写到本地脚本文件
  assert.equal(up.alerts[1].title, "已更新");

  // 下载回来的不是本人的脚本（比如链接被换过）：拒绝覆盖
  const bad = await runWidgetScript({
    payload: PAYLOAD, url: URL_OK, nowMs: NOW, inApp: true, choose: ["更新脚本"],
    scriptBody: "const SCRIPT_VERSION = \"x\"; // other user", cache: new Map(),
  });
  assert.equal(bad.alerts[1].title, "出错了");
});
