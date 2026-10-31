/** 主题：auto / light / dark。默认跟随系统，可单独覆盖。 */

import { readStorage, writeStorage } from "./store.js";

export const THEME_MODES = ["auto", "light", "dark"];
const KEY = "kb_theme";
const DARK_QUERY = "(prefers-color-scheme: dark)";

let mode = "auto";
let media = null;
let initialized = false;

function resolve(value) {
  if (value === "light" || value === "dark") return value;
  return media?.matches ? "dark" : "light";
}

function apply() {
  const theme = resolve(mode);
  document.documentElement.setAttribute("data-theme", theme);
  document.documentElement.setAttribute("data-theme-mode", mode);
  document.documentElement.style.colorScheme = theme;
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", theme === "dark" ? "#0b0e13" : "#f5f6fa");
  window.dispatchEvent(new CustomEvent("kb:theme", { detail: { mode, theme } }));
}

function normalize(value) {
  return THEME_MODES.includes(value) ? value : "auto";
}

export function initTheme() {
  mode = normalize(readStorage(KEY));
  if (!initialized && typeof window.matchMedia === "function") {
    media = window.matchMedia(DARK_QUERY);
    media.addEventListener?.("change", () => {
      if (mode === "auto") apply();
    });
    window.addEventListener("storage", (event) => {
      if (event.key !== KEY) return;
      mode = normalize(event.newValue);
      apply();
    });
    initialized = true;
  }
  apply();
  return mode;
}

export function getThemeMode() {
  return mode;
}

export function getResolvedTheme() {
  return resolve(mode);
}

export function setThemeMode(value, { persist = true } = {}) {
  mode = normalize(value);
  if (persist) writeStorage(KEY, mode);
  apply();
  return mode;
}

/** 顶栏按钮：在明/暗之间切换（保留 auto 仅作为设置页选项）。 */
export function toggleTheme() {
  return setThemeMode(getResolvedTheme() === "dark" ? "light" : "dark");
}
