/* 在样式表加载前应用主题，避免深色模式用户看到一闪而过的白屏。
 * CSP 只允许外部脚本，这里保持为独立的经典脚本（不参与模块依赖）。 */
(function () {
  try {
    var mode = window.localStorage.getItem("kb_theme");
    if (mode !== "light" && mode !== "dark") mode = "auto";
    var dark = mode === "dark"
      || (mode === "auto" && window.matchMedia
        && window.matchMedia("(prefers-color-scheme: dark)").matches);
    var theme = dark ? "dark" : "light";
    var root = document.documentElement;
    root.setAttribute("data-theme", theme);
    root.setAttribute("data-theme-mode", mode);
    root.style.colorScheme = theme;
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", dark ? "#0b0e13" : "#f5f6fa");
  } catch {
    /* 隐私模式下 localStorage 可能不可用：保持 CSS 默认主题 */
  }
})();

// 模块脚本没加载成功时（老浏览器 / 网络问题 / 被拦截），不要让用户一直看骨架屏
window.setTimeout(function () {
  try {
    var app = document.getElementById("app");
    if (!app || !app.querySelector(".boot-screen") || app.querySelector(".boot-error")) return;
    var tip = document.createElement("p");
    tip.className = "boot-error";
    tip.textContent = "页面加载失败。请刷新重试；若持续失败，请检查网络或浏览器是否支持 ES modules。";
    app.append(tip);
  } catch {
    /* 忽略：不能因为兜底提示再抛错 */
  }
}, 8000);
