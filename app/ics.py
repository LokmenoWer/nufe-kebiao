"""把课表导出为 iCalendar（每个排课 × 每个上课周 → 一个事件）。"""
from __future__ import annotations

import datetime as dt
import hashlib

from .config import TIMEZONE

_VTIMEZONE = [
    "BEGIN:VTIMEZONE", f"TZID:{TIMEZONE}",
    "BEGIN:STANDARD", "DTSTART:19700101T000000", "TZOFFSETFROM:+0800", "TZOFFSETTO:+0800", "TZNAME:CST",
    "END:STANDARD", "END:VTIMEZONE",
]


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


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


def _hm(s: str) -> tuple[int, int] | None:
    try:
        h, m = s.split(":")[:2]
        return int(h), int(m)
    except (ValueError, AttributeError):
        return None


def unit_times(activities: list[dict]) -> dict[int, tuple[str, str]]:
    """从已知时间的课推出 "第 n 节" 的起止时间，给缺时间的课兜底。"""
    starts, ends = {}, {}
    for a in activities:
        if a.get("startTime"):
            starts.setdefault(a["startUnit"], a["startTime"])
        if a.get("endTime"):
            ends.setdefault(a["endUnit"], a["endTime"])
    return {u: (starts.get(u, ""), ends.get(u, "")) for u in set(starts) | set(ends)}


def build(student_id: str, semesters: list[tuple[dict, str]]) -> str:
    """semesters: [(timetable_view, week1_monday_iso)]"""
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//nufe-kebiao//CN", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", "X-WR-CALNAME:南财课表", f"X-WR-TIMEZONE:{TIMEZONE}",
             "REFRESH-INTERVAL;VALUE=DURATION:PT12H", *_VTIMEZONE]
    for tt, week1 in semesters:
        monday = dt.date.fromisoformat(week1)
        units = unit_times(tt["activities"])
        for a in tt["activities"]:
            st = _hm(a["startTime"] or units.get(a["startUnit"], ("", ""))[0])
            et = _hm(a["endTime"] or units.get(a["endUnit"], ("", ""))[1])
            if not st or not et:
                continue
            loc = " ".join(x for x in (a["campus"], a["room"] or a["building"]) if x)
            desc = f"{'、'.join(a['teachers'])}\n第{a['startUnit']}-{a['endUnit']}节  {a['weeksStr']}周"
            for w in a["weekIndexes"]:
                day = monday + dt.timedelta(weeks=w - 1, days=a["weekday"] - 1)
                uid = hashlib.sha1(
                    f"{student_id}|{tt['semester_id']}|{a['courseName']}|{a['weekday']}|{a['startUnit']}|{w}".encode()
                ).hexdigest()
                lines += [
                    "BEGIN:VEVENT", f"UID:{uid}@nufe-kebiao", f"DTSTAMP:{stamp}",
                    f"DTSTART;TZID={TIMEZONE}:{day:%Y%m%d}T{st[0]:02d}{st[1]:02d}00",
                    f"DTEND;TZID={TIMEZONE}:{day:%Y%m%d}T{et[0]:02d}{et[1]:02d}00",
                    f"SUMMARY:{_esc(a['courseName'])}", f"LOCATION:{_esc(loc)}",
                    f"DESCRIPTION:{_esc(desc)}", "END:VEVENT",
                ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(l) for l in lines) + "\r\n"
