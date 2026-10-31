import assert from "node:assert/strict";
import test from "node:test";

import { buildHash, parseHash } from "../../static/js/hash.js";

test("解析完整的 hash", () => {
  assert.deepEqual(parseHash("#sem=161&w=8&view=day&d=3"), {
    semesterId: 161,
    week: 8,
    view: "day",
    day: 3,
  });
  assert.deepEqual(parseHash("#/w=2&view=week"), { week: 2, view: "week" });
});

test("非法值被忽略", () => {
  assert.deepEqual(parseHash(""), {});
  assert.deepEqual(parseHash("#sem=-1&w=abc&d=9&view=agenda"), {});
  assert.deepEqual(parseHash("#w=0&d=-2"), {});
});

test("生成 hash 会省略无效项，并在 day 视图带上日期", () => {
  assert.equal(buildHash({ semesterId: 161, week: 8, view: "week" }), "#sem=161&w=8&view=week");
  assert.equal(buildHash({ semesterId: 161, week: 8, view: "day", day: 2 }), "#sem=161&w=8&view=day&d=2");
  assert.equal(buildHash({ semesterId: null, week: 0, view: "week" }), "#view=week");
  assert.equal(buildHash({}), "");
});

test("build / parse 往返一致", () => {
  const state = { semesterId: 161, week: 12, view: "day", day: 6 };
  assert.deepEqual(parseHash(buildHash(state)), state);
});
