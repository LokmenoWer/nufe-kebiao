/**
 * 学校滑块拼图的转发界面：背景图 + 拼图块 + 拖动条。
 * 显示尺寸可以比 280px 窄（小屏手机），指针坐标会先换算回 280 宽坐标系再记录。
 */

import { h } from "../dom.js";
import { icon } from "../icons.js";
import { KNOB_WIDTH, SLIDER_WIDTH, createTrackRecorder, pieceWidth } from "../slider.js";

export function createSliderCaptcha({ onRelease, onRefresh } = {}) {
  const bg = h("img", { class: "slider-bg", alt: "滑块验证码图片", draggable: "false" });
  const piece = h("img", { class: "slider-piece", alt: "", draggable: "false" });
  const refresh = h("button", {
    type: "button", class: "slider-refresh", title: "换一张", "aria-label": "换一张拼图",
    onClick: () => onRefresh?.(),
  }, icon("refresh"));
  const stageEl = h("div", { class: "slider-stage" }, bg, piece, refresh);
  const fill = h("div", { class: "slider-fill" });
  const knob = h("div", {
    class: "slider-knob", role: "slider", tabindex: "0",
    "aria-label": "按住向右拖动，把拼图移到缺口处",
    "aria-valuemin": "0", "aria-valuemax": String(SLIDER_WIDTH - KNOB_WIDTH), "aria-valuenow": "0",
  }, icon("arrow-right"));
  const text = h("span", { class: "slider-text" }, "按住滑块向右拖动，补全拼图");
  const track = h("div", { class: "slider-track" }, fill, text, knob);
  const el = h("div", { class: "slider-captcha" }, stageEl, track);

  const recorder = createTrackRecorder();
  let locked = false;
  let pointerId = null;

  function scale() {
    const width = track.getBoundingClientRect().width;
    return width > 0 ? SLIDER_WIDTH / width : 1;
  }

  function place(dx) {
    const pct = (dx / SLIDER_WIDTH) * 100;
    piece.style.left = `${pct}%`;
    knob.style.left = `${((Math.max(0, dx - 1)) / SLIDER_WIDTH) * 100}%`;
    fill.style.width = `${((dx + 4) / SLIDER_WIDTH) * 100}%`;
    knob.setAttribute("aria-valuenow", String(Math.round(dx)));
  }

  function reset() {
    recorder.cancel();
    pointerId = null;
    locked = false;
    el.classList.remove("is-dragging", "is-busy", "is-failed");
    place(0);
    fill.style.width = "0";
  }

  function load({ background, piece: pieceSrc }) {
    reset();
    piece.style.width = "0";
    bg.onload = piece.onload = () => {
      const w = pieceWidth(bg.naturalWidth, piece.naturalWidth);
      if (w > 0) piece.style.width = `${(w / SLIDER_WIDTH) * 100}%`;
    };
    bg.src = background;
    piece.src = pieceSrc;
  }

  function pointFromEvent(event) {
    const k = scale();
    return [event.clientX * k, event.clientY * k];
  }

  knob.addEventListener("pointerdown", (event) => {
    if (locked || event.button > 0) return;
    event.preventDefault();
    pointerId = event.pointerId;
    knob.setPointerCapture?.(event.pointerId);
    el.classList.remove("is-failed");
    el.classList.add("is-dragging");
    recorder.start(...pointFromEvent(event));
  });

  knob.addEventListener("pointermove", (event) => {
    if (event.pointerId !== pointerId || !recorder.active) return;
    const dx = recorder.move(...pointFromEvent(event));
    if (dx !== null) place(dx);
  });

  const release = (event) => {
    if (event.pointerId !== pointerId || !recorder.active) return;
    pointerId = null;
    el.classList.remove("is-dragging");
    const result = recorder.end(...pointFromEvent(event));
    if (!result) {
      place(0);
      return;
    }
    locked = true;
    el.classList.add("is-busy");
    onRelease?.(result);
  };
  knob.addEventListener("pointerup", release);
  knob.addEventListener("pointercancel", (event) => {
    if (event.pointerId !== pointerId) return;
    recorder.cancel();
    pointerId = null;
    el.classList.remove("is-dragging");
    place(0);
  });

  return {
    el,
    load,
    reset,
    fail() {
      locked = false;
      el.classList.remove("is-busy");
      el.classList.add("is-failed");
    },
  };
}
