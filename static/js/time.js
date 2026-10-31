/**
 * 日期 / 时间工具。全部按浏览器本地时区计算，避免把“第几周”算错。
 * 纯函数，不碰 DOM，方便 node --test 直接测试。
 */

export const WEEKDAY_LONG = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];
export const WEEKDAY_SHORT = ["一", "二", "三", "四", "五", "六", "日"];
const DAY_MINUTES = 24 * 60;

export function parseISODate(value) {
  if (typeof value !== "string") return null;
  const m = /^(\d{4})-(\d{1,2})-(\d{1,2})/.exec(value.trim());
  if (!m) return null;
  const year = Number(m[1]);
  const month = Number(m[2]);
  const day = Number(m[3]);
  if (month < 1 || month > 12 || day < 1 || day > 31) return null;
  const date = new Date(year, month - 1, day);
  if (date.getFullYear() !== year || date.getMonth() !== month - 1 || date.getDate() !== day) return null;
  return date;
}

export function startOfDay(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

export function addDays(date, count) {
  const next = startOfDay(date);
  next.setDate(next.getDate() + count);
  return next;
}

export function sameDay(a, b) {
  return Boolean(a && b)
    && a.getFullYear() === b.getFullYear()
    && a.getMonth() === b.getMonth()
    && a.getDate() === b.getDate();
}

/** 周一 = 0 … 周日 = 6 */
export function dayIndex(date) {
  return (date.getDay() + 6) % 7;
}

function utcDayNumber(date) {
  return Math.floor(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()) / 86_400_000);
}

/** 自然日差（不受夏令时影响） */
export function daysBetween(from, to) {
  return utcDayNumber(to) - utcDayNumber(from);
}

/** 第 1 周周一 → 某天所在周次（可能为 0 或负数，由调用方决定是否夹紧） */
export function weekOf(date, week1Monday) {
  const first = parseISODate(week1Monday);
  if (!first) return null;
  return Math.floor(daysBetween(first, date) / 7) + 1;
}

export function weekStart(week1Monday, week) {
  const first = parseISODate(week1Monday);
  if (!first) return null;
  return addDays(first, (week - 1) * 7);
}

export function minutesOf(value) {
  if (typeof value !== "string") return null;
  const m = /^\s*(\d{1,2}):(\d{2})/.exec(value);
  if (!m) return null;
  const hour = Number(m[1]);
  const minute = Number(m[2]);
  if (hour > 23 || minute > 59) return null;
  return hour * 60 + minute;
}

export function formatMinutes(total) {
  const minutes = Math.max(0, Math.min(DAY_MINUTES - 1, Math.round(total)));
  return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
}

export function nowMinutes(date = new Date()) {
  return date.getHours() * 60 + date.getMinutes();
}

export function monthDay(date) {
  return `${date.getMonth() + 1}/${date.getDate()}`;
}

export function formatWeekRange(monday) {
  if (!monday) return "";
  return `${monthDay(monday)} – ${monthDay(addDays(monday, 6))}`;
}

export function formatDateTime(timestamp) {
  if (!timestamp) return "—";
  const date = new Date(timestamp * 1000);
  if (Number.isNaN(date.getTime())) return "—";
  return `${monthDay(date)} ${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

export function relativeTime(timestamp, now = Date.now()) {
  if (!timestamp) return "从未";
  const diff = Math.floor(now / 1000 - timestamp);
  if (!Number.isFinite(diff)) return "从未";
  if (diff < -120) return formatDateTime(timestamp); // 未来时间直接显示日期，不写成“刚刚”
  if (diff < 0) return "即将";
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86_400) return `${Math.floor(diff / 3600)} 小时前`;
  if (diff < 7 * 86_400) return `${Math.floor(diff / 86_400)} 天前`;
  return formatDateTime(timestamp);
}

export function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}
