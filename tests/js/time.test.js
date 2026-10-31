import assert from "node:assert/strict";
import test from "node:test";

import {
  addDays,
  clamp,
  dayIndex,
  daysBetween,
  formatDateTime,
  formatMinutes,
  formatWeekRange,
  minutesOf,
  monthDay,
  parseISODate,
  relativeTime,
  sameDay,
  weekOf,
  weekStart,
} from "../../static/js/time.js";

test("parseISODate 只接受真实存在的日期", () => {
  const date = parseISODate("2026-09-07");
  assert.equal(date.getFullYear(), 2026);
  assert.equal(date.getMonth(), 8);
  assert.equal(date.getDate(), 7);
  assert.equal(parseISODate("2026-02-30"), null);
  assert.equal(parseISODate("2026/09/07"), null);
  assert.equal(parseISODate(null), null);
  const withTime = parseISODate("2026-09-07T10:00:00");
  assert.equal(withTime.getFullYear(), 2026);
  assert.equal(withTime.getDate(), 7);
});

test("日期加减与自然日差不受月份长度影响", () => {
  const start = parseISODate("2026-09-07");
  assert.equal(monthDay(addDays(start, 25)), "10/2");
  assert.equal(monthDay(addDays(start, 49)), "10/26");
  assert.equal(daysBetween(start, addDays(start, 51)), 51);
  assert.ok(sameDay(addDays(start, 0), start));
  assert.equal(dayIndex(start), 0); // 周一
  assert.equal(dayIndex(addDays(start, 6)), 6); // 周日
});

test("周次与周起点", () => {
  const monday = "2026-09-07";
  assert.equal(weekOf(new Date(2026, 8, 7), monday), 1);
  assert.equal(weekOf(new Date(2026, 8, 13), monday), 1);
  assert.equal(weekOf(new Date(2026, 8, 14), monday), 2);
  assert.equal(weekOf(new Date(2026, 9, 28), monday), 8);
  assert.equal(weekOf(new Date(2026, 9, 28), null), null);
  assert.equal(monthDay(weekStart(monday, 8)), "10/26");
  assert.equal(formatWeekRange(weekStart(monday, 8)), "10/26 – 11/1");
});

test("时间解析与格式化", () => {
  assert.equal(minutesOf("8:20"), 500);
  assert.equal(minutesOf("08:20:00"), 500);
  assert.equal(minutesOf("24:00"), null);
  assert.equal(minutesOf(""), null);
  assert.equal(formatMinutes(500), "08:20");
  assert.equal(formatMinutes(-10), "00:00");
  assert.equal(formatMinutes(24 * 60), "23:59");
  assert.equal(clamp(12, 1, 10), 10);
});

test("相对时间与时间戳格式化", () => {
  const now = new Date(2026, 9, 31, 12, 0, 0).getTime();
  const seconds = now / 1000;
  assert.equal(relativeTime(null, now), "从未");
  assert.equal(relativeTime(seconds - 20, now), "刚刚");
  assert.equal(relativeTime(seconds - 120, now), "2 分钟前");
  assert.equal(relativeTime(seconds - 7200, now), "2 小时前");
  assert.equal(relativeTime(seconds - 2 * 86400, now), "2 天前");
  assert.equal(relativeTime(seconds + 30, now), "即将");
  assert.equal(relativeTime(seconds + 3600, now), formatDateTime(seconds + 3600));
  assert.equal(formatDateTime(seconds), "10/31 12:00");
  assert.equal(formatDateTime(null), "—");
});
