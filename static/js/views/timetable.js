/** 课表视图：周视图 / 日视图、当前时间、课程详情、手动刷新、设置入口。 */

import { api, withQuery } from "../api.js";
import { h, replace, debounce, isTypingTarget } from "../dom.js";
import { icon, REPO_URL } from "../icons.js";
import { showToast } from "../components/toast.js";
import { openDialog } from "../components/dialog.js";
import * as T from "../time.js";
import { lessonsOfWeek, maxWeekOf, formatUnits, formatTimeRange, formatUnitsWithTime, initialOf } from "../courses.js";
import { layoutItems } from "../overlap.js";
import { parseHash, buildHash } from "../hash.js";
import { readViewPreference, writeViewPreference, readSemesterPreference, writeSemesterPreference } from "../store.js";
import { getResolvedTheme, toggleTheme } from "../theme.js";
import { openSettings } from "./settings.js";

const STATUS_TEXT = {
  ok: { label: "正常", tone: "ok" },
  error: { label: "刷新失败", tone: "warn" },
  captcha: { label: "等待验证码", tone: "warn" },
  needs_rebind: { label: "需要重新登录", tone: "danger" },
};

function actionButton(label, iconName, onClick, { className = "btn btn-ghost btn-sm" } = {}) {
  return h("button", { type: "button", class: className, onClick },
    icon(iconName),
    h("span", {}, label));
}

function emptyState({ iconName = "calendar", title, text, actionLabel, onAction }) {
  return h("div", { class: "empty-state" },
    h("span", { class: "empty-icon", "aria-hidden": "true" }, icon(iconName)),
    h("h2", {}, title),
    h("p", { class: "muted" }, text),
    actionLabel ? h("button", { type: "button", class: "btn btn-primary", onClick: onAction }, actionLabel) : null);
}

export function mountTimetable(root, { me, onLogout, onLogoutAll, onAccountDeleted, onMeChanged } = {}) {
  return new TimetableController(root, me, { onLogout, onLogoutAll, onAccountDeleted, onMeChanged }).start();
}

class TimetableController {
  constructor(root, me, { onLogout, onLogoutAll, onAccountDeleted, onMeChanged } = {}) {
    this.root = root;
    this.me = me;
    this.onLogout = onLogout;
    this.onLogoutAll = onLogoutAll;
    this.onAccountDeleted = onAccountDeleted;
    this.onMeChanged = onMeChanged;

    const hash = parseHash(window.location.hash);
    this.pendingWeek = hash.week ?? null;
    this.pendingDay = hash.day ?? null;
    const preferredSemester = hash.semesterId || readSemesterPreference();
    const knownSemesters = new Set(
      (Array.isArray(me?.semesters) ? me.semesters : []).map((item) => Number(item?.id)),
    );
    if (me?.current_semester_id) knownSemesters.add(Number(me.current_semester_id));
    this.state = {
      timetable: null,
      semesterId: preferredSemester && knownSemesters.has(Number(preferredSemester))
        ? Number(preferredSemester)
        : me?.current_semester_id || null,
      // 手机上的周视图也会把整周压进一屏宽，默认先看整周
      view: hash.view || readViewPreference() || "week",
      week: hash.week || 1,
      day: hash.day ?? T.dayIndex(new Date()),
      loading: true,
      refreshing: false,
      error: null,
    };
    this.geom = null;
    this.weekRefs = null;
    this.nowSignature = "";
    this.semSignature = "";
    this.timer = 0;
    this.disposers = [];
    this.destroyed = false;
    this.loadToken = 0;
  }

  start() {
    this.buildShell();
    this.bindGlobalEvents();
    this.renderAll();
    this.loadTimetable({ initial: true });
    this.timer = window.setInterval(() => this.tick(), 30_000);
    return this;
  }

  destroy() {
    this.destroyed = true;
    this.loadToken += 1;
    this.appbarObserver?.disconnect();
    window.clearInterval(this.timer);
    for (const dispose of this.disposers) dispose();
    this.disposers = [];
    this.root.replaceChildren();
  }

  // ---------- 骨架 ----------

  buildShell() {
    this.weekTitle = h("strong", { class: "week-title" }, "第 1 周");
    this.weekRange = h("span", { class: "week-range" });
    this.weekLabel = h("button", {
      type: "button",
      class: "week-label",
      title: "回到本周",
      "aria-label": "回到本周",
      onClick: () => this.jumpToday(),
    }, this.weekTitle, this.weekRange);

    this.prevBtn = h("button", {
      type: "button", class: "btn btn-icon btn-ghost", "aria-label": "上一周", title: "上一周（←）",
      onClick: () => this.selectWeek(this.state.week - 1),
    }, icon("chevron-left"));
    this.nextBtn = h("button", {
      type: "button", class: "btn btn-icon btn-ghost", "aria-label": "下一周", title: "下一周（→）",
      onClick: () => this.selectWeek(this.state.week + 1),
    }, icon("chevron-right"));

    this.todayBtn = actionButton("今天", "calendar", () => this.jumpToday());
    this.todayBtn.classList.add("today-btn");
    this.todayBtn.setAttribute("aria-label", "回到本周");
    this.todayBtn.title = "回到本周";

    this.weekTabs = h("div", { class: "seg", role: "tablist", "aria-label": "视图" });
    this.weekTab = h("button", {
      type: "button", role: "tab", "data-view": "week", "aria-controls": "view-host",
      onClick: () => this.setView("week"),
    }, "周");
    this.dayTab = h("button", {
      type: "button", role: "tab", "data-view": "day", "aria-controls": "view-host",
      onClick: () => this.setView("day"),
    }, "日");
    this.weekTabs.append(this.weekTab, this.dayTab);
    this.weekTabs.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      event.preventDefault();
      event.stopPropagation();
      const next = this.state.view === "week" ? "day" : "week";
      this.setView(next);
      (next === "week" ? this.weekTab : this.dayTab).focus();
    });

    this.semSelect = h("select", {
      class: "select", "aria-label": "选择学期",
      onChange: (event) => this.setSemester(Number(event.target.value)),
    });
    this.semWrap = h("div", { class: "sem-wrap" }, this.semSelect);

    this.refreshBtn = h("button", {
      type: "button", class: "btn btn-icon", "aria-label": "立即刷新", title: "立即刷新",
      onClick: () => this.refresh(),
    }, icon("refresh"));

    const toolbar = h("div", { class: "timetable-toolbar" },
      h("div", { class: "week-nav" }, this.prevBtn, this.weekLabel, this.nextBtn),
      h("div", { class: "toolbar-actions" },
        this.todayBtn,
        this.weekTabs,
        this.semWrap,
        this.refreshBtn));

    this.meta = h("p", { class: "timetable-meta" });
    this.banner = this.buildBanner();
    this.srStatus = h("p", { class: "sr-only", role: "status", "aria-live": "polite" });
    this.viewHost = h("div", { class: "view-host", id: "view-host" });

    this.avatar = h("span", { class: "avatar", "aria-hidden": "true" });
    this.userName = h("span", { class: "user-name" });
    this.userId = h("span", { class: "user-id" });
    this.userChip = h("button", {
      type: "button", class: "user-chip", onClick: () => this.openSettings(),
    }, this.avatar, h("span", { class: "user-text" }, this.userName, this.userId));

    this.themeBtn = h("button", {
      type: "button", class: "btn btn-icon btn-ghost theme-btn", onClick: () => {
        toggleTheme();
      },
    });

    const appbar = h("header", { class: "appbar" },
      h("button", { type: "button", class: "brand brand-button", onClick: () => this.jumpToday(), "aria-label": "南财课表，回到本周" },
        h("span", { class: "brand-mark", "aria-hidden": "true" }),
        h("span", { class: "brand-name" }, "南财课表")),
      h("div", { class: "appbar-actions" }, this.themeBtn, this.userChip));

    const footer = h("footer", { class: "app-footer" },
      h("p", {}, "非官方第三方工具 · 数据来自南京财经大学教务系统 · 可在设置中随时删除全部数据"),
      h("p", {}, "开源项目：", h("a", { href: REPO_URL, target: "_blank", rel: "noopener noreferrer" }, "GitHub"), " · GPL-3.0"));

    const shell = h("div", { class: "app-shell" },
      h("div", { class: "appbar-wrap" }, appbar),
      h("main", { class: "app-main" }, this.banner, toolbar, this.meta, this.srStatus, this.viewHost, footer));

    this.root.replaceChildren(shell);
    this.syncAppbarHeight();
    const wrap = this.root.querySelector(".appbar-wrap");
    if (wrap && typeof ResizeObserver === "function") {
      this.appbarObserver = new ResizeObserver(() => this.syncAppbarHeight());
      this.appbarObserver.observe(wrap);
    }
    document.fonts?.ready.then(() => this.syncAppbarHeight()).catch(() => {});
    this.renderThemeButton();
    this.renderUser();
  }

  buildBanner() {
    this.bannerText = h("span", { class: "banner-text" });
    this.bannerAction = h("button", { type: "button", class: "btn btn-sm", onClick: () => this.onBannerAction() });
    return h("div", { class: "status-banner", role: "status", hidden: true },
      h("span", { class: "banner-icon", "aria-hidden": "true" }, icon("alert")),
      this.bannerText,
      this.bannerAction);
  }

  bindGlobalEvents() {
    const onResize = debounce(() => {
      this.syncAppbarHeight();
      if (this.state.view === "week" && this.state.timetable) this.renderView();
    }, 180);
    window.addEventListener("resize", onResize);
    this.disposers.push(() => window.removeEventListener("resize", onResize));

    const onKeyDown = (event) => {
      if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return;
      if (isTypingTarget(event.target) || document.querySelector("dialog[open]")) return;
      if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        event.preventDefault();
        const step = event.key === "ArrowRight" ? 1 : -1;
        if (this.state.view === "day") this.shiftDay(step);
        else this.selectWeek(this.state.week + step);
      } else if (event.key === "t" || event.key === "T") {
        event.preventDefault();
        this.jumpToday();
      } else if (event.key === "w" || event.key === "W") {
        this.setView("week");
      } else if (event.key === "d" || event.key === "D") {
        this.setView("day");
      }
    };
    document.addEventListener("keydown", onKeyDown);
    this.disposers.push(() => document.removeEventListener("keydown", onKeyDown));

    const onVisibility = () => {
      if (document.visibilityState === "visible") this.tick();
    };
    document.addEventListener("visibilitychange", onVisibility);
    this.disposers.push(() => document.removeEventListener("visibilitychange", onVisibility));

    const onTheme = () => this.renderThemeButton();
    window.addEventListener("kb:theme", onTheme);
    this.disposers.push(() => window.removeEventListener("kb:theme", onTheme));

    const onHash = () => this.applyHash(parseHash(window.location.hash));
    window.addEventListener("hashchange", onHash);
    this.disposers.push(() => window.removeEventListener("hashchange", onHash));
  }

  // ---------- 数据 ----------

  async loadTimetable({ initial = false, jump = false } = {}) {
    const token = ++this.loadToken;
    this.state.loading = true;
    this.state.error = null;
    this.renderView();
    this.renderToolbar();
    try {
      const data = await api.get(withQuery("api/timetable", { semester_id: this.state.semesterId }));
      if (this.destroyed || token !== this.loadToken) return;  // 快速切换学期时丢弃过期响应
      this.state.timetable = data;
      this.state.semesterId = data.semester_id;
      writeSemesterPreference(data.semester_id);
      this.applyWeekFromData(data, { initial: initial || jump });
      this.state.loading = false;
      this.state.error = null;
    } catch (error) {
      if (this.destroyed || token !== this.loadToken) return;
      this.state.loading = false;
      if (error.status === 404) {
        this.state.timetable = null;
      } else if (error.status !== 401) {
        this.state.error = error.message;
      }
    }
    this.renderAll();
  }

  applyWeekFromData(data, { initial = false } = {}) {
    const maxWeek = maxWeekOf(data);
    const isCurrent = data.semester_id === this.me?.current_semester_id;
    const currentWeek = isCurrent && data.week1_monday
      ? T.weekOf(new Date(), data.week1_monday)
      : null;
    if (this.pendingWeek) {
      this.state.week = T.clamp(this.pendingWeek, 1, maxWeek);
      this.pendingWeek = null;
    } else if (initial) {
      this.state.week = currentWeek ? T.clamp(currentWeek, 1, maxWeek) : 1;
    } else {
      this.state.week = T.clamp(this.state.week, 1, maxWeek);
    }
    if (this.pendingDay !== null && this.pendingDay !== undefined) {
      this.state.day = this.pendingDay;
      this.pendingDay = null;
    } else if (initial && isCurrent && this.state.view === "day") {
      this.state.day = T.dayIndex(new Date());
    }
  }

  currentWeek() {
    const tt = this.state.timetable;
    if (!tt || !tt.week1_monday || tt.semester_id !== this.me?.current_semester_id) return null;
    return T.weekOf(new Date(), tt.week1_monday);
  }

  maxWeek() {
    return maxWeekOf(this.state.timetable);
  }

  selectWeek(week) {
    if (this.destroyed) return;
    const next = T.clamp(week, 1, this.maxWeek());
    if (next === this.state.week) return;
    this.state.week = next;
    this.renderToolbar();
    this.renderView();
    this.renderMeta();
    this.syncHash();
    this.updateTitle();
  }

  setView(view) {
    if (view !== "week" && view !== "day") return;
    if (this.state.view === view) return;
    this.state.view = view;
    writeViewPreference(view);
    if (view === "day" && this.currentWeek() === this.state.week) {
      this.state.day = T.dayIndex(new Date());
    }
    this.renderToolbar();
    this.renderView();
    this.syncHash();
  }

  setDay(day, { focusTab = false } = {}) {
    const next = T.clamp(day, 0, 6);
    if (next !== this.state.day) {
      this.state.day = next;
      // “今天”按钮是否可用取决于选中的是不是今天，必须跟着刷新工具栏
      this.renderToolbar();
      this.renderView();
      this.syncHash();
    }
    if (focusTab) {
      this.viewHost.querySelector('.day-tab[aria-selected="true"]')?.focus();
    }
  }

  shiftDay(step) {
    let day = this.state.day + step;
    let week = this.state.week;
    if (day > 6) {
      day = 0;
      week += 1;
    } else if (day < 0) {
      day = 6;
      week -= 1;
    }
    const maxWeek = this.maxWeek();
    if (week < 1 || week > maxWeek) return;
    const weekChanged = week !== this.state.week;
    this.state.week = week;
    this.state.day = day;
    this.renderToolbar();
    if (weekChanged) this.renderMeta();
    this.renderView();
    this.syncHash();
    this.updateTitle();
  }

  setSemester(semesterId, { jump = true } = {}) {
    if (!Number.isInteger(semesterId) || semesterId === this.state.semesterId) return;
    this.state.semesterId = semesterId;
    this.state.week = 1;
    this.state.day = T.dayIndex(new Date());
    writeSemesterPreference(semesterId);
    this.renderToolbar();
    this.loadTimetable({ jump });
  }

  jumpToday() {
    if (!this.state.timetable) {
      if (this.me?.current_semester_id && this.state.semesterId !== this.me.current_semester_id) {
        this.setSemester(this.me.current_semester_id);
      }
      return;
    }
    if (this.state.timetable.semester_id !== this.me?.current_semester_id) {
      this.setSemester(this.me.current_semester_id);
      return;
    }
    const currentWeek = this.currentWeek();
    if (!currentWeek || !this.state.timetable.week1_monday) {
      showToast("这个学期还没有配置校历，无法定位到本周", { type: "warn" });
      return;
    }
    this.state.week = T.clamp(currentWeek, 1, this.maxWeek());
    if (this.state.view === "day") this.state.day = T.dayIndex(new Date());
    this.renderToolbar();
    this.renderView();
    this.renderMeta();
    this.syncHash();
    this.updateTitle();
  }

  async refresh() {
    if (this.destroyed || this.state.refreshing || this.state.loading) return;
    this.state.refreshing = true;
    this.renderToolbar();
    try {
      const result = await api.post("api/refresh", { semester_id: this.state.semesterId });
      if (this.destroyed) return;
      if (result.account) {
        this.me = { ...this.me, ...result.account };
        this.renderUser();
        this.renderBanner();
        this.onMeChanged?.(this.me);
      }
      if (result.timetable) {
        this.state.timetable = result.timetable;
        this.state.semesterId = result.timetable.semester_id;
        this.state.error = null;
        this.applyWeekFromData(result.timetable);
      }
      this.renderAll();
      if (result.ok === false) {
        showToast(result.message || "刷新失败，请稍后再试", { type: "error" });
      } else if (result.cached) {
        showToast(result.message || "课表已是最新", { type: "info" });
      } else {
        showToast("课表已更新", { type: "success" });
      }
    } catch (error) {
      if (this.destroyed || error.status === 401) return;
      if (error.status === 409) showToast("正在刷新中，请稍候", { type: "info" });
      else showToast(error.message, { type: "error" });
    } finally {
      this.state.refreshing = false;
      if (!this.destroyed) this.renderToolbar();
    }
  }

  // ---------- 渲染 ----------

  renderAll() {
    if (this.destroyed) return;
    this.renderUser();
    this.renderToolbar();
    this.renderBanner();
    this.renderMeta();
    this.renderView();
    this.syncHash();
    this.updateTitle();
  }

  syncAppbarHeight() {
    const wrap = this.root.querySelector(".appbar-wrap");
    if (!wrap) return;
    const height = Math.ceil(wrap.getBoundingClientRect().height);
    if (height > 0) document.documentElement.style.setProperty("--appbar-h", `${height + 1}px`);
  }

  renderUser() {
    if (!this.me) return;
    const name = this.me.name || "已登录";
    this.userName.textContent = name;
    this.userId.textContent = this.me.student_id || "";
    this.avatar.textContent = initialOf(this.me.name);
    this.userChip.setAttribute("aria-label", `账号与设置：${name}`);
  }

  renderThemeButton() {
    const dark = getResolvedTheme() === "dark";
    replace(this.themeBtn, icon(dark ? "sun" : "moon"));
    this.themeBtn.setAttribute("aria-label", dark ? "切换到浅色主题" : "切换到深色主题");
    this.themeBtn.title = dark ? "切换到浅色主题" : "切换到深色主题";
  }

  semesterOptions() {
    const list = Array.isArray(this.me?.semesters) ? [...this.me.semesters] : [];
    const tt = this.state.timetable;
    if (tt && !list.some((item) => item.id === tt.semester_id)) {
      list.push({ id: tt.semester_id, name: tt.semester_name || String(tt.semester_id) });
    }
    if (!list.length && this.me?.current_semester_id) {
      list.push({ id: this.me.current_semester_id, name: "本学期" });
    }
    return list
      .filter((item) => item && Number.isFinite(Number(item.id)))
      .map((item) => ({ id: Number(item.id), name: String(item.name || item.id) }))
      .sort((a, b) => b.id - a.id);
  }

  renderToolbar() {
    const tt = this.state.timetable;
    const currentWeek = this.currentWeek();
    const maxWeek = this.maxWeek();
    this.weekTitle.textContent = `第 ${this.state.week} 周`;
    const monday = tt?.week1_monday ? T.weekStart(tt.week1_monday, this.state.week) : null;
    this.weekRange.textContent = T.formatWeekRange(monday);
    this.weekLabel.classList.toggle("is-current", currentWeek === this.state.week);
    this.weekLabel.setAttribute("aria-label", currentWeek === this.state.week
      ? `第 ${this.state.week} 周，本周，回到本周`
      : `第 ${this.state.week} 周，回到本周`);

    this.prevBtn.disabled = this.state.loading || this.state.week <= 1;
    this.nextBtn.disabled = this.state.loading || this.state.week >= maxWeek;
    this.weekTab.setAttribute("aria-selected", String(this.state.view === "week"));
    this.dayTab.setAttribute("aria-selected", String(this.state.view === "day"));
    this.weekTab.tabIndex = this.state.view === "week" ? 0 : -1;
    this.dayTab.tabIndex = this.state.view === "day" ? 0 : -1;
    if (!this.state.loading) {
      this.srStatus.textContent = `第 ${this.state.week} 周`
        + `${this.weekRange.textContent ? `，${this.weekRange.textContent}` : ""}`
        + `${currentWeek === this.state.week ? "，本周" : ""}`;
    }

    const atCurrent = currentWeek === this.state.week
      && (this.state.view !== "day" || this.state.day === T.dayIndex(new Date()));
    this.todayBtn.disabled = this.state.loading || !tt || atCurrent;

    this.refreshBtn.disabled = this.state.refreshing || this.state.loading;
    this.refreshBtn.classList.toggle("is-spinning", this.state.refreshing);
    this.refreshBtn.setAttribute("aria-busy", String(this.state.refreshing));

    const options = this.semesterOptions();
    const signature = options.map((item) => `${item.id}:${item.name}`).join("|");
    if (signature !== this.semSignature) {
      this.semSignature = signature;
      this.semSelect.replaceChildren(...options.map((item) => h("option", {
        value: String(item.id),
        text: item.name + (item.id === this.me?.current_semester_id ? "（当前）" : ""),
      })));
    }
    this.semWrap.hidden = options.length === 0;
    this.semSelect.value = String(this.state.semesterId ?? "");
    this.semSelect.disabled = this.state.loading || this.state.refreshing;
  }

  renderBanner() {
    const status = this.me?.status || "ok";
    if (status === "ok") {
      this.banner.hidden = true;
      return;
    }
    const meta = STATUS_TEXT[status] || { label: "状态异常", tone: "warn" };
    const messages = {
      needs_rebind: this.me?.last_error || "登录态已过期，请重新登录本站。",
      captcha: this.me?.last_error || "统一身份认证要求验证码，自动刷新已暂停。",
      error: this.me?.last_error || "上次刷新失败，稍后会自动重试。",
    };
    this.banner.hidden = false;
    this.banner.className = `status-banner status-${meta.tone}`;
    this.bannerText.textContent = `${meta.label}：${messages[status] || status}`;
    const action = status === "error" ? "retry" : "reauth";
    this.bannerAction.hidden = false;
    this.bannerAction.textContent = action === "retry" ? "重试刷新" : "重新登录";
    this.bannerAction.dataset.action = action;
    replace(this.banner.querySelector(".banner-icon"), icon(status === "error" ? "alert" : "key"));
  }

  async onBannerAction() {
    if (this.bannerAction.dataset.action === "retry") {
      await this.refresh();
      return;
    }
    this.onLogout?.();
  }

  renderMeta() {
    const tt = this.state.timetable;
    const parts = [];
    if (tt) {
      parts.push(tt.semester_name || "未知学期");
      parts.push(`${T.relativeTime(tt.fetched_at)}更新`);
    }
    if (this.me?.next_refresh_at) {
      parts.push(`下次自动刷新 ${T.formatDateTime(this.me.next_refresh_at)}`);
    } else if (tt) {
      parts.push("自动刷新已暂停");
    }
    if (tt && !tt.week1_monday) parts.push("该学期未配置校历，日期和 ICS 可能不完整");
    this.meta.textContent = parts.join(" · ");
  }

  updateTitle() {
    const tt = this.state.timetable;
    const week = `第 ${this.state.week} 周`;
    document.title = tt ? `${week} · 南财课表` : "南财课表";
  }

  syncHash() {
    const hash = buildHash({
      semesterId: this.state.semesterId,
      week: this.state.week,
      day: this.state.day,
      view: this.state.view,
    });
    const url = `${window.location.pathname}${window.location.search}${hash}`;
    if (`${window.location.pathname}${window.location.search}${window.location.hash}` !== url) {
      window.history.replaceState(null, "", url);
    }
  }

  applyHash(hash) {
    let changed = false;
    if (hash.view && hash.view !== this.state.view) {
      this.state.view = hash.view;
      writeViewPreference(hash.view);
      changed = true;
    }
    if (hash.week) {
      const week = T.clamp(hash.week, 1, this.maxWeek());
      if (week !== this.state.week) {
        this.state.week = week;
        changed = true;
      }
    }
    if (Number.isInteger(hash.day) && hash.day !== this.state.day) {
      this.state.day = hash.day;
      changed = true;
    }
    if (hash.semesterId && hash.semesterId !== this.state.semesterId) {
      this.state.semesterId = hash.semesterId;
      this.pendingWeek = hash.week ?? null;
      this.pendingDay = hash.day ?? null;
      this.loadTimetable({ jump: true });
      return;
    }
    if (changed) {
      this.renderToolbar();
      this.renderView();
      this.renderMeta();
    }
  }

  // ---------- 视图 ----------

  renderView() {
    this.dayKey = new Date().toDateString();
    this.viewHost.setAttribute("aria-busy", String(this.state.loading));
    if (this.state.loading) {
      this.srStatus.textContent = "正在加载课表";
      replace(this.viewHost, this.skeleton());
      return;
    }
    if (this.state.error) {
      this.srStatus.textContent = "课表加载失败";
      replace(this.viewHost, emptyState({
        iconName: "alert",
        title: "课表加载失败",
        text: this.state.error,
        actionLabel: "重试",
        onAction: () => this.loadTimetable(),
      }));
      return;
    }
    if (!this.state.timetable) {
      this.srStatus.textContent = "这个学期还没有课表";
      replace(this.viewHost, emptyState({
        iconName: "calendar",
        title: "这个学期还没有课表",
        text: "点击刷新，从教务系统获取这个学期的课程。",
        actionLabel: "立即刷新",
        onAction: () => this.refresh(),
      }));
      return;
    }
    if (this.state.view === "day") this.renderDay();
    else this.renderWeek();
  }

  skeleton() {
    const rows = Array.from({ length: 6 }, () => h("div", { class: "sk-line" }));
    return h("div", { class: "skeleton", "aria-hidden": "true" },
      h("span", { class: "sr-only" }, "正在加载课表…"),
      h("div", { class: "sk-header" }, h("div", { class: "sk-pill" }), h("div", { class: "sk-pill short" })),
      h("div", { class: "sk-card" }, rows));
  }

  pixelPerMinute() {
    const width = window.innerWidth;
    // 手机上列很窄，课程名要折成多行，纵向反而要留得比桌面略高
    if (width <= 760) return 0.92;
    if (width <= 1080) return 1.0;
    return 1.12;
  }

  weekMonday(week = this.state.week) {
    const week1 = this.state.timetable?.week1_monday;
    return week1 ? T.weekStart(week1, week) : null;
  }

  renderWeek() {
    this.dayKey = new Date().toDateString();
    const tt = this.state.timetable;
    const week = this.state.week;
    const monday = this.weekMonday(week);
    const items = lessonsOfWeek(tt, week);
    const today = new Date();
    const isCurrentWeek = this.currentWeek() === week;
    // 周末有课才占列；周末没课时把宽度留给工作日
    const days = items.some((item) => item.day >= 5) ? 7 : 5;

    if (items.length === 0) {
      this.geom = null;
      this.nowTag = null;
      this.nowLine = null;
      this.nowSignature = "";
      const empty = [];
      if (!tt.week1_monday) {
        empty.push(emptyState({
          iconName: "calendar",
          title: "还没有这一周的日期",
          text: "教务系统没有返回本学期第 1 周的日期。可以在 semesters.json 里补充后刷新；课表本身仍然可用。",
          actionLabel: "立即刷新",
          onAction: () => this.refresh(),
        }));
      }
      empty.push(emptyState({
        iconName: "sparkles",
        title: `第 ${week} 周没有课`,
        text: "好好休息，或者去看看其他周。",
        actionLabel: this.currentWeek() === week ? null : "回到本周",
        onAction: () => this.jumpToday(),
      }));
      replace(this.viewHost, h("div", { class: "empty-stack" }, empty));
      return;
    }

    let lo = 8 * 60;
    let hi = 18 * 60;
    for (const item of items) {
      lo = Math.min(lo, item.start);
      hi = Math.max(hi, item.end);
    }
    // 上下留一点余量，避免整点标签被卡片边缘/吸顶表头裁掉
    lo = Math.max(0, Math.floor(lo / 60) * 60 - 20);
    hi = Math.min(23 * 60 + 59, Math.ceil(hi / 60) * 60 + 10);
    if (hi - lo < 6 * 60) {
      const mid = (lo + hi) / 2;
      lo = Math.max(0, Math.floor((mid - 180) / 60) * 60);
      hi = lo + 6 * 60;
    }
    const ppm = this.pixelPerMinute();
    const height = (hi - lo) * ppm;
    const now = T.nowMinutes();
    const todayColumn = monday ? T.daysBetween(monday, today) : -1;
    const showNow = todayColumn >= 0 && todayColumn < days && isCurrentWeek && now >= lo && now <= hi;

    this.dayKey = new Date().toDateString();
    const grid = h("div", { class: "week-grid" });
    grid.style.setProperty("--days", String(days));
    grid.append(h("div", { class: "week-corner", "aria-hidden": "true" }));

    for (let day = 0; day < days; day += 1) {
      const date = monday ? T.addDays(monday, day) : null;
      const isToday = Boolean(date && T.sameDay(date, today));
      grid.append(h("div", {
        class: `week-day${isToday ? " is-today" : ""}`,
        attrs: isToday ? { "aria-current": "date" } : {},
      },
      h("span", { class: "week-day-name" }, T.WEEKDAY_LONG[day]),
      date ? h("span", { class: "week-day-date" }, String(date.getDate())) : null));
    }

    const axis = h("div", { class: "week-axis", style: { height: `${height}px` } });
    for (let minute = Math.ceil(lo / 60) * 60; minute <= hi; minute += 60) {
      if (showNow && Math.abs(minute - now) < 14) continue;
      const offset = (minute - lo) * ppm;
      if (offset < 12 || height - offset < 10) continue;
      axis.append(h("span", {
        class: "week-tick",
        style: { top: `${offset}px` },
        "aria-hidden": "true",
      }, T.formatMinutes(minute)));
    }
    this.nowTag = null;
    this.nowLine = null;
    if (showNow) {
      this.nowTag = h("span", {
        class: "week-now-tag",
        style: { top: `${(now - lo) * ppm}px` },
        "aria-hidden": "true",
      }, T.formatMinutes(now));
      axis.append(this.nowTag);
    }
    grid.append(axis);

    const byDay = Array.from({ length: days }, () => []);
    for (const item of items) if (item.day < days) byDay[item.day].push(item);

    for (let day = 0; day < days; day += 1) {
      const isToday = day === todayColumn && isCurrentWeek;
      const column = h("div", {
        class: `week-col${isToday ? " is-today" : ""}`,
        style: { height: `${height}px` },
      });
      for (let minute = Math.ceil(lo / 30) * 30; minute < hi; minute += 30) {
        column.append(h("span", {
          class: `week-line${minute % 60 === 0 ? " is-hour" : ""}`,
          style: { top: `${(minute - lo) * ppm}px` },
          "aria-hidden": "true",
        }));
      }
      if (isToday && showNow) {
        this.nowLine = h("span", {
          class: "week-now-line",
          style: { top: `${(now - lo) * ppm}px` },
          "aria-hidden": "true",
        });
        column.append(this.nowLine);
      }
      for (const item of layoutItems(byDay[day])) {
        const live = isToday && item.start <= now && now < item.end;
        column.append(this.lessonBlock(item, { ppm, lo, monday, live }));
      }
      grid.append(column);
    }

    this.geom = { lo, hi, ppm, height, monday, days, todayColumn, showNow };
    this.nowSignature = this.signatureOf(items, isCurrentWeek);

    replace(this.viewHost, h("section", {
      class: "week-card",
      "aria-label": `第 ${week} 周课程表`,
    }, grid));
  }

  lessonBlock(item, { ppm, lo, monday, live }) {
    const activity = item.activity;
    const top = (item.start - lo) * ppm + 2;
    const height = Math.max((item.end - item.start) * ppm - 4, 26);
    const compact = height < 58;
    const width = 100 / item.lanes;
    const room = activity.room || activity.building || "";
    const teachers = Array.isArray(activity.teachers) ? activity.teachers.join("、") : "";
    const units = formatUnits(activity);

    const block = h("button", {
      type: "button",
      class: `lesson-block${compact ? " is-compact" : ""}${live ? " is-live" : ""}`,
      style: {
        "--c": item.color,
        top: `${top}px`,
        height: `${height}px`,
        left: `calc(${item.lane * width}% + var(--lesson-inset))`,
        width: `calc(${width}% - 2 * var(--lesson-inset))`,
      },
      dataset: { start: String(item.start), end: String(item.end), day: String(item.day) },
      "aria-label": `${item.courseName}，${T.WEEKDAY_LONG[item.day]} ${units ? `${units}，` : ""}${T.formatMinutes(item.start)} 到 ${T.formatMinutes(item.end)}，${room}，${teachers}`,
      onClick: () => this.openLessonDetail(item, monday),
    },
    live ? h("span", { class: "lesson-badge" }, "上课中") : null,
    h("span", { class: "lesson-name" }, item.courseName),
    h("span", { class: "lesson-time" },
      units ? h("span", { class: "lesson-units" }, units) : null,
      h("span", { class: "lesson-clock" }, formatTimeRange(item))),
    room ? h("span", { class: "lesson-room" }, room) : null,
    teachers ? h("span", { class: "lesson-teacher" }, teachers) : null);
    return block;
  }

  renderDay() {
    const tt = this.state.timetable;
    const week = this.state.week;
    const monday = this.weekMonday(week);
    const items = lessonsOfWeek(tt, week);
    const today = new Date();
    const selectedDay = this.state.day;
    const date = monday ? T.addDays(monday, selectedDay) : null;
    const isToday = Boolean(date && T.sameDay(date, today));
    const now = T.nowMinutes();
    const todayItems = items.filter((item) => item.day === selectedDay);
    const nextIndex = isToday ? todayItems.findIndex((item) => item.start > now) : -1;
    const liveSignature = this.signatureOf(items, this.currentWeek() === week);

    this.dayKey = new Date().toDateString();
    const strip = h("div", { class: "day-strip", role: "tablist", "aria-label": "选择日期" });
    for (let day = 0; day < 7; day += 1) {
      const dayDate = monday ? T.addDays(monday, day) : null;
      const selected = day === selectedDay;
      const hasClass = items.some((item) => item.day === day);
      strip.append(h("button", {
        type: "button",
        role: "tab",
        dataset: { day: String(day) },
        class: `day-tab${selected ? " is-selected" : ""}${dayDate && T.sameDay(dayDate, today) ? " is-today" : ""}${hasClass ? " has-class" : ""}`,
        "aria-selected": String(selected),
        tabindex: selected ? "0" : "-1",
        "aria-label": `${T.WEEKDAY_LONG[day]}${dayDate ? ` ${T.monthDay(dayDate)}` : ""}${hasClass ? "，有课" : ""}`,
        onClick: () => this.setDay(day, { focusTab: true }),
      },
      h("span", { class: "day-tab-name" }, T.WEEKDAY_SHORT[day]),
      h("span", { class: "day-tab-date" }, dayDate ? String(dayDate.getDate()) : "·"),
      hasClass ? h("span", { class: "day-tab-dot", "aria-hidden": "true" }) : null));
    }
    strip.addEventListener("keydown", (event) => {
      const step = event.key === "ArrowLeft" ? -1 : event.key === "ArrowRight" ? 1 : 0;
      if (step === 0 && event.key !== "Home" && event.key !== "End") return;
      event.preventDefault();
      event.stopPropagation();
      const focusedTab = document.activeElement?.closest?.(".day-tab");
      const baseDay = focusedTab ? Number(focusedTab.dataset.day) : selectedDay;
      if (step !== 0) {
        const nextDay = baseDay + step;
        if (nextDay >= 0 && nextDay <= 6) this.setDay(nextDay);
        else if (this.state.week > 1 || this.state.week < this.maxWeek()) {
          this.shiftDay(step);
        } else {
          return;
        }
      } else {
        this.setDay(event.key === "Home" ? 0 : 6);
      }
      this.viewHost.querySelector('.day-tab[aria-selected="true"]')?.focus();
    });

    this.srStatus.textContent = `${T.WEEKDAY_LONG[selectedDay]}`
      + `${date ? `，${T.monthDay(date)}` : ""}，${todayItems.length} 节课`;

    const heading = h("div", { class: "day-heading" },
      h("h2", {}, `${T.WEEKDAY_LONG[selectedDay]}${date ? ` · ${T.monthDay(date)}` : ""}`),
      h("span", { class: "muted" }, todayItems.length ? `${todayItems.length} 节课` : "没有课"));

    let list;
    if (todayItems.length === 0) {
      list = emptyState({
        iconName: "sparkles",
        title: `${T.WEEKDAY_LONG[selectedDay]}没有课`,
        text: isToday ? "今天没有安排，去做点自己喜欢的事吧。" : "这天没有安排课程。",
      });
    } else {
      list = h("ol", { class: "lesson-list" });
      for (let index = 0; index < todayItems.length; index += 1) {
        const item = todayItems[index];
        const live = isToday && item.start <= now && now < item.end;
        const past = isToday && item.end <= now;
        list.append(h("li", {},
          this.dayLessonCard(item, {
            live,
            past,
            next: index === nextIndex && !live,
            now,
            isToday,
          })));
      }
    }

    this.nowSignature = liveSignature;

    const section = h("section", { class: "day-view", "aria-label": `第 ${week} 周课程` },
      strip, heading, list);
    this.bindDaySwipe(section);
    replace(this.viewHost, section);
  }

  bindDaySwipe(node) {
    let startX = null;
    let startY = null;
    node.addEventListener("touchstart", (event) => {
      if (event.touches.length !== 1) return;
      startX = event.touches[0].clientX;
      startY = event.touches[0].clientY;
    }, { passive: true });
    node.addEventListener("touchend", (event) => {
      if (startX === null) return;
      const dx = event.changedTouches[0].clientX - startX;
      const dy = event.changedTouches[0].clientY - startY;
      startX = null;
      startY = null;
      if (Math.abs(dx) < 56 || Math.abs(dx) < Math.abs(dy) * 1.4) return;
      this.shiftDay(dx < 0 ? 1 : -1);
    }, { passive: true });
  }

  dayLessonCard(item, { live, past, next, now, isToday }) {
    const activity = item.activity;
    const room = [activity.campus, activity.room || activity.building].filter(Boolean).join(" · ");
    const teachers = Array.isArray(activity.teachers) ? activity.teachers.join("、") : "";
    const progress = live && isToday
      ? Math.round(((now - item.start) / Math.max(1, item.end - item.start)) * 100)
      : 0;

    return h("button", {
      type: "button",
      class: `lesson-card${live ? " is-live" : ""}${past ? " is-past" : ""}`,
      style: { "--c": item.color },
      dataset: { start: String(item.start), end: String(item.end) },
      onClick: () => this.openLessonDetail(item, null),
      "aria-label": `${item.courseName}，${T.formatMinutes(item.start)} 到 ${T.formatMinutes(item.end)}，${room}，${teachers}`,
    },
    h("span", { class: "lesson-card-time" },
      h("strong", {}, T.formatMinutes(item.start)),
      h("span", {}, T.formatMinutes(item.end))),
    h("span", { class: "lesson-card-body" },
      h("span", { class: "lesson-card-title" },
        h("span", { class: "lesson-card-name" }, item.courseName),
        live ? h("span", { class: "tag tag-live" }, "上课中") : null,
        next ? h("span", { class: "tag tag-next" }, "下一节") : null),
      h("span", { class: "lesson-card-meta" },
        room ? h("span", {}, icon("pin"), room) : null,
        teachers ? h("span", {}, icon("user"), teachers) : null,
        h("span", {}, icon("clock"), formatUnitsWithTime(item))),
      live ? h("span", { class: "lesson-progress", "aria-hidden": "true" },
        h("i", {
          dataset: { start: String(item.start), end: String(item.end) },
          style: { width: `${T.clamp(progress, 0, 100)}%` },
        })) : null));
  }

  openLessonDetail(item, monday) {
    const activity = item.activity;
    const week = this.state.week;
    const date = (monday || this.weekMonday(week))
      ? T.addDays(monday || this.weekMonday(week), item.day)
      : null;
    const room = [activity.campus, activity.building, activity.room].filter(Boolean).join(" · ");
    const teachers = Array.isArray(activity.teachers) ? activity.teachers.join("、") : "";
    const weeks = activity.weeksStr
      ? `${activity.weeksStr} 周`
      : (Array.isArray(activity.weekIndexes) && activity.weekIndexes.length
        ? `第 ${activity.weekIndexes.join("、")} 周`
        : "—");

    const rows = [
      ["时间", `${T.WEEKDAY_LONG[item.day]}${date ? ` ${T.monthDay(date)}` : ""} · ${formatUnitsWithTime(item)}`],
      ["周次", weeks],
      ["地点", room || "—"],
      ["教师", teachers || "—"],
      ["学分", activity.credits ?? "—"],
    ];

    const content = h("div", { class: "lesson-detail-body" },
      h("div", { class: "lesson-detail-head" },
        h("span", { class: "lesson-detail-swatch", style: { background: item.color } }),
        h("div", {},
          h("h2", {}, item.courseName),
          h("p", { class: "muted" }, `第 ${week} 周${date ? ` · ${T.monthDay(date)}` : ""}`)),
        h("button", {
          type: "button", class: "btn btn-icon btn-ghost", "aria-label": "关闭", autofocus: true,
          onClick: () => dialog.close("close"),
        }, icon("x"))),
      h("dl", { class: "kv" },
        ...rows.flatMap(([label, value]) => [h("dt", {}, label), h("dd", {}, String(value))])));

    const dialog = openDialog({
      className: "modal lesson-detail",
      label: item.courseName,
      content,
    });
  }

  signatureOf(items, isCurrentWeek) {
    if (!isCurrentWeek) return "";
    const now = T.nowMinutes();
    const todayIndex = T.dayIndex(new Date());
    const live = items
      .filter((item) => item.day === todayIndex && item.start <= now && now < item.end)
      .map((item) => `${item.start}:${item.end}:${item.courseName}`)
      .join("|");
    const next = items
      .filter((item) => item.day === todayIndex && item.start > now)
      .sort((a, b) => a.start - b.start)[0];
    return `${live}|next:${next ? `${next.start}:${next.courseName}` : ""}`;
  }

  // ---------- 时间推进 ----------

  tick() {
    if (this.destroyed) return;
    this.renderMeta();
    if (this.state.loading || !this.state.timetable) return;
    if (this.dayKey && this.dayKey !== new Date().toDateString()) {
      this.renderView();
      return;
    }
    const isCurrentWeek = this.currentWeek() === this.state.week;
    const items = lessonsOfWeek(this.state.timetable, this.state.week);
    const signature = this.signatureOf(items, isCurrentWeek);
    const focusInside = this.viewHost.contains(document.activeElement);

    if (this.state.view === "week" && this.geom) {
      const now = T.nowMinutes();
      const { lo, hi, ppm } = this.geom;
      const inRange = now >= lo && now <= hi;
      if ((inRange && !this.nowTag) || (!inRange && (this.nowTag || this.nowLine))) {
        this.renderView();
        return;
      }
      if (this.nowTag) {
        this.nowTag.textContent = T.formatMinutes(now);
        this.nowTag.style.top = `${(now - lo) * ppm}px`;
      }
      if (this.nowLine) this.nowLine.style.top = `${(now - lo) * ppm}px`;
      if (signature !== this.nowSignature && !focusInside) {
        this.renderView();
      }
    } else if (this.state.view === "day") {
      const now = T.nowMinutes();
      for (const bar of this.viewHost.querySelectorAll(".lesson-progress i")) {
        const start = Number(bar.dataset.start);
        const end = Number(bar.dataset.end);
        if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) continue;
        bar.style.width = `${T.clamp(((now - start) / (end - start)) * 100, 0, 100)}%`;
      }
      if (signature !== this.nowSignature && !focusInside) this.renderView();
    }
  }

  // ---------- 设置 ----------

  openSettings() {
    if (!this.me) return;
    openSettings({
      me: this.me,
      onUpdated: (me) => {
        this.me = me;
        this.renderUser();
        this.renderBanner();
        this.renderMeta();
        this.onMeChanged?.(me);
      },
      onThemeChange: () => this.renderThemeButton(),
      onLogout: () => this.onLogout?.(),
      onLogoutAll: () => this.onLogoutAll?.(),
      onAccountDeleted: () => this.onAccountDeleted?.(),
    });
  }
}
