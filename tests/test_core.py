import base64

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
    assert all(len(l.encode()) <= 75 for l in out.split("\r\n"))


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
    import threading, time
    from app import polite
    polite._last_request_at = 0.0
    stamps, lock = [], threading.Lock()

    def worker():
        polite._pace()
        with lock:
            stamps.append(time.monotonic())

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    stamps.sort()
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert len(gaps) == 2 and all(g >= 1.0 for g in gaps), gaps
