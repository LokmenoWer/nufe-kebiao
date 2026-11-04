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


_QUOTED_ID = re.compile(r"(['\"])id\1\s*:\s*(\d+)")
# 课表页可能是手机上传的（最大 1 MB）：下面的正则都先按 < 切开标签，单次匹配走不出当前标签，不会平方级回溯
_SELECT_TAG = re.compile(r"<select\b([^<>]*)>")
_OPTION = re.compile(r"<option\b([^<>]*)>([^<]+)</option>")
_ALL_SEMESTERS_ID = re.compile(r"\bid=[\"']allSemesters[\"']")
_OPTION_VALUE = re.compile(r"\bvalue=[\"'](\d+)[\"']")


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
                km = _QUOTED_ID.match(html, i)  # 传位置而不是切片：切片每遇到一个引号都要复制剩下的全文
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
    return CoursePage(int(m_person.group(1)), sem_id, _parse_semesters(html))


def _parse_semesters(html: str) -> list[dict]:
    tag = next((m for m in _SELECT_TAG.finditer(html) if _ALL_SEMESTERS_ID.search(m.group(1))), None)
    end = html.find("</select>", tag.end()) if tag else -1
    if end < 0:
        return []
    semesters = []
    for attrs, text in _OPTION.findall(html, tag.end(), end):
        value = _OPTION_VALUE.search(attrs)
        if value:
            semesters.append({"id": int(value.group(1)), "name": html_lib.unescape(text).strip()})
    return semesters


def _check(r: requests.Response) -> requests.Response:
    if urlparse(r.url).hostname == cas.AUTH_HOST or cas.is_jw_login_url(r.url):
        raise SessionExpired()
    r.raise_for_status()
    return r


def touch(session: requests.Session) -> bool:
    """教务会话保活：不跟随跳转地打开一次课表页。True = 教务会话还在。"""
    r = session.get(cas.COURSE_TABLE_URL, allow_redirects=False)
    if r.status_code >= 500:
        r.raise_for_status()
    return r.status_code == 200 and cas.looks_like_course_page(r.text)


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


def parse_print_data(raw: bytes | str) -> dict:
    if len(raw) > PRINT_DATA_MAX_BYTES:
        raise ValueError("课表数据过大")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("课表数据格式异常")
    return data


def print_data_url(semester_id: int) -> str:
    return f"{JW_BASE}/student/for-std/course-table/semester/{semester_id}/print-data?semesterId={semester_id}&hasExperiment=true"


def semester_detail_url(semester_id: int) -> str:
    return f"{JW_BASE}/student/ws/semester/get/{semester_id}"


PRINT_DATA_HEADERS = {"Accept": "application/json, text/plain, */*", "X-Requested-With": "XMLHttpRequest"}


def get_print_data(session: requests.Session, semester_id: int) -> dict:
    r = _check(session.get(print_data_url(semester_id), headers=PRINT_DATA_HEADERS, stream=True))
    return parse_print_data(_read_capped(r, PRINT_DATA_MAX_BYTES))


def get_semester_detail(session: requests.Session, semester_id: int) -> dict | None:
    try:
        r = _check(session.get(semester_detail_url(semester_id), headers={"Accept": "application/json"}))
        return r.json()
    except (ValueError, requests.HTTPError):
        return None


_PAREN = re.compile(r"[(（]")


def _teacher_name(t) -> str:
    """"张三(工号)"、"张三(学院(兼职))" → "张三"。"""
    if t is None:
        return ""
    if isinstance(t, dict):
        t = t.get("name") or t.get("teacherName") or ""
    name = str(t)
    paren = _PAREN.search(name)
    return (name[:paren.start()] if paren else name).strip()


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


def requested_semester(page: CoursePage, semester_id) -> int:
    requested = _as_int(semester_id)
    if requested is not None and requested < 1:
        raise ValueError("学期 id 不正确")
    return requested or page.current_semester_id


def build_timetable(page: CoursePage, semester_id: int, print_data: dict, detail: dict | None) -> dict:
    """课表页 + print-data（+ 学期详情）→ 存库用的结构。服务器自己抓的和小组件代抓上传的都走这里。"""
    name = next((s["name"] for s in page.semesters if s["id"] == semester_id), str(semester_id))
    return {
        "person_id": page.person_id,
        "semester_id": semester_id,
        "semester_name": name,
        "current_semester_id": page.current_semester_id,
        "semesters": page.semesters,
        "semester_detail": detail if isinstance(detail, dict) else None,
        **normalize(print_data),
    }


def week1_known(semester_id: int, semesters: list[dict]) -> bool:
    name = next((s["name"] for s in semesters if s["id"] == semester_id), str(semester_id))
    return _week1_known(semester_id, name)


def fetch_timetable(session: requests.Session, semester_id: int | None = None,
                    page_html: str | None = None) -> dict:
    if page_html is None:
        page_html = getattr(session, "kebiao_course_html", None)
        if page_html is not None:
            with contextlib.suppress(AttributeError):
                del session.kebiao_course_html
    page = parse_course_page(page_html) if page_html else get_course_page(session)
    sid = requested_semester(page, semester_id)
    print_data = get_print_data(session, sid)
    detail = None if week1_known(sid, page.semesters) else get_semester_detail(session, sid)
    return build_timetable(page, sid, print_data, detail)
