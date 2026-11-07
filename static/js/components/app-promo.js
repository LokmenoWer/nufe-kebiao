/** 安卓手机打开本站时的提示：App 2.0 起可独立登录学校，不必先登录本站就能下载安装。 */

import { h } from "../dom.js";
import { icon } from "../icons.js";
import { readStorage, writeStorage } from "../store.js";

const RELEASE_URL = "https://github.com/LokmenoWer/nufe-kebiao/releases/latest";
const HIDE_KEY = "kb_app_promo_hidden_at";
const HIDE_FOR_MS = 30 * 24 * 3600 * 1000;

/** 鸿蒙 NEXT 的 UA 不含 Android，装不了 APK，自然不显示；鸿蒙 4 及以前仍带 Android，能装。 */
export function isAndroid(ua = navigator.userAgent) {
  return /\bAndroid\b/i.test(ua);
}

function hiddenRecently(now = Date.now()) {
  const at = Number(readStorage(HIDE_KEY));
  return Number.isFinite(at) && at > 0 && now - at < HIDE_FOR_MS;
}

/** 返回提示条；不是安卓或用户最近关掉过时返回 null。 */
export function createAppPromo({ ua, now } = {}) {
  if (!isAndroid(ua) || hiddenRecently(now)) return null;
  const el = h("aside", { class: "app-promo", "aria-labelledby": "app-promo-title" },
    h("span", { class: "app-promo-icon", "aria-hidden": "true" }, icon("sparkles")),
    h("div", { class: "app-promo-body" },
      h("p", { class: "app-promo-title", id: "app-promo-title" }, "安卓手机可直接安装 App"),
      h("p", { class: "app-promo-text" },
        "「nufe课表」App 在手机上直接登录学校、整学期课表加密保存在本机，支持桌面小组件和课前提醒；"
        + "无需注册或登录本站即可下载。"),
      h("div", { class: "button-row" },
        h("a", { class: "btn btn-sm btn-primary", href: "android/latest.apk" }, "下载安装包"),
        h("a", { class: "btn btn-sm", href: RELEASE_URL, target: "_blank", rel: "noopener noreferrer" },
          "GitHub 下载"))),
    h("button", {
      type: "button", class: "btn btn-icon btn-sm app-promo-close", "aria-label": "不再提示",
      title: "30 天内不再提示",
      onClick: () => {
        writeStorage(HIDE_KEY, String(Date.now()));
        el.remove();
      },
    }, icon("x")));
  return el;
}
