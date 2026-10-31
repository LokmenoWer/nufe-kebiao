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


def test_renew_with_tgc_needs_castgc():
    import requests
    # 没有 CASTGC 时直接返回 False，不发任何请求
    assert cas.renew_with_tgc(requests.Session()) is False


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
    assert cas.reauth_method('<input id="dynamicCode">', "3") == "sms"
    assert cas.reauth_method('<input id="otpCode" name="otpCode">', "5") == "otp"
    assert cas.reauth_method('<input name="answer1"><input name="answer2">', "6") == "密保问题"
    assert cas.reauth_method("请输入邮箱验证码", "4") == "邮箱验证码"
    assert "reAuthType=9" in cas.reauth_method("<p>?</p>", "9")


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
