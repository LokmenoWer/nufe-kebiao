import assert from "node:assert/strict";
import test from "node:test";

import { layoutItems } from "../../static/js/overlap.js";

const item = (start, end, name = "") => ({ start, end, name });

test("不重叠的课程各自占满整列", () => {
  const result = layoutItems([item(0, 60, "a"), item(60, 120, "b")]);
  assert.deepEqual(result.map((x) => [x.name, x.lane, x.lanes]), [["a", 0, 1], ["b", 0, 1]]);
});

test("重叠的课程分道", () => {
  const result = layoutItems([item(0, 60, "a"), item(30, 90, "b")]);
  assert.equal(result[0].lanes, 2);
  assert.equal(result[1].lanes, 2);
  assert.deepEqual(result.map((x) => x.lane).sort(), [0, 1]);
});

test("链式重叠使用最少车道", () => {
  const result = layoutItems([item(0, 60, "a"), item(30, 90, "b"), item(70, 120, "c")]);
  assert.equal(result.find((x) => x.name === "a").lane, 0);
  assert.equal(result.find((x) => x.name === "b").lane, 1);
  assert.equal(result.find((x) => x.name === "c").lane, 0);
  assert.ok(result.every((x) => x.lanes === 2));
});

test("不同时间段分开计算车道", () => {
  const result = layoutItems([
    item(0, 60, "a"), item(20, 50, "b"), item(120, 180, "c"), item(150, 190, "d"),
  ]);
  const byName = Object.fromEntries(result.map((x) => [x.name, x]));
  assert.equal(byName.a.lanes, 2);
  assert.equal(byName.b.lanes, 2);
  assert.equal(byName.c.lanes, 2);
  assert.equal(byName.d.lanes, 2);
  assert.equal(byName.c.lane, 0);
});

test("零长度课程至少有 1 分钟高度", () => {
  const [only] = layoutItems([item(500, 500, "x")]);
  assert.equal(only.start, 500);
  assert.equal(only.end, 501);
});

test("输入为空时返回空数组", () => {
  assert.deepEqual(layoutItems([]), []);
  assert.deepEqual(layoutItems(null), []);
});
