/** 课表数据 → 周视图/日视图需要的课程块。纯函数，便于测试。 */

import { formatMinutes, minutesOf } from "./time.js";

export const PALETTE = [
  "#4b63e0", "#0f8b8d", "#2f9e44", "#e8590c", "#ae3ec9", "#1971c2",
  "#c2255c", "#5c940d", "#e67700", "#6741d9", "#0b7285", "#d9480f",
  "#087f5b", "#a61e4d", "#3b5bdb", "#1098ad", "#74b816", "#f08c00",
  "#9c36b5", "#495057",
];

const FALLBACK_FIRST_MINUTE = 8 * 60;
const FALLBACK_UNIT_MINUTES = 55;

function hashName(name) {
  // FNV-1a 32 位：分布比 h * 31 均匀，同名字的颜色永远一致
  let hash = 2166136261;
  for (const ch of String(name || "")) {
    hash ^= ch.codePointAt(0) || 0;
    hash = Math.imul(hash, 16777619) >>> 0;
  }
  return hash >>> 0;
}

export function colorFor(name) {
  return PALETTE[hashName(name) % PALETTE.length];
}

/**
 * 给整份课表分配颜色：先按课程名排序，再从哈希位置开始找第一个未使用的颜色。
 * 课程数不超过调色板大小时，同一学期内不会出现两门课同色。
 */
export function buildColorMap(activities) {
  const names = [...new Set((activities || []).map((a) => String(a?.courseName || "")))].sort();
  const used = new Set();
  const map = new Map();
  for (const name of names) {
    let index = hashName(name) % PALETTE.length;
    for (let tries = 0; tries < PALETTE.length && used.has(index); tries += 1) {
      index = (index + 1) % PALETTE.length;
    }
    used.add(index);
    map.set(name, PALETTE[index]);
  }
  return map;
}

export function initialOf(name) {
  const trimmed = String(name || "").trim();
  if (!trimmed) return "我";
  return trimmed.length >= 3 ? trimmed.slice(-2) : trimmed;
}

export function formatUnits(lesson) {
  if (!lesson) return "";
  const start = lesson.startUnit;
  const end = lesson.endUnit;
  if (!start) return "";
  return `第 ${start}${end && end !== start ? `–${end}` : ""} 节`;
}

/** 课程块的起止时间，例如 "10:00–11:35"。 */
export function formatTimeRange(item) {
  if (!item || !Number.isFinite(item.start) || !Number.isFinite(item.end)) return "";
  return `${formatMinutes(item.start)}–${formatMinutes(item.end)}`;
}

/** 节次 + 具体时间，例如 "第 3–4 节 · 10:00–11:35"；缺哪个就只显示另一个。 */
export function formatUnitsWithTime(item) {
  return [formatUnits(item?.activity), formatTimeRange(item)].filter(Boolean).join(" · ");
}

/** 从有起止时间的课里推出每节课的上下课时间，给缺时间的课兜底。 */
export function unitTimeMap(activities) {
  const starts = new Map();
  const ends = new Map();
  for (const activity of activities || []) {
    const startUnit = Number(activity?.startUnit);
    const endUnit = Number(activity?.endUnit);
    if (Number.isInteger(startUnit) && activity?.startTime && !starts.has(startUnit)) {
      starts.set(startUnit, activity.startTime);
    }
    if (Number.isInteger(endUnit) && activity?.endTime && !ends.has(endUnit)) {
      ends.set(endUnit, activity.endTime);
    }
  }
  return { starts, ends };
}

function validWeeks(activity) {
  const raw = Array.isArray(activity?.weekIndexes) ? activity.weekIndexes : [];
  return raw
    .map((v) => Number(v))
    .filter((v) => Number.isInteger(v) && v >= 1 && v <= 60);
}

/** 某一周的全部课程块，按星期、开始时间排序。 */
export function lessonsOfWeek(timetable, week) {
  if (!timetable || !Array.isArray(timetable.activities)) return [];
  const targetWeek = Number(week);
  const { starts, ends } = unitTimeMap(timetable.activities);
  const colors = buildColorMap(timetable.activities);
  const items = [];

  for (const activity of timetable.activities) {
    if (!activity || !validWeeks(activity).includes(targetWeek)) continue;
    const weekday = Number(activity.weekday);
    const startUnit = Number(activity.startUnit);
    if (!Number.isInteger(weekday) || weekday < 1 || weekday > 7) continue;
    if (!Number.isInteger(startUnit) || startUnit < 1) continue;
    const endUnit = Math.max(startUnit, Number(activity.endUnit) || startUnit);

    let start = minutesOf(activity.startTime);
    if (start === null) start = minutesOf(starts.get(startUnit));
    if (start === null) start = FALLBACK_FIRST_MINUTE + (startUnit - 1) * FALLBACK_UNIT_MINUTES;

    let end = minutesOf(activity.endTime);
    if (end === null) end = minutesOf(ends.get(endUnit));
    if (end === null || end <= start) end = start + Math.max(1, endUnit - startUnit + 1) * 45;

    items.push({
      activity,
      courseName: activity.courseName || "未命名课程",
      day: weekday - 1,
      weekday,
      startUnit,
      endUnit,
      start,
      end,
      color: colors.get(String(activity.courseName || "")) || colorFor(activity.courseName),
    });
  }

  return items.sort((a, b) => a.day - b.day || a.start - b.start || b.end - a.end);
}

/** 整份课表一共上到第几周（至少 16 周，最多 60 周）。 */
export function maxWeekOf(timetable) {
  let max = 16;
  for (const activity of timetable?.activities || []) {
    for (const week of validWeeks(activity)) max = Math.max(max, week);
  }
  return Math.min(60, max);
}

export function groupByDay(items, dayCount = 7) {
  const groups = Array.from({ length: dayCount }, () => []);
  for (const item of items) {
    if (item.day >= 0 && item.day < dayCount) groups[item.day].push(item);
  }
  return groups;
}
