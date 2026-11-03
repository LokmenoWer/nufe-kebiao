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
CHANGE_SHOW = 7 * 86400   # 调课标记在小组件上保留多久

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


_WEEKDAY = "一二三四五六日"


def lesson_line(a: dict) -> str:
    """一条排课的人类可读描述，也作为比较课表变动的键（邮件、同班联动、小组件标记都用它）。"""
    day = a.get("weekday")
    day_txt = f"周{_WEEKDAY[day - 1]}" if isinstance(day, int) and 1 <= day <= 7 else "周?"
    start, end = a.get("startUnit"), a.get("endUnit")
    units = f"第{start}–{end}节" if start and end and end != start else (f"第{start}节" if start else "")
    clock = f"{a.get('startTime') or ''}–{a.get('endTime') or ''}".strip("–")
    when = " ".join(x for x in (day_txt, units, clock) if x)
    weeks = a.get("weeksStr") or ",".join(str(w) for w in (a.get("weekIndexes") or []))
    room = a.get("room") or a.get("building") or "地点待定"
    return f"{a.get('courseName') or '未命名课程'} · {when} · {room} · 第{weeks}周"


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


def _occurrence(lesson: dict) -> tuple:
    return lesson["name"], lesson["start"], lesson["end"], lesson["room"]


def _mark_changes(out: dict, day: dt.date, week1: dt.date, units: dict, colors: dict, changes: list) -> None:
    """按“这一天”对比改动前后：新出现的课标 changed（附“原 …”），整门课当天没了的列进 cancelled。

    比的是具体某天的上课安排而不是排课条目：教务常把“1~16 周”拆成“1~8,10~16 周”再补一条第 9 周，
    这时其他周什么都没变，不该被标出来。
    """
    before, after = [], []
    for ev in reversed(changes):  # 新的在前：同一节课改过几次，“原 …”和停课只认最近一次之前的样子
        before += lessons_on(day, ev.get("removed") or [], week1, units, colors)
        after += lessons_on(day, ev.get("added") or [], week1, units, colors)
    if not before and not after:
        return
    old = {_occurrence(x) for x in before}
    new = {_occurrence(x) for x in after}
    current = {_occurrence(x) for x in out["lessons"]}
    unpaired = [x for x in before if _occurrence(x) not in current]
    for lesson in out["lessons"]:
        key = _occurrence(lesson)
        if key in old or key not in new:
            continue
        lesson["changed"] = True
        # 同一天同一门课的旧安排：说明是改了教室还是改了时间
        was = next((x for x in unpaired if x["name"] == lesson["name"]), None)
        if not was:
            continue
        unpaired.remove(was)
        if was["start"] != lesson["start"]:
            lesson["was"] = f"原 {was['start']}"
        elif was["room"] != lesson["room"]:
            lesson["was"] = f"原 {was['room'] or '地点待定'}"
    cancelled, seen = [], set()
    for x in unpaired:
        if (x["name"], x["start"]) in seen:
            continue
        seen.add((x["name"], x["start"]))
        cancelled.append({k: x[k] for k in ("name", "start", "end", "room", "color")})
    if cancelled:
        out["cancelled"] = sorted(cancelled, key=lambda x: (x["start"], x["name"]))


def _day(day: dt.date, activities: list, week1: dt.date | None, units: dict, colors: dict,
         changes: list | None = None) -> dict:
    out = {"date": day.isoformat(), "weekday": day.isoweekday(), "week": None, "lessons": []}
    if week1:
        week = week_of(day, week1)
        if week >= 1:
            out["week"] = week
            out["lessons"] = lessons_on(day, activities, week1, units, colors)
            if changes:
                _mark_changes(out, day, week1, units, colors, changes)
    return out


UPCOMING_DAYS = 14


def upcoming(today: dt.date, activities: list, week1: dt.date | None, units: dict, colors: dict,
             changes: list | None = None) -> dict | None:
    """今天之后第一个有课的日子（两周内）：周末 / 放假前小组件可以提示“下次上课”。"""
    for i in range(1, UPCOMING_DAYS + 1):
        d = _day(today + dt.timedelta(days=i), activities, week1, units, colors, changes)
        if d["lessons"]:
            return d
    return None


def term_bounds(activities: list, week1: dt.date | None) -> dict | None:
    """本学期第一天 / 最后一天有课的日期：开学前显示倒计时，结课后显示“已结束”。"""
    if not week1:
        return None
    dates = []
    for a in activities:
        if not isinstance(a, dict) or not isinstance(a.get("weekday"), int) or not 1 <= a["weekday"] <= 7:
            continue
        for w in a.get("weekIndexes") or []:
            if isinstance(w, int) and 1 <= w <= 60:
                dates.append(week1 + dt.timedelta(weeks=w - 1, days=a["weekday"] - 1))
    if not dates:
        return None
    return {"first_day": min(dates).isoformat(), "last_day": max(dates).isoformat()}


def _week_block(monday: dt.date, activities: list, week1: dt.date | None, units: dict, colors: dict,
                changes: list | None = None) -> dict:
    days = [_day(monday + dt.timedelta(days=i), activities, week1, units, colors, changes) for i in range(7)]
    return {"week": days[0]["week"], "monday": monday.isoformat(), "days": days}


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


def recent_changes(events: list | None, semester_id, now: dt.datetime) -> list:
    """本学期、CHANGE_SHOW 内的调课记录（service 在发现变动时写入）。"""
    cutoff = now.timestamp() - CHANGE_SHOW
    return [ev for ev in events or [] if isinstance(ev, dict) and ev.get("semester_id") == semester_id
            and isinstance(ev.get("at"), int | float) and ev["at"] >= cutoff]


def build(view: dict | None, week1_iso: str | None, *, status: str = "ok", now: dt.datetime | None = None,
          script_version: str | None = None, changes: list | None = None) -> dict:
    """view: service.timetable_view() 的结果（当前学期）；week1_iso: 第 1 周周一。

    script_version：服务器上最新的小组件脚本版本，旧脚本据此提示“可更新”。
    changes：最近发现的调课 [{"at", "semester_id", "removed": [排课], "added": [排课]}]。
    """
    now = (now or dt.datetime.now(_TZ)).astimezone(_TZ)
    today = now.date()
    try:
        week1 = dt.date.fromisoformat(week1_iso) if week1_iso else None
    except ValueError:
        week1 = None
    activities = (view or {}).get("activities") or []
    units = unit_times([a for a in activities if isinstance(a, dict)])
    colors = color_map(activities)
    recent = recent_changes(changes, (view or {}).get("semester_id"), now)
    monday = today - dt.timedelta(days=today.weekday())
    today_d = _day(today, activities, week1, units, colors, recent)
    tomorrow_d = _day(today + dt.timedelta(days=1), activities, week1, units, colors, recent)
    this_week = _week_block(monday, activities, week1, units, colors, recent)
    return {
        "v": PAYLOAD_VERSION,
        "now": now.isoformat(timespec="seconds"),
        "semester": (view or {}).get("semester_name"),
        "semester_id": (view or {}).get("semester_id"),
        "script_version": script_version,
        "updated_at": (view or {}).get("fetched_at"),
        "status": status,               # ok | error | captcha | needs_rebind：脚本据此提示“需要重新登录”
        "has_calendar": week1 is not None,
        "today": today_d,
        "tomorrow": tomorrow_d,
        "week": this_week,
        # 周末本周已经没课时，week 模式改看下周
        "next_week": _week_block(monday + dt.timedelta(days=7), activities, week1, units, colors, recent),
        "term": term_bounds(activities, week1),
        "upcoming": upcoming(today, activities, week1, units, colors, recent),
        "next_change": next_change(now, [today_d, tomorrow_d]),
        # 最近一次发现调课的时刻：脚本据此在标题栏提示，并提醒到 Scriptable 里重排上课通知
        "changed_at": max((ev["at"] for ev in recent), default=None),
    }
