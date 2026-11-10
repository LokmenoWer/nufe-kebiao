import base64
import contextlib
import time

import pytest
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app import cas, crypto, ics, jwxt


def test_encrypt_password_recoverable_without_iv():
    salt = "AbCdEfGhJkMnPqRs"
    blob = base64.b64decode(cas.encrypt_password("p@ss中文", salt))
    # 服务端视角：随便用一个 IV 解密，只有第一个块会乱，64 位前缀丢掉后就是真实密码
    dec = Cipher(algorithms.AES(salt.encode()), modes.CBC(b"0" * 16)).decryptor()
    raw = dec.update(blob) + dec.finalize()
    unpad = padding.PKCS7(128).unpadder()
    raw = unpad.update(raw) + unpad.finalize()
    assert raw[64:].decode() == "p@ss中文"
    assert all(chr(c) in cas._AES_CHARS for c in raw[16:64])


def test_parse_login_form_prefers_pwd_form():
    html = """<form id="phoneFromId"><input id="pwdEncryptSalt" value="PHONESALT0000000"><input name="execution" value="x"></form>
    <form id="pwdFromId"><input type="hidden" id="pwdEncryptSalt" value="RealSalt12345678">
    <input name="lt" value=""><input name="execution" value="e1s1"></form>"""
    f = cas.parse_login_form(html)
    assert f == {"salt": "RealSalt12345678", "execution": "e1s1", "lt": ""}


def test_url_classifiers():
    assert cas.is_auth_login_url("https://authserver.nufe.edu.cn/authserver/login?service=x")
    assert cas.is_jw_login_url("https://jwxt.nufe.edu.cn/student/login?refer=x")
    assert not cas.is_logged_out_url("https://jwxt.nufe.edu.cn/student/for-std/course-table")


def test_parse_course_page():
    html = """<script>var personId = 123456; var bizTypeId = 2;
    var currentSemester = {'approvedYear':'2026','calendarAssoc':{'id':1},'endDate':{'zone':{'ID':'UTC'}},
      'name':'a}b\\'c','id':161,'code':'x'};</script>
    <select id="allSemesters"><option value="161" selected>2026-2027-1</option><option value="141">2025-2026-2</option></select>"""
    p = jwxt.parse_course_page(html)
    assert p.person_id == 123456 and p.current_semester_id == 161
    assert p.semesters == [{"id": 161, "name": "2026-2027-1"}, {"id": 141, "name": "2025-2026-2"}]


def test_normalize_strips_teacher_ids():
    d = jwxt.normalize({"studentTableVms": [{"name": "张三", "code": "2023001", "activities": [
        {"courseName": "高数", "teachers": ["李四(10086)", "王五（2001）", "赵六(马院(兼职))"], "weekday": 1, "startUnit": 1, "endUnit": 2,
         "weeksStr": "1~3(单)", "weekIndexes": [1, 3], "room": "A101", "startTime": "8:20", "endTime": "9:45"},
        {"courseName": "实践周", "weekday": None, "startUnit": None},
    ]}]})
    assert d["student"]["code"] == "2023001"
    assert len(d["activities"]) == 1
    assert d["activities"][0]["teachers"] == ["李四", "王五", "赵六"]


def test_aead_bound_to_user():
    blob = crypto.encrypt(b"secret", "password", 1)
    assert crypto.decrypt(blob, "password", 1) == b"secret"
    try:
        crypto.decrypt(blob, "password", 2)
    except Exception:
        pass
    else:
        raise AssertionError("ciphertext must not decrypt for another user")


def test_ics_build():
    tt = {"semester_id": 161, "activities": [
        {"courseName": "高数,一", "teachers": ["李四"], "weekday": 3, "startUnit": 1, "endUnit": 2, "weeksStr": "1-2",
         "weekIndexes": [1, 2], "room": "A101", "building": "", "campus": "仙林", "startTime": "8:20", "endTime": "9:45"},
        {"courseName": "缺时间", "teachers": [], "weekday": 1, "startUnit": 1, "endUnit": 2, "weeksStr": "1",
         "weekIndexes": [1], "room": "", "building": "", "campus": "", "startTime": "", "endTime": ""},
    ]}
    out = ics.build("2023001", [(tt, "2026-09-07")])
    assert out.count("BEGIN:VEVENT") == 3
    assert "DTSTART;TZID=Asia/Shanghai:20260909T082000" in out  # 第1周周三
    assert "DTSTART;TZID=Asia/Shanghai:20260916T082000" in out  # 第2周周三
    assert "DTSTART;TZID=Asia/Shanghai:20260907T082000" in out  # 缺时间的课用节次兜底
    assert "SUMMARY:高数\\,一" in out
    assert all(len(line.encode()) <= 75 for line in out.split("\r\n"))


def test_reauth_page_parsing():
    html = """<script>var url = contextPath + '/dynamicCode/getDynamicCodeByReauth.do';
    $.post('/authserver/reAuthCheck/reAuthSubmit.do', data)</script>
    var reAuthParams = {"reAuthType":"3", "isMultifactor":"true",
      "service":"https:\\/\\/jwxt.nufe.edu.cn\\/student\\/sso\\/login", "reAuthUserId":"2023001"};</script>
    <input id="username" value="张三(138****1234)" disabled>"""
    url = "https://authserver.nufe.edu.cn/authserver/reAuthCheck/reAuthLoginView.do?isMultifactor=true&service=x"
    assert cas.is_reauth_page(url, html)
    info = cas.parse_reauth_page(url, html)
    assert info["submit_url"] == "https://authserver.nufe.edu.cn/authserver/reAuthCheck/reAuthSubmit.do"
    assert info["send_url"] == "https://authserver.nufe.edu.cn/authserver/dynamicCode/getDynamicCodeByReauth.do"
    assert info["phone"] == "138****1234"
    assert info["service"] == "https://jwxt.nufe.edu.cn/student/sso/login"
    assert info["reAuthType"] == "3" and info["isMultifactor"] == "true" and info["userId"] == "2023001"


def test_parse_qr_form():
    html = """<form id="pwdFromId"><input name="execution" value="e9s9"></form>
    <form id="qrLoginForm" action="/authserver/login" method="post"><input id="lt" name="lt" value="">
    <input id="uuid" name="uuid" value=""><input name="cllt" value="qrLogin"><input name="dllt" value="generalLogin">
    <input name="execution" value="e1s1"><input name="_eventId" value="submit"><input name="rmShown" value="1"></form>"""
    f = cas.parse_qr_form(html)
    assert f["execution"] == "e1s1" and f["cllt"] == "qrLogin" and f["rmShown"] == "1"


def test_upstream_rate_floor():
    from app import polite
    assert polite.MIN_INTERVAL >= 1.0
    assert polite._min_interval("0.2") == 1.0      # 配置不能把间隔调到 1 秒以下
    assert polite._min_interval("2.5") == 2.5      # 只能调大
    assert polite._min_interval("abc") == 1.0
    assert polite._min_interval(None) == 1.0


def test_reauth_url_must_stay_on_auth_host():
    html = "<script>'https://evil.example/reAuthSubmit.do'</script>"
    url = "https://authserver.nufe.edu.cn/authserver/reAuthCheck/reAuthLoginView.do?isMultifactor=true"
    try:
        cas.parse_reauth_page(url, html)
    except cas.CasError as e:
        assert "认证服务器" in str(e)
    else:
        raise AssertionError("off-host reauth URL must be rejected")


def test_safe_image_type_rejects_svg():
    assert cas.safe_image_type("image/png; charset=binary") == "image/png"
    assert cas.safe_image_type("image/svg+xml") is None
    assert cas.safe_image_type("text/html") is None
    assert cas.safe_image_type("") is None


def test_known_week1_skips_semester_detail(monkeypatch):
    monkeypatch.setattr(jwxt, "get_course_page", lambda s: (_ for _ in ()).throw(AssertionError("refetch")))
    monkeypatch.setattr(jwxt, "get_print_data", lambda s, sid: {"studentTableVms": []})
    monkeypatch.setattr(jwxt, "get_semester_detail", lambda *a, **k: (_ for _ in ()).throw(AssertionError("detail")))
    html = """<script>var personId = 123456; var currentSemester = {'id':161};</script>
    <select id="allSemesters"><option value="161">2026-2027-1</option></select>"""
    data = jwxt.fetch_timetable(object(), page_html=html)
    assert data["semester_id"] == 161 and data["semester_detail"] is None and data["person_id"] == 123456


def test_unknown_semester_fetches_detail(monkeypatch):
    from app import db
    db.init()
    called = []
    monkeypatch.setattr(jwxt, "get_course_page", lambda s: jwxt.CoursePage(1, 999, [{"id": 999, "name": "1999-2000-1"}]))
    monkeypatch.setattr(jwxt, "get_print_data", lambda s, sid: {"studentTableVms": []})
    monkeypatch.setattr(jwxt, "get_semester_detail", lambda s, sid: called.append(sid) or {"startDate": "1999-09-01"})
    data = jwxt.fetch_timetable(object())
    assert called == [999] and data["semester_detail"]["startDate"] == "1999-09-01"


def test_print_data_rejects_oversized_body():
    class R:
        url = "https://jwxt.nufe.edu.cn/student/for-std/course-table"
        content = b"x" * (jwxt.PRINT_DATA_MAX_BYTES + 1)
        closed = False

        def raise_for_status(self):
            return None

        def iter_content(self, n):
            for i in range(0, len(self.content), n):
                yield self.content[i:i + n]

        def close(self):
            R.closed = True

        def json(self):
            return {}

    class S:
        def get(self, *a, **k):
            return R()

    try:
        jwxt.get_print_data(S(), 1)
    except ValueError as e:
        assert "过大" in str(e) and R.closed
    else:
        raise AssertionError("oversized print-data must be rejected")


def test_rate_limiter_drops_expired_keys():
    import time

    from app.security import RateLimiter
    lim = RateLimiter(2, 0.05)
    assert lim.hit("gone")
    time.sleep(0.06)
    assert lim.hit("stay")
    assert "gone" not in lim._hits and "stay" in lim._hits


def test_background_yields_when_interactive_waiting():
    import requests

    from app import polite
    polite._interactive_waiters = 1
    try:
        req = requests.Request("GET", "https://authserver.nufe.edu.cn/").prepare()
        with polite.background_upstream():
            try:
                polite.PoliteSession().send(req)
            except polite.UpstreamYield:
                return
            raise AssertionError("background send must yield while an interactive request is waiting")
    finally:
        polite._interactive_waiters = 0


def test_pace_spacing_across_threads():
    """多个线程同时发请求：发起时刻两两间隔都 ≥ 1 秒（全站每秒最多 1 次）。"""
    import threading
    import time

    from app import polite
    polite._last_request_at = 0.0
    polite.reset_health()
    stamps, lock = [], threading.Lock()

    def worker():
        polite._pace(True)
        with lock:
            stamps.append(time.monotonic())

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    stamps.sort()
    gaps = [b - a for a, b in zip(stamps, stamps[1:], strict=False)]
    assert len(gaps) == 2 and all(g >= 1.0 for g in gaps), gaps


# ---------- 回归：解析边界 ----------

_MB = 1024 * 1024


@pytest.mark.parametrize("html", [
    # 顶层很多字符串：旧实现每遇到一个引号就复制一遍剩下的全文
    "var personId = 1; var currentSemester = {" + "'a':1," * (_MB // 6) + "'id': 161};",
    # 下面这些没有闭合的标签：旧的正则会从每个开头一路回溯
    "var personId = 1; var currentSemester = {'id': 161};" + "<select " * (_MB // 8),
    "var personId = 1; var currentSemester = {'id': 161};<select " + "id='allSemesters'" * (_MB // 17),
    "var personId = 1; var currentSemester = {'id': 161};<select id='allSemesters'><option "
    + "value='1'" * (_MB // 9) + "</select>",
    "var personId = 1; var currentSemester = {'id': 161};<select id='allSemesters'>" + "<option " * (_MB // 8)
    + "</select>",
])
def test_course_page_parses_large_hostile_input_quickly(html):
    assert len(html) <= _MB + 100
    started = time.monotonic()
    page = jwxt.parse_course_page(html)
    assert time.monotonic() - started < 3
    assert page.person_id == 1 and page.current_semester_id == 161


def test_course_page_semester_list_variants():
    html = """var personId = 1; var currentSemester = {'id': 161};
    <select class="x" id="other"><option value="9">不是这个</option></select>
    <select name="s" id='allSemesters' data-a="1">
      <option selected value="161" data-x="y"> 2026-2027-1 &amp; </option>
      <option value='160'>2025-2026-2</option>
      <option>没有 value</option>
    </select>
    <option value="8">选择框外面</option>"""
    assert jwxt.parse_course_page(html).semesters == [
        {"id": 161, "name": "2026-2027-1 &"}, {"id": 160, "name": "2025-2026-2"}]
    no_close = "var personId = 1; var currentSemester = {'id': 161};<select id='allSemesters'><option value='1'>a</option>"
    assert jwxt.parse_course_page(no_close).semesters == []


def test_teacher_name_strips_parens_and_survives_long_whitespace():
    assert jwxt._teacher_name("张三 (工号)") == "张三"
    assert jwxt._teacher_name("张三（学院(兼职)）") == "张三"
    assert jwxt._teacher_name({"name": " 李四 "}) == "李四"
    started = time.monotonic()
    assert jwxt._teacher_name(" " * (2 * _MB)) == ""
    assert jwxt._teacher_name("(" * (2 * _MB) + "\n") == ""
    assert time.monotonic() - started < 3


def test_course_page_word_boundaries():
    html = """<script>var mypersonId = 999; var personId = 123;
    var mycurrentSemester = {'id': 999}; var currentSemester = {'id': 161};</script>"""
    p = jwxt.parse_course_page(html)
    assert p.person_id == 123 and p.current_semester_id == 161
    try:
        jwxt.parse_course_page("<script>var mypersonId=1; var mycurrentSemester={'id':2};</script>")
    except ValueError:
        pass
    else:
        raise AssertionError("前缀变量不应被当成 personId/currentSemester")


def test_normalize_ignores_dirty_activities():
    d = jwxt.normalize({"studentTableVms": [{"activities": [
        {"courseName": "坏 weekday", "weekday": "abc", "startUnit": 1},
        {"courseName": "越界 weekday", "weekday": 9, "startUnit": 1},
        {"courseName": "坏 startUnit", "weekday": 1, "startUnit": 0},
        {"courseName": "正常", "weekday": "3", "startUnit": "1", "endUnit": 0,
         "weekIndexes": ["2", None, 99, -1, 3], "teachers": [None, {"name": None}, "A(1)"],
         "room": None, "startTime": None},
    ]}]})
    acts = d["activities"]
    assert len(acts) == 1
    a = acts[0]
    assert a["weekday"] == 3 and a["startUnit"] == 1 and a["endUnit"] == 1
    assert a["weekIndexes"] == [2, 3] and a["teachers"] == ["A"]
    assert a["room"] == "" and a["startTime"] == ""


def test_cas_rejects_error_page_as_logged_in():
    class Resp:
        def __init__(self, url, text="", status=200):
            self.url, self.text, self.status_code = url, text, status
            self.content = text.encode()
            self.history = []
            self.request = None
            self.raw = type("Raw", (), {"headers": type("H", (), {"getlist": lambda *a: []})()})()

    class Session:
        headers = {}
        cookies = []

        def get(self, url, **kw):
            return Resp(cas.COURSE_TABLE_URL, "<html>500</html>", 500)

    flow = cas.LoginFlow(Session(), "2023001", "pw")
    try:
        flow._open_login_page()
    except cas.CasError as e:
        assert "教务系统" in str(e) and flow.stage != "done"
    else:
        raise AssertionError("500 页面不能当作已登录")

    # 课程表页结构正常时，直接复用页面，不再多发一次请求
    class OkSession(Session):
        def get(self, url, **kw):
            return Resp(cas.COURSE_TABLE_URL, "<script>var personId = 1; var currentSemester = {'id': 161};</script>")

    flow = cas.LoginFlow(OkSession(), "2023001", "pw")
    assert flow._open_login_page() is None and flow.stage == "done"
    assert "personId" in (flow.course_html or "")


def test_ics_alarm():
    tt = {"semester_id": 161, "activities": [
        {"courseName": "高数", "teachers": [], "weekday": 3, "startUnit": 1, "endUnit": 2, "weeksStr": "1-2",
         "weekIndexes": [1, 2], "room": "A101", "building": "", "campus": "", "startTime": "8:20", "endTime": "9:45"},
    ]}
    assert "VALARM" not in ics.build("2023001", [(tt, "2026-09-07")])
    out = ics.build("2023001", [(tt, "2026-09-07")], 15)
    assert out.count("BEGIN:VALARM") == 2 and out.count("TRIGGER:-PT15M") == 2
    assert out.index("BEGIN:VALARM") < out.index("END:VEVENT")
    assert [ics.parse_alarm(v) for v in (None, "", "abc", "0", "-5", "10", " 30 ", "120", "121")] == \
        [0, 0, 0, 0, 0, 10, 30, 120, 0]


def test_ics_skips_invalid_time_ranges():
    tt = {"semester_id": 1, "activities": [
        {"courseName": "倒序", "teachers": [], "weekday": 1, "startUnit": 1, "endUnit": 2,
         "weeksStr": "1", "weekIndexes": [1], "room": "", "building": "", "campus": "",
         "startTime": "10:00", "endTime": "09:00"},
        {"courseName": "正常", "teachers": [], "weekday": 1, "startUnit": 1, "endUnit": 2,
         "weeksStr": "1", "weekIndexes": [1], "room": "", "building": "", "campus": "",
         "startTime": "08:00", "endTime": "09:00"},
    ]}
    out = ics.build("2023001", [(tt, "2026-09-07")])
    assert out.count("BEGIN:VEVENT") == 1 and "正常" in out and "倒序" not in out


def test_load_cookies_skips_corrupt_entries():
    import requests

    from app.polite import load_cookies
    session = requests.Session()
    load_cookies(session, [
        {"name": "A", "value": "1", "domain": "example.com", "path": "/"},
        {"name": None},
        "not-a-dict",
    ])
    assert session.cookies.get("A") == "1"


def test_db_tx_rolls_back_on_exception():
    from app import db
    db.init()
    with db.tx() as conn:
        conn.execute("INSERT INTO semesters(semester_id, name) VALUES(?,?)", (987654, "rollback"))
    try:
        with db.tx() as conn:
            conn.execute("INSERT INTO semesters(semester_id, name) VALUES(?,?)", (987655, "rollback"))
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    count = db.connect().execute("SELECT COUNT(*) FROM semesters WHERE semester_id IN (987654,987655)").fetchone()[0]
    assert count == 1
    db.connect().execute("DELETE FROM semesters WHERE semester_id=?", (987654,))


# ---------- 上游自适应退避 ----------

def test_retry_after_pauses_and_background_yields():
    import pytest

    from app import polite
    polite.reset_health()
    try:
        polite._record(429, "120")
        assert 100 < polite.cooldown_remaining() <= 120
        s = polite.PoliteSession()
        # 暂停期间：后台刷新直接让路，交互请求不陪等 2 分钟，都不会真的发出请求
        with polite.background_upstream(), pytest.raises(polite.UpstreamYield):
            s.get("https://jwxt.invalid/")
        with pytest.raises(polite.UpstreamCooldown) as e:
            s.get("https://jwxt.invalid/")
        assert 100 < e.value.retry_after <= 120
    finally:
        polite.reset_health()


def test_retry_after_is_capped_and_defaulted():
    from app import polite
    polite.reset_health()
    try:
        polite._record(503, "999999")
        assert polite.cooldown_remaining() <= polite.MAX_COOLDOWN
        polite.reset_health()
        polite._record(429, None)
        assert 0 < polite.cooldown_remaining() <= polite.DEFAULT_COOLDOWN
    finally:
        polite.reset_health()


def test_error_penalty_grows_and_decays():
    from app import polite
    polite.reset_health()
    try:
        for _ in range(10):
            polite._record(None)  # 超时 / 连不上
        assert polite._penalty == polite.MAX_PENALTY
        assert polite.cooldown_remaining() == 0  # 普通 5xx/超时只放慢，不整体暂停
        for _ in range(20):
            polite._record(200)
        assert polite._penalty == 0
    finally:
        polite.reset_health()


def _fake_upstream(monkeypatch, seen=None):
    """替换底层 HTTPAdapter.send：/a 302 到 /b，/b 返回 200，全程不联网。"""
    import time

    import requests
    from requests.adapters import HTTPAdapter

    from app import polite
    polite.reset_health()
    monkeypatch.setattr(polite, "MIN_INTERVAL", 0.0)
    monkeypatch.setattr(polite, "JITTER", 0.0)

    def send(self, request, timeout=None, **kwargs):
        if seen is not None:
            seen.append(timeout)
        r = requests.Response()
        r.request, r.url, r._content = request, request.url, b""
        if request.url.endswith("/a"):
            time.sleep(0.2)  # 让并发的两条都先拿到名额再撞上重定向
            r.status_code, r.headers["Location"] = 302, "https://jwxt.invalid/b"
        else:
            r.status_code = 200
        return r

    monkeypatch.setattr(HTTPAdapter, "send", send)


def test_default_timeout_applies(monkeypatch):
    from app import polite
    seen = []
    _fake_upstream(monkeypatch, seen)
    s = polite.PoliteSession()
    s.get("https://jwxt.invalid/b")
    s.get("https://jwxt.invalid/b", timeout=5)
    assert seen == [polite.TIMEOUT, 5]


def test_concurrent_redirects_do_not_deadlock(monkeypatch):
    import threading

    from app import polite
    _fake_upstream(monkeypatch)
    for slots in (2, 1):  # 并发上限 1 时，单条重定向也不能自锁
        monkeypatch.setattr(polite, "_slots", threading.BoundedSemaphore(slots))
        done = []
        # lambda 的 done=done 显式绑定当轮列表：列表在循环内重赋值，B023 按晚绑定保守报警
        threads = [threading.Thread(target=lambda done=done: done.append(polite.PoliteSession().get("https://jwxt.invalid/a")),
                                    daemon=True) for _ in range(2)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(5)
        assert [r.status_code for r in done] == [200, 200]
        assert all(len(r.history) == 1 for r in done)


def test_renew_with_tgc_needs_castgc():
    import requests
    # 没有 CASTGC 时直接返回 False，不发任何请求
    assert cas.renew_with_tgc(requests.Session()) is False


class _OneResponse:
    """只记下请求、按给定状态码 / 跳转 / 正文回应的 Session（不联网）。"""

    def __init__(self, status=200, location=None, text=""):
        import requests
        self.cookies = requests.cookies.RequestsCookieJar()
        self.cookies.set("CASTGC", "tgc", domain=cas.AUTH_HOST, path="/authserver")
        self.calls = []
        self._status, self._location, self._text = status, location, text

    def get(self, url, **kw):
        import requests
        self.calls.append((url, kw))
        r = requests.Response()
        r.status_code, r.url, r._content = self._status, url, self._text.encode()
        if self._location:
            r.headers["Location"] = self._location
        return r


def test_touch_sso():
    s = _OneResponse(302, cas.JW_SSO_URL + "?ticket=ST-1-abc")
    assert cas.touch_sso(s) is True
    (url, kw), = s.calls
    assert url == f"{cas.AUTH_BASE}/login" and kw["params"] == {"service": cas.JW_SSO_URL}
    assert kw["allow_redirects"] is False  # 只签 ticket，不去兑换
    assert cas.touch_sso(_OneResponse(200, text="<form id=pwdFromId>")) is False  # 回到登录页 = 已失效
    assert cas.touch_sso(_OneResponse(302, f"{cas.AUTH_BASE}/reAuthCheck/reAuthLoginView.do")) is False
    with pytest.raises(cas.CasError):
        cas.touch_sso(_OneResponse(503))
    empty = _OneResponse()
    empty.cookies.clear()
    assert cas.touch_sso(empty) is False and empty.calls == []


def test_jwxt_touch():
    page = "var personId = 1; var currentSemester = {id: 161};"
    s = _OneResponse(200, text=page)
    assert jwxt.touch(s) is True and s.calls[0][1]["allow_redirects"] is False
    assert jwxt.touch(_OneResponse(302, "https://jwxt.nufe.edu.cn/student/login")) is False
    assert jwxt.touch(_OneResponse(200, text="<html>维护中</html>")) is False


# ---------- 人机验证解析 ----------

def _png(extra=b""):
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 20 + b"IEND\xaeB`\x82" + extra


def test_captcha_switch_parsing():
    assert cas.parse_captcha_switch('var captchaSwitch = "2";') == "2"
    assert cas.parse_captcha_switch("var captchaSwitch = '1';") == "1"
    assert cas.parse_captcha_switch("no switch here") == "1"


def test_slider_challenge_parsing():
    key = b"ZQ11W42KhOPNAkYE"
    payload = {"bigImage": base64.b64encode(b"\xff\xd8\xff\xe0 jpeg").decode(),
               "smallImage": base64.b64encode(_png(key)).decode(), "tagWidth": 93, "yHeight": 0}
    c = cas.parse_slider_challenge(payload)
    assert c["key"] == key.decode() and c["big"][1] == "image/jpeg" and c["small"][1] == "image/png"
    # 没有追加密钥 / 不是位图 / 不是 base64：都拒绝
    for bad in ({**payload, "smallImage": base64.b64encode(_png()).decode()},
                {**payload, "bigImage": base64.b64encode(b"<svg onload=alert(1)>").decode()},
                {**payload, "bigImage": "%%%"}, "not a dict"):
        try:
            cas.parse_slider_challenge(bad)
        except cas.CasError:
            pass
        else:
            raise AssertionError(f"should reject {str(bad)[:40]}")


def test_slider_sign_matches_page_plugin():
    """sign = AES-CBC(randomString(64) + JSON.stringify({...}), key)；服务端丢掉前 64 字符即可，IV 不影响。"""
    import json as _json
    key = "ZQ11W42KhOPNAkYE"
    tracks = [{"a": 0, "b": 0, "c": 0}, {"a": 12.5, "b": -1, "c": 40}, {"a": 120, "b": 3, "c": 260}]
    sign = cas.slider_sign(280, 120, tracks, key)
    raw = base64.b64decode(sign)
    dec = Cipher(algorithms.AES(key.encode()), modes.CBC(b"\0" * 16)).decryptor()
    unpad = padding.PKCS7(128).unpadder()
    text = (unpad.update(dec.update(raw) + dec.finalize()) + unpad.finalize()).decode("utf-8", "replace")
    body = text[64:]
    assert body == '{"canvasLength":280,"moveLength":120,"tracks":[{"a":0,"b":0,"c":0},{"a":12.5,"b":-1,"c":40},{"a":120,"b":3,"c":260}]}'
    assert _json.loads(body)["moveLength"] == 120


def test_reauth_method_detection():
    # 只按 reAuthType 判断：学校模板里所有方式的输入框都在
    assert cas.reauth_method("3") == "sms"
    assert cas.reauth_method("10") == "otp"
    assert cas.reauth_method("5") == "今日校园验证码"
    assert cas.reauth_method("11") == "邮箱验证码"
    assert cas.reauth_method("2") == "重新输入密码"
    assert "reAuthType=99" in cas.reauth_method("99")
    assert "reAuthType=?" in cas.reauth_method("")


def test_reauth_alternatives():
    html = """<ul class="changeReAuthTypes"><li id="3" onclick="changeReAuthType(this)">短信</li>
    <li id="10" onclick="changeReAuthType(this)">动态口令</li></ul>"""
    assert cas.reauth_alternatives(html) == {"3", "10"}
    assert cas.reauth_alternatives("<a onclick=\"switchReAuthType('3', f)\">x</a>") == {"3"}
    assert cas.reauth_alternatives('<input id="dynamicCode"><input id="otpCode">') == set()


# ---------- 邮件 ----------

def test_mail_build_and_mask():
    from app import mailer
    msg = mailer.build(mailer.Mail("someone@example.com", "【南财课表】测试 123456", "正文\n第二行"))
    assert msg["To"] == "someone@example.com" and "南财课表" in str(msg["Subject"])
    assert msg["Auto-Submitted"] == "auto-generated" and msg["Message-ID"]
    assert msg.get_content().startswith("正文")
    assert mailer.mask("someone@example.com") == "s***e@example.com"
    assert mailer.mask("ab@x.cn") == "a***@x.cn" and mailer.mask("bad") == "***"


def test_mail_worker_retries_then_delivers(monkeypatch):
    import smtplib
    import time as _time

    from app import mailer
    calls = []

    def flaky(msg):
        calls.append(msg["To"])
        if len(calls) == 1:
            raise smtplib.SMTPServerDisconnected("bye")

    monkeypatch.setattr(mailer, "deliver", flaky)
    monkeypatch.setattr(mailer, "RETRY_DELAYS", (0.05, 0.05))
    monkeypatch.setattr(mailer, "MAIL_MIN_INTERVAL", 0)
    mailer.start()
    try:
        assert mailer.send("a@example.com", "s", "b")
        for _ in range(100):
            if len(calls) >= 2:
                break
            _time.sleep(0.02)
        assert calls == ["a@example.com", "a@example.com"]
    finally:
        mailer.stop()


def test_timetable_diff_lines():
    from app import service
    a = {"courseName": "高数", "weekday": 1, "startUnit": 1, "endUnit": 2, "startTime": "08:00",
         "endTime": "09:35", "room": "A101", "weeksStr": "1-16"}
    added, removed = service.timetable_diff([a], [{**a, "room": "B202"}])
    assert added == ["高数 · 周一 第1–2节 08:00–09:35 · B202 · 第1-16周"]
    assert removed == ["高数 · 周一 第1–2节 08:00–09:35 · A101 · 第1-16周"]
    assert service.timetable_diff([a], [dict(a)]) == ([], [])


# ---------- 桌面小组件数据 ----------

def _widget_view():
    acts = [
        {"courseName": "高数", "weekday": 1, "startUnit": 1, "endUnit": 2, "startTime": "08:20", "endTime": "09:45",
         "weekIndexes": [1, 2], "room": "A101", "teachers": ["甲"]},
        {"courseName": "英语", "weekday": 1, "startUnit": 3, "endUnit": 4, "startTime": "", "endTime": "",
         "weekIndexes": [1], "room": "", "building": "B楼", "teachers": []},
        {"courseName": "线代", "weekday": 2, "startUnit": 3, "endUnit": 4, "startTime": "10:00", "endTime": "11:35",
         "weekIndexes": [1], "room": "C303"},
        {"courseName": "高数", "weekday": 1, "startUnit": 3, "endUnit": 4, "startTime": "10:00", "endTime": "11:35",
         "weekIndexes": [3], "room": "A101"},
    ]
    return {"semester_name": "2026-2027-1", "fetched_at": 1.0, "activities": acts}


def test_widget_payload_today_tomorrow_week():
    import datetime as dt
    from zoneinfo import ZoneInfo

    from app import widget
    now = dt.datetime(2026, 9, 7, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))  # 第 1 周周一 09:00
    p = widget.build(_widget_view(), "2026-09-07", now=now)
    assert p["v"] == 1 and p["today"]["week"] == 1 and p["today"]["weekday"] == 1
    names = [(x["name"], x["start"], x["end"], x["room"]) for x in p["today"]["lessons"]]
    # 英语缺时间：用同节次别的课的时间兜底；缺教室用楼名
    assert names == [("高数", "08:20", "09:45", "A101"), ("英语", "10:00", "11:35", "B楼")]
    assert p["today"]["lessons"][0]["units"] == "1–2" and p["today"]["lessons"][0]["color"].startswith("#")
    assert [x["name"] for x in p["tomorrow"]["lessons"]] == ["线代"]
    assert len(p["week"]["days"]) == 7 and p["week"]["monday"] == "2026-09-07"
    assert p["next_change"] == "2026-09-07T09:45:00+08:00"  # 下一个上下课时刻
    assert p["upcoming"]["date"] == "2026-09-08"  # 明天就有课
    # 周二晚上：下次上课是第 2 周周一（第 1 周周三之后都没课）
    late = dt.datetime(2026, 9, 8, 22, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert widget.build(_widget_view(), "2026-09-07", now=late)["upcoming"]["date"] == "2026-09-14"
    assert p["term"] == {"first_day": "2026-09-07", "last_day": "2026-09-21"}  # 第 3 周周一最后一节
    assert p["next_week"]["week"] == 2 and p["next_week"]["monday"] == "2026-09-14"
    assert [x["name"] for x in p["next_week"]["days"][0]["lessons"]] == ["高数"]


def test_widget_payload_vacation_and_missing_calendar():
    import datetime as dt
    from zoneinfo import ZoneInfo

    from app import widget
    before = dt.datetime(2026, 9, 1, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))  # 开学前
    p = widget.build(_widget_view(), "2026-09-07", now=before)
    assert p["today"]["week"] is None and p["today"]["lessons"] == []
    p = widget.build(_widget_view(), None, now=before)
    assert p["has_calendar"] is False and p["week"]["days"][0]["lessons"] == []
    assert widget.build(None, None, now=before)["today"]["lessons"] == []


def test_widget_colors_match_frontend():
    from app import widget
    acts = [{"courseName": n} for n in ["高等数学", "大学英语", "线性代数", "宏观经济学", "Data Structures", "概率论与数理统计"]]
    # 与 static/js/courses.js 的 buildColorMap 在同样输入下的输出一致
    assert widget.color_map(acts) == {
        "Data Structures": "#6741d9", "大学英语": "#2f9e44", "宏观经济学": "#1971c2",
        "概率论与数理统计": "#74b816", "线性代数": "#ae3ec9", "高等数学": "#e67700"}


# ---------- 动态刷新策略 ----------

def test_refresh_policy_targets():
    from app import refresh_policy as P
    now = 1_000_000_000.0  # 2001-09-09 09:46 北京时间
    H, D, _M = 3600, 86400, 60
    soon_class = now + 5 * D  # 一周内有课，但还远

    def t(**kw):
        return P.target(P.Signals(**{"next_class_at": soon_class, **kw}), now)

    # 活跃度分档
    assert t(last_seen_at=now - H).seconds == 4 * H and t(last_seen_at=now - H).reason == "常用"
    assert t(last_seen_at=now - 10 * D).seconds == 6 * H
    assert t(last_seen_at=now - 30 * D).seconds == 24 * H
    assert t(last_seen_at=now - 90 * D, next_class_at=now + 10 * D).seconds == 7 * D
    # 很久没用但这周有课：至少一天一刷（不再跟着上课时间越刷越密）
    assert t(last_seen_at=now - 90 * D).seconds == D and t(last_seen_at=now - 90 * D).reason == "这周有课"
    # 基础间隔与离上课多远无关：临近上课靠检查点
    assert t(last_seen_at=now - H, next_class_at=now + H).seconds == 4 * H
    # 近期没课（假期 / 开学前 / 结课后）：至少一天一刷
    assert t(last_seen_at=now - H, next_class_at=None).seconds == 24 * H
    assert t(last_seen_at=now - H, next_class_at=now + 20 * D).reason == "近期没有课"
    # 刚调过课：减半
    changed = t(last_seen_at=now - H, last_change_at=now - D)
    assert changed.seconds == 2 * H and "变动" in changed.reason


def test_refresh_policy_checkpoint_before_class():
    from app import refresh_policy as P
    now = 1_000_000_000.0
    H, M = 3600, 60
    cls = now + 2 * H  # 11:46 上课：检查点 11:16，窗口从 11:06 开始
    sig = P.Signals(last_seen_at=now, last_success_at=now - H, next_class_at=cls)
    assert P.overdue(sig, now) < 1
    assert P.next_due(sig, now) == cls - P.CHECK_LEAD - P.CHECK_WINDOW
    assert "出门前" in P.reason(sig, now)
    # 进了窗口：排在基础间隔前面，越接近上课越急
    opens = cls - P.CHECK_LEAD - P.CHECK_WINDOW
    assert P.CHECKPOINT_BOOST <= P.overdue(sig, opens) < P.overdue(sig, opens + 10 * M) < P.REQUESTED_BOOST
    assert P.reason(sig, opens) == "上课前确认"
    # 窗口里刷过一次就够了，直到上课都不再刷
    done = P.Signals(**{**sig.__dict__, "last_success_at": opens + 60})
    assert P.overdue(done, cls - M) < 1
    # 同班同学在窗口里刷过（没发现变动）：也算检查过
    peer = P.Signals(**{**sig.__dict__, "peer_checked_at": opens + 60})
    assert P.overdue(peer, opens + 5 * M) < 1
    stale_peer = P.Signals(**{**sig.__dict__, "peer_checked_at": opens - 60})
    assert P.overdue(stale_peer, opens + 5 * M) >= P.CHECKPOINT_BOOST
    # 很久没用的人不做检查点；订阅了变动邮件的仍然做
    idle = P.Signals(last_seen_at=now - 30 * 86400, last_success_at=now - H, next_class_at=cls)
    assert P.overdue(idle, opens + 5 * M) < 1
    mailed = P.Signals(**{**idle.__dict__, "subscribed": True})
    assert P.overdue(mailed, opens + 5 * M) >= P.CHECKPOINT_BOOST


def test_refresh_policy_quiet_hours():
    import datetime as dt
    from zoneinfo import ZoneInfo

    from app import refresh_policy as P
    tz = ZoneInfo("Asia/Shanghai")
    at = lambda h, m=0: dt.datetime(2026, 10, 27, h, m, tzinfo=tz).timestamp()  # noqa: E731
    # 23:00 刷过，4 小时后本该 3:00 再刷：推迟到 6:30
    sig = P.Signals(last_seen_at=at(1), last_success_at=at(0) - 3600, next_class_at=at(14))
    assert P.overdue(sig, at(3)) < 1 and P.overdue(sig, at(6)) < 1
    assert P.next_due(sig, at(3)) == at(6, 30)
    assert P.overdue(sig, at(6, 31)) >= 1
    # 早课的检查点不受深夜影响：7:00 上课，6:20 起就要确认
    early = P.Signals(last_seen_at=at(1), last_success_at=at(0) - 3600, next_class_at=at(7))
    assert P.overdue(early, at(6, 21)) >= P.CHECKPOINT_BOOST


def test_refresh_policy_overdue_and_requests():
    from app import refresh_policy as P
    now = 1_000_000_000.0
    sig = P.Signals(last_seen_at=now, last_success_at=now - 3600, next_class_at=now + 5 * 86400)
    assert 0.24 < P.overdue(sig, now) < 0.26  # 目标 4 小时，过了 1 小时
    assert P.next_due(sig, now) == now + 3 * 3600
    requested = P.Signals(**{**sig.__dict__, "refresh_requested_at": now - 60})
    assert P.overdue(requested, now) >= P.REQUESTED_BOOST and P.next_due(requested, now) == now
    # 请求早于上次成功：已经处理过了，不再加权
    handled = P.Signals(**{**sig.__dict__, "refresh_requested_at": now - 7200})  # 早于上次成功
    assert P.overdue(handled, now) < 1
    assert P.overdue(P.Signals(), now) >= P.REQUESTED_BOOST  # 从没成功过


def test_refresh_policy_widget_grace():
    from app import refresh_policy as P
    now = 1_000_000_000.0
    H, M = 3600, 60
    # 基础间隔：到期之前不用等；到期后最多等手机 45 分钟
    sig = P.Signals(last_seen_at=now, last_success_at=now - 5 * H, next_class_at=now + 5 * 86400)
    due = sig.last_success_at + 4 * H
    assert P.widget_grace_until(P.Signals(**{**sig.__dict__, "last_success_at": now - H}), now) is None
    assert P.widget_grace_until(sig, now) == due + P.WIDGET_GRACE_BASE
    # 主动请求：请求后 10 分钟
    req = P.Signals(**{**sig.__dict__, "refresh_requested_at": now - M})
    assert P.widget_grace_until(req, now) == now - M + P.WIDGET_GRACE_REQUESTED
    # 检查点：等到检查点前 2 分钟
    cls = now + 35 * M
    cp = P.Signals(last_seen_at=now, last_success_at=now - H, next_class_at=cls)
    assert P.widget_grace_until(cp, now) == cls - P.CHECK_LEAD - P.WIDGET_GRACE_CHECKPOINT_MARGIN
    # 从没成功过：不等
    assert P.widget_grace_until(P.Signals(), now) is None


def test_build_timetable_from_uploaded_parts():
    html = ('<script>var personId = 77; var currentSemester = {"id": 161, "calendarAssoc": {"id": 1}};</script>'
            '<select id="allSemesters"><option value="161">2026-2027-1</option></select>')
    page = jwxt.parse_course_page(html)
    raw = '{"studentTableVms": [{"name": "甲", "code": "1", "activities": [{"courseName": "高数", "weekday": 1, "startUnit": 1}]}]}'
    data = jwxt.build_timetable(page, jwxt.requested_semester(page, None), jwxt.parse_print_data(raw), None)
    assert data["semester_id"] == 161 and data["semester_name"] == "2026-2027-1" and data["person_id"] == 77
    assert data["activities"][0]["courseName"] == "高数"
    for bad in ("[]", "x" * (jwxt.PRINT_DATA_MAX_BYTES + 1)):
        try:
            jwxt.parse_print_data(bad)
        except ValueError:
            continue
        raise AssertionError("bad print-data must be rejected")


def test_scheduler_budget_aimd():
    from app import scheduler
    b = scheduler.Budget(max_rps=0.6, tick=30)
    assert abs(b.refill() - 0.6 * 30 / scheduler.REQUESTS_PER_REFRESH) < 1e-9
    assert b.refill() == 2 * 0.6 * 30 / scheduler.REQUESTS_PER_REFRESH  # 最多攒两个 tick
    b.adjust(penalty=2.0, cooldown=0)
    assert b.rate == 0.3
    b.adjust(penalty=0, cooldown=10)
    assert b.rate == 0.15
    for _ in range(3):
        b.adjust(0, 0)
    assert abs(b.rate - (0.15 + 3 * 0.06)) < 1e-9
    for _ in range(50):
        b.adjust(0, 0)
    assert b.rate == 0.6
    for _ in range(50):
        b.adjust(5, 0)
    assert b.rate == scheduler.MIN_RATE


class _FakeResp:
    def __init__(self, url="", text="", payload=None, status=200):
        self.url, self.text, self._payload, self.status_code = url, text, payload, status

    def json(self):
        if self._payload is None:
            raise ValueError
        return self._payload


class _FakeSession:
    def __init__(self, replies):
        self.replies, self.posts = replies, []

    def post(self, url, data=None, headers=None):
        self.posts.append((url, data))
        return self.replies.pop(0)


_REAUTH_URL = "https://authserver.nufe.edu.cn/authserver/reAuthCheck/reAuthLoginView.do?isMultifactor=true"
_REAUTH_OTP_PAGE = """<script>var reAuthParams = {"reAuthType":"10", "isMultifactor":"true",
  "service":"https:\\/\\/jwxt.nufe.edu.cn\\/sso", "reAuthUserId":"2023001"};</script>
<input id="dynamicCode"><input name="otpCode">
<ul class="changeReAuthTypes"><li id="3" onclick="changeReAuthType(this)">短信</li></ul>"""


def test_reauth_switches_to_sms_when_offered():
    s = _FakeSession([_FakeResp(payload={"code": 1, "data": {"reAuthType": "3"}})])
    flow = cas.LoginFlow(s, "2023001", "pw")
    assert flow._after_login_post(_FakeResp(_REAUTH_URL, _REAUTH_OTP_PAGE)) == "mfa"
    url, data = s.posts[0]
    assert url.endswith("/reAuthCheck/changeReAuthType.do") and data["reAuthType"] == "3"
    assert flow.reauth["method"] == "sms" and flow.reauth["reAuthType"] == "3"


def test_reauth_keeps_otp_and_never_sends_sms_when_switch_refused():
    s = _FakeSession([_FakeResp(payload={"code": 0, "message": "no"})])
    flow = cas.LoginFlow(s, "2023001", "pw")
    assert flow._after_login_post(_FakeResp(_REAUTH_URL, _REAUTH_OTP_PAGE)) == "mfa"
    assert flow.reauth["method"] == "otp"
    try:
        flow.send_sms()
    except cas.CasError:
        pass
    else:
        raise AssertionError("must not request an SMS for a non-SMS reauth session")
    assert len(s.posts) == 1  # 只有切换请求，没有发短信请求


def test_captcha_always_shown_when_bad_credentials_count_is_zero():
    assert cas.captcha_always_shown('var _badCredentialsCount = "0";')
    assert not cas.captcha_always_shown('var _badCredentialsCount = "3";')
    assert not cas.captcha_always_shown("")


def _sms_flow(sleep_account: str):
    page = _REAUTH_OTP_PAGE.replace('"reAuthType":"10"', f'"reAuthType":"3", "isSleepAccount":"{sleep_account}"')
    s = _FakeSession([])
    flow = cas.LoginFlow(s, "2023001", "pw")
    flow._after_login_post(_FakeResp(_REAUTH_URL, page))
    return flow, s


def test_skip_tmp_reauth_only_sent_when_page_offers_trust():
    for sleep_account, expected in (("0", "true"), ("1", None)):
        flow, s = _sms_flow(sleep_account)
        s.replies.append(_FakeResp(payload={"code": "reAuth_failed", "msg": "x"}))
        with contextlib.suppress(cas.CasError):
                flow.verify_sms("123456", trust_device=True)

        data = s.posts[-1][1]
        assert data.get("skipTmpReAuth") == expected
        assert data["dynamicCode"] == "123456" and data["otpCode"] == "" and data["reAuthType"] == "3"


def test_none_auth_type_is_unsupported():
    page = _REAUTH_OTP_PAGE.replace('"reAuthType":"10"', '"reAuthType":"", "noneAuthType":"1"')
    flow = cas.LoginFlow(_FakeSession([]), "2023001", "pw")
    try:
        flow._after_login_post(_FakeResp(_REAUTH_URL, page))  # 不会尝试切换：页面这时不提供切换
    except cas.MfaUnsupported as e:
        assert "无可用" in str(e)
    else:
        raise AssertionError("noneAuthType=1 must be reported as unsupported")


def test_widget_poll_after_follows_due_and_tightens_near_class():
    import datetime as dt

    from app import refresh_policy as rp
    now = dt.datetime(2026, 10, 26, 12, 0, tzinfo=rp._TZ).timestamp()
    seen = {"last_seen_at": now, "last_success_at": now - 60}
    # 离上课还远：到下次该刷时再来，不缩短
    far = rp.Signals(**seen, next_class_at=now + 2 * rp.DAY)
    assert rp.widget_poll_after(far, now) == rp.next_due(far, now) - now > rp.WIDGET_POLL_MAX
    # 3 小时内有课：最多 15 分钟一次
    near = rp.Signals(**seen, next_class_at=now + 2 * rp.HOUR)
    assert rp.widget_poll_after(near, now) == rp.WIDGET_POLL_MAX
    # 检查窗口马上打开：正好那时来
    soon = rp.Signals(**seen, next_class_at=now + 45 * rp.MINUTE)
    assert rp.widget_poll_after(soon, now) == 5 * rp.MINUTE
    # 已经到期：尽快来，但不低于下限
    requested = rp.Signals(**seen, next_class_at=now + 2 * rp.HOUR, refresh_requested_at=now)
    assert rp.widget_poll_after(requested, now) == rp.WIDGET_POLL_MIN


def test_rate_limiter_bounded_and_preserves_state():
    from app import security
    lim = security.RateLimiter(limit=2, window=60, max_keys=3)
    assert lim.hit("k1") and lim.hit("k1") and not lim.hit("k1")
    assert lim.hit("k2") and lim.hit("k3") and lim.hit("k4")
    # 超过 max_keys：淘汰最老的未到限额的 k2；已被限速的 k1 不会被灌入的新键冲掉
    assert len(lim._hits) == 3 and "k2" not in lim._hits and not lim.hit("k1")
    # 热重载时传入旧实例：限流桶原样继承
    reloaded = security.RateLimiter(limit=2, window=60, max_keys=3, prev=lim)
    assert reloaded._hits is lim._hits


def test_presence_bounded_and_preserves_state(monkeypatch):
    from app import presence
    presence._presence.clear()
    presence._visitors.clear()
    monkeypatch.setattr(presence, "MAX_TRACKED", 5)
    for i in range(10):
        presence.mark_user(i + 1, "web")
        presence.mark_visitor(f"10.0.0.{i}")
    assert len(presence._presence) == 5 and len(presence._visitors) == 5
    assert 1 not in presence._presence and 10 in presence._presence


def test_admin_verify_password_caps_iterations():
    from app import admin
    excessive = f"pbkdf2_sha256${admin.MAX_PBKDF2_ITERATIONS + 1}$" + base64.b64encode(b"0" * 16).decode() + "$" + base64.b64encode(b"0" * 32).decode()
    assert not admin.verify_password("anything", excessive)


def test_merge_phone_cookies_filters_bad_chars():
    from app import service
    jar = [
        {"name": "SESSION", "value": "jw-old", "domain": "jwxt.nufe.edu.cn", "path": "/student"},
        {"name": "CASTGC", "value": "tgc-1", "domain": "authserver.nufe.edu.cn", "path": "/authserver"},
    ]
    # 含分号、控制字符或超长的值一律拒绝，合法新值正常更新
    merged = service._merge_phone_cookies(jar, [
        {"name": "SESSION", "value": "bad;injected=1"},
        {"name": "SESSION", "value": "bad\r\ninjected"},
        {"name": "SESSION", "value": "x" * 4097},
    ])
    assert {c["name"]: c["value"] for c in merged}["SESSION"] == "jw-old"
    merged2 = service._merge_phone_cookies(jar, [{"name": "SESSION", "value": "jw-clean"}])
    assert {c["name"]: c["value"] for c in merged2}["SESSION"] == "jw-clean"


def test_ics_fold_fast_path_and_utf8_boundary():
    short = "SUMMARY:高等数学"
    assert ics._fold(short) == short
    long_line = "DESCRIPTION:" + "数" * 40
    folded = ics._fold(long_line)
    for part in folded.split("\r\n"):
        assert len(part.encode("utf-8")) <= 75
    assert folded.replace("\r\n ", "") == long_line


def test_rate_limiter_falls_back_to_oldest_when_all_saturated():
    from app import security
    lim = security.RateLimiter(limit=1, window=60, max_keys=2)
    assert lim.hit("a") and lim.hit("b") and lim.hit("c")
    assert len(lim._hits) == 2 and "a" not in lim._hits


def test_rate_limiter_does_not_evict_partially_attacked_key_on_key_flood():
    from app.security import RateLimiter
    limiter = RateLimiter(limit=5, window=900, max_keys=100)
    for _ in range(4):
        assert limiter.hit("victim_student") is True
    assert len(limiter._hits["victim_student"]) == 4

    for i in range(100):
        limiter.hit(f"dummy_{i}")

    assert "victim_student" in limiter._hits
    assert len(limiter._hits["victim_student"]) == 4

    # 即使攻击者对全部伪造键各发起多次命中填满桶容量，正在被爆破的 victim_student 仍不会被挤出，新伪造键 Fail-Closed
    for i in range(1, 100):
        limiter.hit(f"dummy_{i}")
        limiter.hit(f"dummy_{i}")
    assert limiter.hit("brand_new_noise") is False
    assert "victim_student" in limiter._hits
    assert limiter.hit("victim_student") is True
    assert limiter.hit("victim_student") is False


def test_deploy_script_escapes_ssh_args_and_validates_inputs():
    import subprocess
    from pathlib import Path

    script = Path(__file__).resolve().parent.parent / "deploy" / "deploy.sh"
    res = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True, check=False)
    assert res.returncode == 0, res.stderr
    content = script.read_text("utf-8")
    assert "PUBLIC_BASE_URL='$PUBLIC_BASE_URL'" not in content
    assert "printf '%q '" in content


def test_credential_envelope_encryption_and_direct_password_encrypt_blocked():
    import pytest

    from app import crypto

    assert crypto.encrypt(b"MySecretPassword123", "password", 1001)[:1] == b"\x02"
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt(b"\x01" + b"0" * 32, "password", 1001)

    blob = crypto.encrypt_password(b"MySecretPassword123", 1001, "2023001")
    assert blob[:1] == b"\x02"
    assert crypto.decrypt_password(blob, 1001, "2023001") == b"MySecretPassword123"
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt_password(blob, 1002, "2023001")
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt_password(blob, 1001, "2023002")


def test_dotenv_sync_applies_removals_but_keeps_explicit_env(monkeypatch):
    import os

    from app import config
    monkeypatch.setenv("KB_PROBE_EXPLICIT", "from-env")
    monkeypatch.setattr(config, "_initial_env", {*config._initial_env, "KB_PROBE_EXPLICIT"})
    loaded = config._dotenv_loaded
    try:
        config._sync_dotenv({"KB_PROBE_A": "1", "KB_PROBE_B": "2", "KB_PROBE_EXPLICIT": "from-file", "KB_PROBE_NONE": None})
        assert os.environ["KB_PROBE_A"] == "1" and os.environ["KB_PROBE_EXPLICIT"] == "from-env"
        assert "KB_PROBE_NONE" not in os.environ
        config._sync_dotenv({"KB_PROBE_B": "3"})
        assert "KB_PROBE_A" not in os.environ and os.environ["KB_PROBE_B"] == "3"
    finally:
        os.environ.pop("KB_PROBE_A", None)
        os.environ.pop("KB_PROBE_B", None)
        config._dotenv_loaded = loaded


def test_reload_order_covers_every_app_module():
    from pathlib import Path

    from app import reloader
    names = {p.stem for p in Path(reloader.APP_DIR).glob("*.py")} - {"__init__", "cli", "reloader"}
    assert names == {m.removeprefix("app.") for m in reloader._RELOAD_ORDER}


def test_scheduler_start_fills_in_missing_threads(monkeypatch):
    from app import scheduler
    scheduler.stop()
    monkeypatch.setattr(scheduler, "SCHEDULER_TICK", 0.01)
    monkeypatch.setattr(scheduler.service, "housekeeping", lambda: None)
    monkeypatch.setattr(scheduler, "run_once", lambda: 0)
    try:
        scheduler.start(refresh=False)
        assert set(scheduler.alive()) == {"qr-pump"}
        scheduler.start(refresh=True)
        assert scheduler.alive() == {"qr-pump": True, "refresh-scheduler": True}
        refresh_thread = next(t for t in scheduler._threads if t.name == "refresh-scheduler")
        scheduler.start(refresh=False)  # SCHEDULER_ENABLED 被关掉：下个 tick 自行退出
        refresh_thread.join(timeout=2)
        assert not refresh_thread.is_alive() and set(scheduler.alive()) == {"qr-pump"}
    finally:
        scheduler.stop()


def test_android_signing_uses_env_and_no_workspace_keystore():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    gradle_kts = (root / "android" / "app" / "build.gradle.kts").read_text(encoding="utf-8")
    assert 'System.getenv("ANDROID_KEYSTORE_FILE")' in gradle_kts
    assert 'System.getenv("ANDROID_KEYSTORE_PASSWORD")' in gradle_kts
    assert 'System.getenv("ANDROID_KEY_ALIAS")' in gradle_kts
    assert 'System.getenv("ANDROID_KEY_PASSWORD")' in gradle_kts
    assert not (root / "android" / "keystore.properties").exists()
    assert not (root / "android" / "nufe-kebiao-release.jks").exists()
    assert not (root / "android" / "build" / "nufe-kebiao-signing.zip").exists()


def test_frozen_requirements_pinned_with_sha256_hashes():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    frozen = (root / "deploy" / "requirements-frozen.txt").read_text(encoding="utf-8")
    deploy_sh = (root / "deploy" / "deploy.sh").read_text(encoding="utf-8")
    assert "--require-hashes" in deploy_sh
    assert "deploy/requirements-frozen.txt" in deploy_sh
    pkg_lines = [line for line in frozen.splitlines() if line and not line.startswith("#") and not line.startswith(" ")]
    assert len(pkg_lines) >= 20
    for line in pkg_lines:
        assert "==" in line and ">=" not in line
    assert "--hash=sha256:" in frozen
    assert "pytest==" not in frozen


def test_smtp_refuses_plaintext_auth_when_encryption_is_none(monkeypatch):
    import pytest

    from app import mailer

    monkeypatch.setattr(mailer, "MAIL_USERNAME", "user@example.com")
    monkeypatch.setattr(mailer, "MAIL_PASSWORD", "secret")
    monkeypatch.setattr(mailer, "MAIL_ENCRYPTION", "none")
    msg = mailer.build(mailer.Mail(to="dest@example.com", subject="hi", body="body"))
    with pytest.raises(RuntimeError, match="MAIL_ENCRYPTION=none"):
        mailer._deliver_smtp(msg)


def test_deploy_blocks_root_ssh_by_default():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    example = (root / "deploy" / "deploy.env.example").read_text(encoding="utf-8")
    deploy_sh = (root / "deploy" / "deploy.sh").read_text(encoding="utf-8")
    assert "HOST=deployer@" in example
    assert "HOST=root@" not in example
    assert "ALLOW_ROOT_DEPLOY" in deploy_sh
    assert 'SUDO="sudo -n"' in deploy_sh


def test_pyproject_and_requirements_consistency():
    from pathlib import Path

    import tomllib

    root = Path(__file__).resolve().parent.parent
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    reqs = [line.strip() for line in (root / "requirements.txt").read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]
    assert sorted(pyproject["project"]["dependencies"]) == sorted(reqs)
    assert "httpx>=0.27" in pyproject["dependency-groups"]["dev"]
    assert "httpx2>=0.27" not in pyproject["dependency-groups"]["dev"]
    assert "pytest>=8" not in reqs


def test_gradle_wrapper_has_distribution_sha256_sum():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    props = (root / "android" / "gradle" / "wrapper" / "gradle-wrapper.properties").read_text(encoding="utf-8")
    assert "distributionSha256Sum=945faee80f55dfbfdc39e24021235492d529fba40784405391c0e3ebce25c83b" in props


def test_gradle_verification_metadata_present():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    xml = (root / "android" / "gradle" / "verification-metadata.xml").read_text(encoding="utf-8")
    assert "<verify-metadata>true</verify-metadata>" in xml
    assert '<component group="com.squareup.okhttp3" name="okhttp" version="4.12.0">' in xml


def test_android_boot_receiver_validates_intent_action_allowlist():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    kt = (root / "android" / "app" / "src" / "main" / "java" / "wiki" / "nufe" / "kebiao" / "remind" / "Reminders.kt").read_text(encoding="utf-8")
    assert "if (intent.action !in VALID_ACTIONS) return" in kt
    assert "Intent.ACTION_BOOT_COMPLETED" in kt
    assert '"android.intent.action.QUICKBOOT_POWERON"' in kt


def test_systemd_socket_uses_restricted_unix_domain_socket():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    sock = (root / "deploy" / "kebiao.socket").read_text(encoding="utf-8")
    apache = (root / "deploy" / "apache-kebiao.conf").read_text(encoding="utf-8")
    assert "ListenStream=/run/kebiao/kebiao.sock" in sock
    assert "SocketGroup=www-data" in sock
    assert "SocketMode=0660" in sock
    assert "unix:/run/kebiao/kebiao.sock" in apache
    assert "RequestHeader unset X-Forwarded-For" in apache


def test_apache_configs_use_308_redirects_and_global_security_headers():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    apache = (root / "deploy" / "apache-kebiao.conf").read_text(encoding="utf-8")
    legacy = (root / "deploy" / "apache-kebiao-legacy.conf").read_text(encoding="utf-8")
    assert "R=308" in apache and "R=permanent" not in apache
    assert "RedirectMatch 308" in legacy and "RedirectMatch 301" not in legacy
    assert 'Header always set Strict-Transport-Security "max-age=31536000"' in apache
    assert 'Header always set X-Content-Type-Options "nosniff"' in apache
    assert "Header always set Content-Security-Policy" in apache


def test_cas_dump_redacts_ticket_and_workspace_debug_cleaned(tmp_path):
    from pathlib import Path
    from types import SimpleNamespace

    from app import cas
    from app.polite import PoliteSession

    root = Path(__file__).resolve().parent.parent
    assert not (root / "debug").exists()
    assert not (root / "my.json").exists()
    assert not (root / ".nufe_device_id").exists()

    flow = cas.LoginFlow(PoliteSession(), "2023001", "pw", debug_dir=tmp_path / "dbg")
    fake_hist = SimpleNamespace(
        status_code=302,
        url="https://jwxt.nufe.edu.cn/student/sso/login?ticket=ST-123456-secret-ticket",
        raw=SimpleNamespace(headers=SimpleNamespace(getlist=lambda _k: [])),
    )
    fake_resp = SimpleNamespace(
        request=SimpleNamespace(method="GET"),
        url="https://jwxt.nufe.edu.cn/student/for-std/course-table?ticket=ST-999999-secret",
        status_code=200,
        headers={"content-type": "text/html"},
        text="<html>ok</html>",
        content=b"<html>ok</html>",
        history=[fake_hist],
        raw=SimpleNamespace(headers=SimpleNamespace(getlist=lambda _k: [])),
    )
    flow._dump("test", fake_resp)
    dumped = (tmp_path / "dbg" / "01-test.txt").read_text(encoding="utf-8")
    assert "ST-123456-secret-ticket" not in dumped
    assert "ST-999999-secret" not in dumped
    assert "ticket=<REDACTED>" in dumped












