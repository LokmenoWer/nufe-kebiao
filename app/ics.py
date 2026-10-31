"""把课表导出为 iCalendar（每个排课 × 每个上课周 → 一个事件）。"""
from __future__ import annotations

import datetime as dt
import hashlib
import logging

from .config import REFRESH_INTERVAL_HOURS, TIMEZONE

log = logging.getLogger("kebiao.ics")

_VTIMEZONE = [
    "BEGIN:VTIMEZONE", f"TZID:{TIMEZONE}",
    "BEGIN:STANDARD", "DTSTART:19700101T000000", "TZOFFSETFROM:+0800", "TZOFFSETTO:+0800", "TZNAME:CST",
    "END:STANDARD", "END:VTIMEZONE",
]


def _esc(s: str) -> str:
    return (str(s).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n"))


def _fold(line: str) -> str:
    """RFC5545：每行最多 75 字节，续行以空格开头（不在 UTF-8 字符中间切断）。"""
    out, cur = [], b""
    for ch in line:
        b = ch.encode()
        if len(cur) + len(b) > (75 if not out else 74):
            out.append(cur.decode())
            cur = b""
        cur += b
    out.append(cur.decode())
    return "\r\n ".join(out)


def _hm(s) -> tuple[int, int] | None:
    try:
        h, m = str(s).split(":")[:2]
        h, m = int(h), int(m)
    except (ValueError, AttributeError, TypeError):
        return None
    if 0 <= h <= 23 and 0 <= m <= 59:
        return h, m
    return None


def unit_times(activities: list[dict]) -> dict[int, tuple[str, str]]:
    """从已知时间的课推出 "第 n 节" 的起止时间，给缺时间的课兜底。"""
    starts, ends = {}, {}
    for a in activities:
        su, eu = a.get("startUnit"), a.get("endUnit")
        if isinstance(su, int) and a.get("startTime"):
            starts.setdefault(su, a["startTime"])
        if isinstance(eu, int) and a.get("endTime"):
            ends.setdefault(eu, a["endTime"])
    return {u: (starts.get(u, ""), ends.get(u, "")) for u in set(starts) | set(ends)}


def _refresh_interval() -> str:
    hours = max(1.0, float(REFRESH_INTERVAL_HOURS))
    if hours == int(hours):
        return f"PT{int(hours)}H"
    return f"PT{int(hours * 60)}M"


def build(student_id: str, semesters: list[tuple[dict, str]]) -> str:
    """semesters: [(timetable_view, week1_monday_iso)]"""
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//nufe-kebiao//CN", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", "X-WR-CALNAME:南财课表", "X-WR-CALDESC:南京财经大学课表（非官方）",
             f"X-WR-TIMEZONE:{TIMEZONE}",
             f"REFRESH-INTERVAL;VALUE=DURATION:{_refresh_interval()}", *_VTIMEZONE]
    for tt, week1 in semesters:
        try:
            monday = dt.date.fromisoformat(week1)
        except (TypeError, ValueError):
            log.warning("skip semester %s: invalid week1 date %r", tt.get("semester_id"), week1)
            continue
        activities = tt.get("activities") or []
        units = unit_times(activities)
        for a in activities:
            weekday = a.get("weekday")
            if not isinstance(weekday, int) or not 1 <= weekday <= 7:
                continue
            st = _hm(a.get("startTime") or units.get(a.get("startUnit"), ("", ""))[0])
            et = _hm(a.get("endTime") or units.get(a.get("endUnit"), ("", ""))[1])
            if not st or not et or (et[0], et[1]) <= (st[0], st[1]):
                continue
            loc = " ".join(x for x in (a.get("campus", ""), a.get("room") or a.get("building", "")) if x)
            desc = (f"{'、'.join(a.get('teachers') or [])}\n"
                    f"第{a.get('startUnit')}-{a.get('endUnit')}节  {a.get('weeksStr', '')}周")
            for w in a.get("weekIndexes") or []:
                if not isinstance(w, int) or not 1 <= w <= 60:
                    continue
                day = monday + dt.timedelta(weeks=w - 1, days=weekday - 1)
                uid = hashlib.sha1(
                    f"{student_id}|{tt.get('semester_id')}|{a.get('courseName')}|{weekday}|"
                    f"{a.get('startUnit')}|{w}".encode()
                ).hexdigest()
                lines += [
                    "BEGIN:VEVENT", f"UID:{uid}@nufe-kebiao", f"DTSTAMP:{stamp}",
                    f"DTSTART;TZID={TIMEZONE}:{day:%Y%m%d}T{st[0]:02d}{st[1]:02d}00",
                    f"DTEND;TZID={TIMEZONE}:{day:%Y%m%d}T{et[0]:02d}{et[1]:02d}00",
                    f"SUMMARY:{_esc(a.get('courseName', ''))}", f"LOCATION:{_esc(loc)}",
                    f"DESCRIPTION:{_esc(desc)}", "END:VEVENT",
                ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
