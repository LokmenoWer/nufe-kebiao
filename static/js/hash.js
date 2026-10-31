/**
 * 把当前视图状态写进 URL hash，刷新 / 分享时能回到同一周、同一天。
 * 例：#sem=161&w=8&view=day&d=3
 */

const VIEWS = new Set(["week", "day"]);

export function parseHash(hash) {
  const raw = String(hash || "").replace(/^#\/?/, "");
  if (!raw) return {};
  const params = new URLSearchParams(raw);
  const out = {};

  const numberParam = (name) => {
    const value = params.get(name);
    if (value === null || value.trim() === "") return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  };

  const sem = numberParam("sem");
  if (Number.isInteger(sem) && sem > 0) out.semesterId = sem;

  const week = numberParam("w");
  if (Number.isInteger(week) && week > 0) out.week = week;

  const day = numberParam("d");
  if (Number.isInteger(day) && day >= 0 && day <= 6) out.day = day;

  const view = params.get("view");
  if (VIEWS.has(view)) out.view = view;

  return out;
}

export function buildHash({ semesterId, week, day, view } = {}) {
  const params = new URLSearchParams();
  if (Number.isInteger(semesterId) && semesterId > 0) params.set("sem", String(semesterId));
  if (Number.isInteger(week) && week > 0) params.set("w", String(week));
  if (VIEWS.has(view)) params.set("view", view);
  if (view === "day" && Number.isInteger(day) && day >= 0 && day <= 6) params.set("d", String(day));
  const query = params.toString();
  return query ? `#${query}` : "";
}
