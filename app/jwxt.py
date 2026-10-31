"""教务系统（for-std）课表抓取与规范化。"""
from __future__ import annotations

import contextlib
import html as html_lib
import json
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

import requests

from . import cas, db

JW_BASE = "https://jwxt.nufe.edu.cn"
PRINT_DATA_MAX_BYTES = 2 * 1024 * 1024


class SessionExpired(Exception):
    """登录态失效（被重定向回统一身份认证）。"""


@dataclass
class CoursePage:
    person_id: int
    current_semester_id: int
    semesters: list[dict] = field(default_factory=list)  # [{id, name}]


def _top_level_id(html: str, var: str) -> int | None:
    """取 `var xxx = {...}` 这个对象字面量的顶层 'id'（对象里还嵌套着 calendarAssoc:{'id':1} 等）。"""
    m = re.search(rf"\b{re.escape(var)}\s*=\s*\{{", html)
    if not m:
        return None
    depth, quote, i = 0, None, m.end() - 1
    while i < len(html):
        ch = html[i]
        if quote:
            if ch == "\\":
                i += 1
            elif ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
            if depth == 1:
                km = re.match(r"(['\"])id\1\s*:\s*(\d+)", html[i:])
                if km:
                    return int(km.group(2))
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return None
        i += 1
    return None


def parse_course_page(html: str) -> CoursePage:
    m_person = re.search(r"\bpersonId\s*=\s*(\d+)", html)
    sem_id = _top_level_id(html, "currentSemester")
    if not m_person or sem_id is None:
        raise ValueError("课表页结构异常：未找到 personId / currentSemester")
    semesters = []
    m_sel = re.search(r"<select[^>]*id=[\"']allSemesters[\"'][^>]*>(.*?)</select>", html, re.S)
    if m_sel:
        for v, text in re.findall(r"<option[^>]*value=[\"'](\d+)[\"'][^>]*>([^<]+)</option>", m_sel.group(1)):
            semesters.append({"id": int(v), "name": html_lib.unescape(text).strip()})
    return CoursePage(int(m_person.group(1)), sem_id, semesters)


def _check(r: requests.Response) -> requests.Response:
    if urlparse(r.url).hostname == cas.AUTH_HOST or cas.is_jw_login_url(r.url):
        raise SessionExpired()
    r.raise_for_status()
    return r


def get_course_page(session: requests.Session) -> CoursePage:
    return parse_course_page(_check(session.get(cas.COURSE_TABLE_URL)).text)


def _read_capped(r: requests.Response, limit: int) -> bytes:
    """边下边数，超过上限立刻断开，不把超大响应整个读进内存。"""
    buf = bytearray()
    try:
        for chunk in r.iter_content(64 * 1024):
            buf += chunk
            if len(buf) > limit:
                raise ValueError("课表数据过大")
    finally:
        r.close()
    return bytes(buf)


def get_print_data(session: requests.Session, semester_id: int) -> dict:
    r = _check(session.get(
        f"{JW_BASE}/student/for-std/course-table/semester/{semester_id}/print-data",
        params={"semesterId": semester_id, "hasExperiment": "true"},
        headers={"Accept": "application/json, text/plain, */*", "X-Requested-With": "XMLHttpRequest"},
        stream=True,
    ))
    data = json.loads(_read_capped(r, PRINT_DATA_MAX_BYTES))
    if not isinstance(data, dict):
        raise ValueError("课表数据格式异常")
    return data


def get_semester_detail(session: requests.Session, semester_id: int) -> dict | None:
    try:
        r = _check(session.get(f"{JW_BASE}/student/ws/semester/get/{semester_id}",
                               headers={"Accept": "application/json"}))
        return r.json()
    except (ValueError, requests.HTTPError):
        return None


_PAREN = re.compile(r"\s*[(（].*$")  # "张三(工号)"、"张三(学院(兼职))" → "张三"


def _teacher_name(t) -> str:
    if t is None:
        return ""
    if isinstance(t, dict):
        t = t.get("name") or t.get("teacherName") or ""
    return _PAREN.sub("", str(t)).strip()


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize(print_data: dict) -> dict:
    """把 print-data 精简为前端/ICS 需要的结构。字段脏了就跳过，不让一条坏数据拖垮整份课表。"""
    vms = print_data.get("studentTableVms") or []
    if not isinstance(vms, list) or not vms:
        return {"student": {}, "activities": []}
    vm = vms[0] if isinstance(vms[0], dict) else {}
    student = {k: vm.get(k) for k in ("name", "code", "department", "major", "adminclass", "grade", "credits")}
    acts = []
    for a in vm.get("activities") or []:
        if not isinstance(a, dict):
            continue
        weekday, start = _as_int(a.get("weekday")), _as_int(a.get("startUnit"))
        if not weekday or not start or not (1 <= weekday <= 7) or start < 1:
            continue  # 无固定时间的课程（如实践周）不进周视图
        end = _as_int(a.get("endUnit")) or start
        if end < start:
            end = start
        weeks = []
        for w in a.get("weekIndexes") or []:
            wi = _as_int(w)
            if wi and 1 <= wi <= 60:
                weeks.append(wi)
        acts.append({
            "courseName": str(a.get("courseName") or ""),
            "teachers": [n for n in (_teacher_name(t) for t in a.get("teachers") or []) if n],
            "weekday": weekday,
            "startUnit": start,
            "endUnit": end,
            "weeksStr": str(a.get("weeksStr") or ""),
            "weekIndexes": sorted(set(weeks)),
            "room": str(a.get("room") or ""),
            "building": str(a.get("building") or ""),
            "campus": str(a.get("campus") or ""),
            "startTime": str(a.get("startTime") or ""),
            "endTime": str(a.get("endTime") or ""),
            "credits": a.get("credits"),
        })
    return {"student": student, "activities": acts}


def _week1_known(semester_id: int, name: str) -> bool:
    """校历文件或库里已有第 1 周周一时，不必再打学期详情接口。"""
    from .config import semester_overrides
    ov = semester_overrides()
    if (name and name in ov) or str(semester_id) in ov:
        return True
    row = db.connect().execute(
        "SELECT week1_monday FROM semesters WHERE semester_id=?", (semester_id,)).fetchone()
    return bool(row and row["week1_monday"])


def fetch_timetable(session: requests.Session, semester_id: int | None = None,
                    page_html: str | None = None) -> dict:
    if page_html is None:
        page_html = getattr(session, "kebiao_course_html", None)
        if page_html is not None:
            with contextlib.suppress(AttributeError):
                del session.kebiao_course_html
    page = parse_course_page(page_html) if page_html else get_course_page(session)
    requested = _as_int(semester_id)
    if requested is not None and requested < 1:
        raise ValueError("学期 id 不正确")
    sid = requested or page.current_semester_id
    data = normalize(get_print_data(session, sid))
    name = next((s["name"] for s in page.semesters if s["id"] == sid), str(sid))
    detail = None if _week1_known(sid, name) else get_semester_detail(session, sid)
    return {
        "person_id": page.person_id,
        "semester_id": sid,
        "semester_name": name,
        "current_semester_id": page.current_semester_id,
        "semesters": page.semesters,
        "semester_detail": detail,
        **data,
    }
