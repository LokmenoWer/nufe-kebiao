/** localStorage 的安全封装：隐私模式 / 空间已满时静默失败，不拖垮整个页面。 */

export function readStorage(key) {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function writeStorage(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* 忽略：只是记不住偏好 */
  }
}

export function readViewPreference() {
  const view = readStorage("kb_view");
  return view === "week" || view === "day" ? view : null;
}

export function writeViewPreference(view) {
  if (view === "week" || view === "day") writeStorage("kb_view", view);
}

export function readSemesterPreference() {
  const value = Number(readStorage("kb_sem"));
  return Number.isInteger(value) && value > 0 ? value : null;
}

export function writeSemesterPreference(semesterId) {
  if (Number.isInteger(semesterId) && semesterId > 0) writeStorage("kb_sem", String(semesterId));
}
