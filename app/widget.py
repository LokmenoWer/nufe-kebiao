"""iOS 桌面小组件（Scriptable）的数据：今天 / 明天 / 本周的课，按北京时间在服务端算好。

脚本只负责排版，周次、星期、缺失时间的兜底都和网页、ICS 用同一套规则，避免两边算得不一样。
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from .config import TIMEZONE
from .ics import _hm, unit_times

PAYLOAD_VERSION = 1
_TZ = ZoneInfo(TIMEZONE)

# 与 static/js/courses.js 完全一致：同一门课在网页和小组件上颜色相同
PALETTE = [
    "#4b63e0", "#0f8b8d", "#2f9e44", "#e8590c", "#ae3ec9", "#1971c2",
    "#c2255c", "#5c940d", "#e67700", "#6741d9", "#0b7285", "#d9480f",
    "#087f5b", "#a61e4d", "#3b5bdb", "#1098ad", "#74b816", "#f08c00",
    "#9c36b5", "#495057",
]


def _hash_name(name: str) -> int:
    h = 2166136261
    for ch in name:
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def color_map(activities: list) -> dict[str, str]:
    """courses.js 的 buildColorMap：按课程名排序，从哈希位置找第一个没用过的颜色。"""
    names = sorted({str(a.get("courseName") or "") for a in activities if isinstance(a, dict)})
    used: set[int] = set()
    out = {}
    for name in names:
        index = _hash_name(name) % len(PALETTE)
        for _ in range(len(PALETTE)):
            if index not in used:
                break
            index = (index + 1) % len(PALETTE)
        used.add(index)
        out[name] = PALETTE[index]
    return out


def _lesson(a: dict, units: dict, colors: dict) -> dict | None:
    st = _hm(a.get("startTime") or units.get(a.get("startUnit"), ("", ""))[0])
    et = _hm(a.get("endTime") or units.get(a.get("endUnit"), ("", ""))[1])
    if not st or not et or et <= st:
        return None
    su, eu = a.get("startUnit"), a.get("endUnit")
    if isinstance(su, int) and isinstance(eu, int) and eu != su:
        unit_txt = f"{su}–{eu}"
    elif isinstance(su, int):
        unit_txt = str(su)
    else:
        unit_txt = ""
    return {
        "name": a.get("courseName") or "未命名课程",
        "start": f"{st[0]:02d}:{st[1]:02d}",
        "end": f"{et[0]:02d}:{et[1]:02d}",
        "room": a.get("room") or a.get("building") or "",
        "units": unit_txt,
        "teachers": "、".join(t for t in (a.get("teachers") or []) if isinstance(t, str)),
        "color": colors.get(str(a.get("courseName") or ""), PALETTE[0]),
    }


def week_of(day: dt.date, week1: dt.date) -> int:
    return (day - week1).days // 7 + 1


def lessons_on(day: dt.date, activities: list, week1: dt.date, units: dict, colors: dict) -> list[dict]:
    week = week_of(day, week1)
    out = []
    for a in activities:
        if not isinstance(a, dict) or a.get("weekday") != day.isoweekday():
            continue
        weeks = a.get("weekIndexes") or []
        if week not in weeks:
            continue
        lesson = _lesson(a, units, colors)
        if lesson:
            out.append(lesson)
    return sorted(out, key=lambda x: (x["start"], x["end"], x["name"]))


def _day(day: dt.date, activities: list, week1: dt.date | None, units: dict, colors: dict) -> dict:
    out = {"date": day.isoformat(), "weekday": day.isoweekday(), "week": None, "lessons": []}
    if week1:
        week = week_of(day, week1)
        if week >= 1:
            out["week"] = week
            out["lessons"] = lessons_on(day, activities, week1, units, colors)
    return out


UPCOMING_DAYS = 14


def upcoming(today: dt.date, activities: list, week1: dt.date | None, units: dict, colors: dict) -> dict | None:
    """今天之后第一个有课的日子（两周内）：周末 / 放假前小组件可以提示“下次上课”。"""
    for i in range(1, UPCOMING_DAYS + 1):
        d = _day(today + dt.timedelta(days=i), activities, week1, units, colors)
        if d["lessons"]:
            return d
    return None


def next_change(now: dt.datetime, days: list[dict]) -> str | None:
    """下一个上课 / 下课时刻：小组件在这之后刷新，“正在上课 / 下一节”才能准时切换。"""
    moments = []
    for d in days:
        date = dt.date.fromisoformat(d["date"])
        for lesson in d["lessons"]:
            for hm in (lesson["start"], lesson["end"]):
                h, m = map(int, hm.split(":"))
                moments.append(dt.datetime.combine(date, dt.time(h, m), _TZ))
    future = sorted(m for m in moments if m > now)
    return future[0].isoformat() if future else None


def build(view: dict | None, week1_iso: str | None, *, status: str = "ok", now: dt.datetime | None = None) -> dict:
    """view: service.timetable_view() 的结果（当前学期）；week1_iso: 第 1 周周一。"""
    now = (now or dt.datetime.now(_TZ)).astimezone(_TZ)
    today = now.date()
    try:
        week1 = dt.date.fromisoformat(week1_iso) if week1_iso else None
    except ValueError:
        week1 = None
    activities = (view or {}).get("activities") or []
    units = unit_times([a for a in activities if isinstance(a, dict)])
    colors = color_map(activities)
    monday = today - dt.timedelta(days=today.weekday())
    today_d = _day(today, activities, week1, units, colors)
    tomorrow_d = _day(today + dt.timedelta(days=1), activities, week1, units, colors)
    week_days = [_day(monday + dt.timedelta(days=i), activities, week1, units, colors) for i in range(7)]
    return {
        "v": PAYLOAD_VERSION,
        "now": now.isoformat(timespec="seconds"),
        "semester": (view or {}).get("semester_name"),
        "updated_at": (view or {}).get("fetched_at"),
        "status": status,               # ok | error | captcha | needs_rebind：脚本据此提示“需要重新登录”
        "has_calendar": week1 is not None,
        "today": today_d,
        "tomorrow": tomorrow_d,
        "week": {"week": today_d["week"], "monday": monday.isoformat(), "days": week_days},
        "upcoming": upcoming(today, activities, week1, units, colors),
        "next_change": next_change(now, [today_d, tomorrow_d]),
    }
