/** 应用入口：会话恢复、登录/课表切换、退出登录。 */

import { api } from "./api.js";
import { initTheme } from "./theme.js";
import { h } from "./dom.js";
import { icon, REPO_URL } from "./icons.js";
import { mountLogin } from "./views/login.js";
import { mountTimetable } from "./views/timetable.js";
import { showToast } from "./components/toast.js";
import { closeOpenDialogs } from "./components/dialog.js";

const root = document.getElementById("app");
let current = null; // { type: "login" | "app", instance }

initTheme();

api.setUnauthorizedHandler(() => {
  if (current?.type === "app") showLogin("登录已过期，请重新登录");
});

async function boot() {
  try {
    const data = await api.get("api/me");
    showApp(data);
  } catch (error) {
    if (error.status === 401) {
      showLogin();
      return;
    }
    showFatal(error);
  }
}

function showFatal(error) {
  teardown();
  api.setCsrfToken(null);
  root.replaceChildren(h("main", { class: "auth-page" },
    h("section", { class: "auth-card fatal-card" },
      h("div", { class: "brand" },
        h("span", { class: "brand-mark", "aria-hidden": "true" }),
        h("span", { class: "brand-name" }, "南财课表")),
      h("span", { class: "empty-icon", "aria-hidden": "true" }, icon("alert")),
      h("h1", {}, "暂时无法连接"),
      h("p", { class: "muted" }, error?.message || "服务器没有响应，请稍后再试。"),
      h("button", { type: "button", class: "btn btn-primary btn-block", onClick: () => boot() }, "重试")),
    h("footer", { class: "auth-footer" },
      h("p", {}, "开源项目：", h("a", { href: REPO_URL, target: "_blank", rel: "noopener noreferrer" }, "GitHub"), " · GPL-3.0"))));
  document.title = "南财课表";
}

function teardown() {
  closeOpenDialogs();
  if (current?.instance?.destroy) current.instance.destroy();
  current = null;
}

function showLogin(message) {
  teardown();
  api.setCsrfToken(null);
  const instance = mountLogin(root, {
    onSuccess: (csrf) => {
      api.setCsrfToken(csrf);
      showToast("登录成功", { type: "success" });
      boot();
    },
  });
  current = { type: "login", instance };
  document.title = "登录 · 南财课表";
  if (message) showToast(message, { type: "info" });
}

function showApp(data) {
  teardown();
  api.setCsrfToken(data.csrf);
  const instance = mountTimetable(root, {
    me: data,
    onLogout: () => signOut("api/logout"),
    onLogoutAll: () => signOut("api/logout-all"),
    onAccountDeleted: () => showLogin("已删除你的账号与全部数据。"),
  });
  current = { type: "app", instance };
}

async function signOut(path) {
  try {
    await api.post(path);
  } catch (error) {
    if (error.status !== 401) {
      showToast(error.message, { type: "error" });
      return;
    }
  }
  showLogin();
}

window.addEventListener("unhandledrejection", (event) => {
  const reason = event.reason;
  if (reason?.name === "ApiError") return;
  console.error("unhandled rejection", reason);
  showToast("操作失败，请重试", { type: "error" });
});

boot();
