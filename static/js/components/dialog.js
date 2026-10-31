/** 原生 dialog 的薄封装：课程详情、设置抽屉、站内确认框。 */

import { h, nextId } from "../dom.js";

/**
 * 打开一个 dialog。content 必须是 DOM 节点。
 * onClose 在关闭动画后调用（dialog 已从文档移除）。
 */
export function openDialog({ className = "modal", label = "", content, onClose }) {
  const dialog = h("dialog", { class: className });
  if (label) dialog.setAttribute("aria-label", label);
  dialog.append(content);
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close("dismiss");
  });
  dialog.addEventListener("close", () => {
    const value = dialog.returnValue;
    dialog.remove();
    onClose?.(value);
  }, { once: true });
  document.body.append(dialog);
  dialog.showModal();
  return dialog;
}

export function closeOpenDialogs() {
  for (const dialog of document.querySelectorAll("dialog[open]")) {
    try {
      dialog.close("dismiss");
    } catch {
      /* 已关闭 */
    }
  }
}

/**
 * 站内确认框（不用原生 confirm：部分内置浏览器 / 微信里会被屏蔽）。
 * 返回 Promise<boolean>；Esc / 点遮罩都视为取消。
 */
export function confirmDialog({
  title = "请确认",
  message = "",
  confirmText = "确定",
  cancelText = "取消",
  danger = false,
} = {}) {
  return new Promise((resolve) => {
    const titleId = nextId("confirm-title");
    let accepted = false;

    const confirmBtn = h("button", {
      type: "button",
      class: `btn ${danger ? "btn-danger" : "btn-primary"}`,
      onClick: () => {
        accepted = true;
        dialog.close("confirm");
      },
    }, confirmText);

    const cancelBtn = h("button", {
      type: "button",
      class: "btn btn-ghost",
      onClick: () => dialog.close("cancel"),
    }, cancelText);

    const content = h("div", { class: "confirm-body" },
      h("h2", { id: titleId, class: "confirm-title" }, title),
      h("p", { class: "confirm-message" }, message),
      h("div", { class: "dialog-actions" }, cancelBtn, confirmBtn));

    const dialog = openDialog({
      className: "modal confirm-dialog",
      content,
      onClose: () => resolve(accepted),
    });
    dialog.setAttribute("aria-labelledby", titleId);
    (danger ? cancelBtn : confirmBtn).focus();
  });
}
