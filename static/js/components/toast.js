/** 全局提示条。使用 popover 让它出现在原生 dialog 之上；不支持时退回 fixed 定位。 */

import { h, replace } from "../dom.js";
import { icon } from "../icons.js";

const ICONS = { info: "info", success: "check", error: "alert", warn: "alert" };
let node = null;
let timer = 0;

function ensure() {
  if (node) return node;
  node = h("div", {
    class: "toast",
    role: "status",
    "aria-live": "polite",
    popover: "manual",
    hidden: true,
    onClick: () => hide(),
  });
  document.body.append(node);
  return node;
}

function hide() {
  if (!node) return;
  window.clearTimeout(timer);
  node.hidden = true;
  node.classList.remove("is-visible");
  try {
    if (node.matches(":popover-open")) node.hidePopover();
  } catch {
    /* 浏览器不支持 popover 时忽略 */
  }
}

export function showToast(message, { type = "info", timeout } = {}) {
  const el = ensure();
  const kind = ICONS[type] ? type : "info";
  window.clearTimeout(timer);
  el.className = `toast toast-${kind} is-visible`;
  el.setAttribute("role", kind === "error" ? "alert" : "status");
  el.setAttribute("aria-live", kind === "error" ? "assertive" : "polite");
  replace(el, icon(ICONS[kind]), h("span", { class: "toast-text" }, message));
  el.hidden = false;
  try {
    // 如果提示条在弹窗之前就打开了，它会留在弹窗下层；先收回再弹出，重新进入 top layer 顶层
    if (el.matches(":popover-open")) el.hidePopover();
    el.showPopover();
  } catch {
    /* 老浏览器：元素本身已经在 body 末尾，fixed 定位同样可见 */
  }
  timer = window.setTimeout(hide, timeout ?? (kind === "error" ? 5200 : 3200));
  return el;
}

export function hideToast() {
  hide();
}
