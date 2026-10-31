import assert from "node:assert/strict";
import test from "node:test";

import { KNOB_WIDTH, SLIDER_WIDTH, createTrackRecorder, pieceWidth } from "../../static/js/slider.js";

function clock(start = 1000) {
  let t = start;
  return { now: () => t, tick: (ms) => { t += ms; } };
}

test("记录用户的拖动：起点、按间隔与距离采样、松手点", () => {
  const c = clock();
  const r = createTrackRecorder({ now: c.now });
  r.start(100, 50);
  c.tick(30);
  assert.equal(r.move(130, 51), 30);
  c.tick(10); // 不足 20ms：位置照常跟手，但不记录
  assert.equal(r.move(140, 51), 40);
  c.tick(30);
  assert.equal(r.move(131, 51), 31); // 离上一个“已记录”的点不足 2px：不记录
  c.tick(30);
  r.move(180, 53);
  c.tick(200);
  const out = r.end(200, 54);
  assert.equal(out.canvasLength, SLIDER_WIDTH);
  assert.equal(out.moveLength, 100);
  assert.deepEqual(out.tracks, [
    { a: 0, b: 0, c: 0 },
    { a: 30, b: 1, c: 30 },
    { a: 80, b: 3, c: 70 },
    { a: 100, b: 4, c: 200 },
  ]);
});

test("越界不跟手，没移动或往回拖不提交", () => {
  const c = clock();
  const r = createTrackRecorder({ now: c.now });
  r.start(0, 0);
  c.tick(30);
  assert.equal(r.move(-5, 0), null);
  assert.equal(r.move(SLIDER_WIDTH - KNOB_WIDTH + 1, 0), null);
  assert.equal(r.end(0, 0), null);
  r.start(50, 0);
  assert.equal(r.end(40, 0), null);
  assert.equal(r.end(60, 0), null); // 已结束的拖动不能再提交
});

test("拼图块宽度按学校插件换算", () => {
  // 590 宽背景配 93 宽拼图条 → 280 宽画布上约 42px
  assert.ok(Math.abs(pieceWidth(590, 93) - (93 * 280 / 590 - 2)) < 1e-9);
  assert.equal(pieceWidth(0, 93), 0);
});
