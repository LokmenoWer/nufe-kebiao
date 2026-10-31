import assert from "node:assert/strict";
import test from "node:test";

import {
  PALETTE,
  buildColorMap,
  colorFor,
  formatUnits,
  groupByDay,
  initialOf,
  lessonsOfWeek,
  maxWeekOf,
  unitTimeMap,
} from "../../static/js/courses.js";

function activity(overrides = {}) {
  return {
    courseName: "高等数学",
    teachers: ["李四"],
    weekday: 1,
    startUnit: 1,
    endUnit: 2,
    weeksStr: "1~3",
    weekIndexes: [1, 2, 3],
    room: "A101",
    building: "",
    campus: "仙林",
    startTime: "08:20",
    endTime: "09:45",
    credits: 2,
    ...overrides,
  };
}

function timetable(activities) {
  return { semester_id: 161, semester_name: "2026-2027-1", week1_monday: "2026-09-07", activities };
}

test("按周筛选课程并计算分钟位置", () => {
  const tt = timetable([
    activity(),
    activity({ courseName: "英语", weekday: 3, weekIndexes: [2], startTime: "14:15", endTime: "16:35" }),
    activity({ courseName: "单周", weekIndexes: [1, 3] }),
  ]);
  const week1 = lessonsOfWeek(tt, 1);
  assert.deepEqual(week1.map((x) => x.courseName), ["高等数学", "单周"]);
  assert.deepEqual([week1[0].start, week1[0].end], [500, 585]);
  assert.equal(week1[0].day, 0);
  assert.equal(week1[0].color, colorFor("高等数学"));
  const week2 = lessonsOfWeek(tt, 2);
  assert.deepEqual(week2.map((x) => x.courseName).sort(), ["英语", "高等数学"].sort());
  assert.deepEqual(lessonsOfWeek(tt, 9), []);
});

test("缺少时间时用同节次其他课程兜底，再退到默认节次", () => {
  const tt = timetable([
    activity({ courseName: "有时间", startTime: "10:00", endTime: "11:15" }),
    activity({ courseName: "缺时间", startTime: "", endTime: "" }),
    activity({ courseName: "完全缺", startTime: "", endTime: "", startUnit: 7, endUnit: 8 }),
  ]);
  const lessons = lessonsOfWeek(tt, 1);
  const missing = lessons.find((x) => x.courseName === "缺时间");
  const fallback = lessons.find((x) => x.courseName === "完全缺");
  assert.deepEqual([missing.start, missing.end], [600, 675]);
  assert.equal(fallback.start, 480 + 6 * 55);
  assert.ok(fallback.end > fallback.start);
});

test("结束时间早于开始时间时兜底为正时长", () => {
  const tt = timetable([activity({ startTime: "10:00", endTime: "09:00" })]);
  const [lesson] = lessonsOfWeek(tt, 1);
  assert.ok(lesson.end > lesson.start);
});

test("无效 weekday / startUnit / 周次会被忽略", () => {
  const tt = timetable([
    activity({ courseName: "坏", weekday: 9 }),
    activity({ courseName: "坏2", startUnit: 0 }),
    activity({ courseName: "坏3", weekIndexes: ["abc", null, 0, 99], weeksStr: "" }),
    activity({ courseName: "好", weekIndexes: [1] }),
  ]);
  assert.deepEqual(lessonsOfWeek(tt, 1).map((x) => x.courseName), ["好"]);
});

test("maxWeek 至少 16，按最大周次扩展且封顶 60", () => {
  assert.equal(maxWeekOf(timetable([])), 16);
  assert.equal(maxWeekOf(timetable([activity({ weekIndexes: [1, 20] })])), 20);
  assert.equal(maxWeekOf(timetable([activity({ weekIndexes: [60] })])), 60);
});

test("节次时间表与格式化", () => {
  const map = unitTimeMap([activity({ startUnit: 3, endUnit: 4, startTime: "10:00", endTime: "11:15" })]);
  assert.equal(map.starts.get(3), "10:00");
  assert.equal(map.ends.get(4), "11:15");
  assert.equal(formatUnits(activity({ startUnit: 3, endUnit: 5 })), "第 3–5 节");
  assert.equal(formatUnits(activity({ startUnit: 3, endUnit: 3 })), "第 3 节");
  assert.equal(formatUnits(null), "");
});

test("同一学期内不超过调色板大小时颜色互不相同", () => {
  const names = ["高等数学", "大学英语", "线性代数", "概率论", "宏观经济学", "微观经济学",
    "会计学", "金融学", "统计学", "管理学", "市场营销", "数据结构"];
  const map = buildColorMap(names.map((courseName) => ({ courseName })));
  assert.equal(map.size, names.length);
  assert.equal(new Set(map.values()).size, names.length);
  assert.ok([...map.values()].every((color) => PALETTE.includes(color)));
  // 与顺序无关
  const reversed = buildColorMap([...names].reverse().map((courseName) => ({ courseName })));
  assert.deepEqual([...reversed.entries()].sort(), [...map.entries()].sort());
});

test("颜色稳定、头像文字与分组", () => {
  assert.ok(PALETTE.includes(colorFor("线性代数")));
  assert.equal(colorFor("线性代数"), colorFor("线性代数"));
  assert.equal(initialOf("示例同学"), "同学");
  assert.equal(initialOf("张三"), "张三");
  assert.equal(initialOf(""), "我");
  assert.equal(initialOf(null), "我");

  const groups = groupByDay([{ day: 0, name: "a" }, { day: 6, name: "b" }], 7);
  assert.equal(groups.length, 7);
  assert.equal(groups[0][0].name, "a");
  assert.equal(groups[6][0].name, "b");
});
