"""Web 层端到端测试：上游（CAS/教务）全部打桩，不访问学校服务器。"""
import pytest
from fastapi.testclient import TestClient

from app import cas, db, jwxt, scheduler, service
from app.main import app

ORIGIN = {"Origin": "http://testserver"}
PASSWORDS = {"2023001": "pw-a", "2023002": "pw-b"}


def _timetable(student_id: str, course: str) -> dict:
    return {
        "person_id": int(student_id), "semester_id": 161, "semester_name": "2026-2027-1",
        "current_semester_id": 161, "semesters": [{"id": 161, "name": "2026-2027-1"}],
        "semester_detail": None,
        "student": {"name": f"学生{student_id[-1]}", "code": student_id},
        "activities": [{"courseName": course, "teachers": ["T"], "weekday": 1, "startUnit": 1, "endUnit": 2,
                        "weeksStr": "1~16", "weekIndexes": list(range(1, 17)), "room": "A101", "building": "",
                        "campus": "", "startTime": "8:20", "endTime": "9:45", "credits": 2}],
    }


class FakeUpstream:
    def __init__(self):
        self.logins = []
        self.expired = set()      # 这些学号的 cookie 已失效
        self.captcha = set()      # 后台登录时要求验证码
        self.mfa = set()          # 后台登录时要求短信
        self.flow_captcha = set() # 交互登录时要求验证码
        self.flow_mfa = set()     # 交互登录时要求短信
        self.flow_slider = set()  # 交互登录时要求滑块拼图
        self.flow_otp = set()     # 二次认证方式是动态口令
        self.flow_mfa_unsupported = set()  # 二次认证方式本站转发不了
        self.slider_submissions = []
        self.sms_sent = 0
        self.devices = []
        self.qr = "waiting"       # waiting | scanned | expired | <学号>（= 该学号已在手机上确认）
        self.qr_mfa = False
        self.tgc_alive = set()    # 教务会话过期但 CASTGC 仍有效：可以静默续期
        self.renewals = 0
        self.fetches = 0
        self.course_override = None
        self.room_override = None
        self.extra_activities = {}  # 学号 -> 额外排课（补课 / 加课）

    def login(self, session, username, password, device_id=None):
        self.devices.append(device_id)
        if username in self.captcha:
            raise cas.CaptchaRequired("需要验证码")
        if username in self.mfa:
            raise cas.MfaRequired("需要短信")
        if PASSWORDS.get(username) != password:
            raise cas.CasError("您提供的用户名或者密码有误")
        self.logins.append(username)
        session.cookies.set("CASTGC", f"tgc-{username}", domain="authserver.nufe.edu.cn", path="/authserver")
        self.expired.discard(username)

    def renew(self, session):
        tgc = session.cookies.get("CASTGC", domain="authserver.nufe.edu.cn", path="/authserver")
        sid = tgc.removeprefix("tgc-") if tgc else None
        if not sid or sid not in self.tgc_alive:
            return False
        self.renewals += 1
        self.expired.discard(sid)
        return True

    def fetch(self, session, semester_id=None, page_html=None):
        self.fetches += 1
        if self.course_override:
            tgc = session.cookies.get("CASTGC", domain="authserver.nufe.edu.cn", path="/authserver")
            if tgc and tgc.removeprefix("tgc-") not in self.expired:
                data = _timetable(tgc.removeprefix("tgc-"), self.course_override)
                data["activities"][0]["room"] = self.room_override or "A101"
                data["activities"] += self.extra_activities.get(tgc.removeprefix("tgc-"), [])
                return data
        tgc = session.cookies.get("CASTGC", domain="authserver.nufe.edu.cn", path="/authserver")
        sid = tgc.removeprefix("tgc-") if tgc else None
        if not sid or sid in self.expired:
            raise jwxt.SessionExpired()
        return _timetable(sid, "高数" if sid == "2023001" else "英语")


class FakeFlow:
    """模拟 cas.LoginFlow 的状态机。"""
    up: "FakeUpstream"

    def __init__(self, session, username, password, device_id="DEV", debug_dir=None):
        self.session, self.username, self.password = session, username, password
        self.device_id = device_id
        self.up.devices.append(device_id)
        self.stage = "init"
        self.reauth = {"phone": "138****0000"}

    def wipe_password(self):
        self.password = ""

    def start(self):
        if self.username in self.up.flow_captcha:
            self.stage = "captcha"
            return self.stage
        if self.username in self.up.flow_slider:
            self.stage = "slider"
            return self.stage
        return self.submit("")

    def slider_challenge(self):
        self.slider_key = "K" * 16
        return {"big": (b"\xff\xd8\xff big", "image/jpeg"), "small": (b"\x89PNG small", "image/png")}

    def submit_slider(self, canvas_length, move_length, tracks):
        self.up.slider_submissions.append((canvas_length, move_length, tracks))
        if not 100 <= move_length <= 110:
            raise cas.CasError("拼图没有对齐，请再试一次")
        self.up.flow_slider.discard(self.username)
        return self.submit("")

    def captcha_image(self):
        return b"\x89PNG fake", "image/png"

    def start_qr(self):
        self.stage, self.qr_status = "qr", "waiting"
        return self.stage

    def qr_image(self):
        return b"\x89PNG qr", "image/png"

    def refresh_qr(self):
        self.up.qr = "waiting"
        self.qr_status = "waiting"

    def poll_qr(self):
        q = self.up.qr
        if q in ("waiting", "scanned", "expired"):
            self.qr_status = q
            return self.stage
        self.username = ""  # 扫码前不知道学号
        self._qr_user = q
        if self.up.qr_mfa:
            self.stage = "mfa"
            return self.stage
        return self._done()

    def submit(self, captcha):
        if self.username in self.up.flow_captcha and captcha != "abcd":
            self.stage = "captcha"
            raise cas.CasError("验证码错误")
        if PASSWORDS.get(self.username) != self.password:
            self.stage = "init"
            raise cas.CasError("您提供的用户名或者密码有误")
        if self.username in self.up.flow_mfa_unsupported:
            self.stage = "failed"
            raise cas.MfaUnsupported("学校要求用「密保问题」完成二次认证，本站暂不能转发")
        if self.username in self.up.flow_mfa:
            self.reauth["method"] = "otp" if self.username in self.up.flow_otp else "sms"
            self.stage = "mfa"
            return self.stage
        return self._done()

    def send_sms(self):
        self.up.sms_sent += 1
        import time
        self.sms_ready_at = time.time() + 60
        return "验证码已发送"

    def verify_sms(self, code, trust_device=True):
        if code != "123456":
            raise cas.CasError("动态码错误")
        return self._done()

    def _done(self):
        who = self.username or getattr(self, "_qr_user", "")
        self.up.logins.append(who)
        self.session.cookies.set("CASTGC", f"tgc-{who}", domain="authserver.nufe.edu.cn", path="/authserver")
        self.up.expired.discard(who)
        self.stage = "done"
        self.wipe_password()
        return self.stage


@pytest.fixture()
def up(monkeypatch):
    db.init()
    with db.tx() as c:
        c.execute("DELETE FROM users")
    service._locks.clear()
    service._pending.clear()
    service._completed.clear()
    service._ics_cache.clear()
    from app import security
    security.login_by_ip._hits.clear()
    security.login_by_student._hits.clear()
    fake = FakeUpstream()
    monkeypatch.setattr(cas, "login", fake.login)
    FakeFlow.up = fake
    monkeypatch.setattr(cas, "LoginFlow", FakeFlow)
    monkeypatch.setattr(jwxt, "fetch_timetable", fake.fetch)
    monkeypatch.setattr(cas, "renew_with_tgc", fake.renew)
    # 手动刷新的冷却 / 新鲜度闸门单独测；其余用例里连续刷新不应被它挡住
    monkeypatch.setattr(service, "MANUAL_REFRESH_MIN_GAP", 0)
    monkeypatch.setattr(service, "MANUAL_REFRESH_FRESH_SECONDS", 0)
    from app import polite
    polite.reset_health()
    from app import notify
    notify.mails_by_user._hits.clear()
    notify.mails_by_ip._hits.clear()
    return fake


@pytest.fixture
def outbox(monkeypatch):
    from app import mailer
    sent = []
    monkeypatch.setattr(mailer, "send", lambda to, subject, body: sent.append((to, subject, body)) or True)
    return sent


def _login(client, sid, remember=False, pw=None, keep=False):
    r = client.post("/api/login", headers=ORIGIN, json={
        "student_id": sid, "password": pw or PASSWORDS[sid], "remember_password": remember, "agree_terms": True,
        "keep_login": keep})
    return r


def _csrf(client):
    return {**ORIGIN, "X-CSRF-Token": client.get("/api/me").json()["csrf"]}


def service_uid(student_id):
    return db.connect().execute("SELECT id FROM users WHERE student_id=?", (student_id,)).fetchone()["id"]


def test_login_and_isolation(up):
    a, b = TestClient(app), TestClient(app)
    assert _login(a, "2023001").status_code == 200
    assert _login(b, "2023002").status_code == 200
    assert a.get("/api/timetable").json()["activities"][0]["courseName"] == "高数"
    assert b.get("/api/timetable").json()["activities"][0]["courseName"] == "英语"
    assert a.get("/api/me").json()["student_id"] == "2023001"
    # 校历来自 semesters.json
    assert a.get("/api/timetable").json()["week1_monday"] == "2026-09-07"


def test_wrong_password_and_terms(up):
    c = TestClient(app)
    r = _login(c, "2023001", pw="bad")
    assert r.status_code == 400 and "密码有误" in r.json()["detail"]
    r = c.post("/api/login", headers=ORIGIN, json={"student_id": "2023001", "password": "pw-a", "agree_terms": False})
    assert r.status_code == 400


def test_loopback_preview_origin(monkeypatch):
    from urllib.parse import urlparse

    from app import security
    monkeypatch.setattr(security, "_ALLOWED_ORIGIN", urlparse("http://localhost:8000"))
    assert security.origin_matches("http://127.0.0.1:8765")
    assert security.origin_matches("http://localhost:8000")
    assert not security.origin_matches("https://evil.example")
    monkeypatch.setattr(security, "_ALLOWED_ORIGIN", urlparse("https://kebiao.example"))
    assert security.origin_matches("https://kebiao.example")
    assert not security.origin_matches("http://127.0.0.1:8765")


def test_csrf_and_origin(up):
    c = TestClient(app)
    _login(c, "2023001")
    assert c.post("/api/refresh", headers=ORIGIN, json={}).status_code == 403          # 无 CSRF token
    assert c.post("/api/refresh", headers={"Origin": "https://evil.example"}, json={}).status_code == 403
    assert c.post("/api/login", json={}).status_code == 403                            # 无 Origin
    assert c.post("/api/refresh", headers=_csrf(c), json={}).json()["ok"] is True


def test_no_plaintext_password_in_db(up):
    c = TestClient(app)
    _login(c, "2023001", remember=True)
    from pathlib import Path
    raw = Path(db.DB_PATH).read_bytes()
    for path in Path(db.DB_PATH).parent.glob(Path(db.DB_PATH).name + "*"):
        raw += path.read_bytes()
    assert b"pw-a" not in raw and b"tgc-2023001" not in raw


def test_cookie_mode_expiry_needs_rebind(up):
    c = TestClient(app)
    _login(c, "2023001")
    up.expired.add("2023001")
    r = c.post("/api/refresh", headers=_csrf(c), json={}).json()
    assert r["ok"] is False and r["status"] == "needs_rebind"
    me = c.get("/api/me").json()
    assert me["status"] == "needs_rebind" and me["next_refresh_at"] is None
    # 重新登录即恢复
    _login(c, "2023001")
    assert c.get("/api/me").json()["status"] == "ok"


# ---------- 人机验证转发 ----------

def _slider_drag(move=105):
    return {"canvas_length": 280, "move_length": move,
            "tracks": [{"a": 0, "b": 0, "c": 0}, {"a": 40, "b": 1, "c": 120}, {"a": move, "b": 2, "c": 300}]}


def test_slider_captcha_forwarded_and_completes(up):
    up.flow_slider.add("2023001")
    c = TestClient(app)
    r = _login(c, "2023001").json()
    assert r["ok"] is False and r["need"] == "slider"
    sl = r["slider"]
    assert sl["width"] == 280 and sl["height"] == 155
    assert sl["background"].startswith("data:image/jpeg;base64,") and sl["piece"].startswith("data:image/png;base64,")
    assert "key" not in sl and "K" * 16 not in str(r)  # 签名密钥只留在服务端
    # 拼歪了：留在滑块步骤，换一张新图
    bad = c.post("/api/login/slider", headers=ORIGIN, json={"flow_id": r["flow_id"], **_slider_drag(30)}).json()
    assert bad["need"] == "slider" and "对齐" in bad["error"] and bad["slider"]["piece"]
    ok = c.post("/api/login/slider", headers=ORIGIN, json={"flow_id": r["flow_id"], **_slider_drag(105)}).json()
    assert ok["ok"] is True
    # 用户的拖动原样转交：整数保持整数
    canvas, move, tracks = up.slider_submissions[-1]
    assert canvas == 280 and move == 105 and isinstance(move, int) and tracks[-1] == {"a": 105, "b": 2, "c": 300}


def test_slider_refresh_and_validation(up):
    up.flow_slider.add("2023001")
    c = TestClient(app)
    fid = _login(c, "2023001").json()["flow_id"]
    assert c.post("/api/login/slider/refresh", headers=ORIGIN, json={"flow_id": fid}).json()["slider"]["background"]
    # 不合理的输入直接 422，不转给学校
    for bad in ({**_slider_drag(), "canvas_length": 20}, {**_slider_drag(), "tracks": []},
                {**_slider_drag(), "move_length": -5}):
        assert c.post("/api/login/slider", headers=ORIGIN, json={"flow_id": fid, **bad}).status_code == 422
    assert up.slider_submissions == []
    # 不在滑块步骤时拒绝
    assert c.post("/api/login/slider/refresh", headers=ORIGIN, json={"flow_id": "nope"}).status_code == 400


def test_slider_refresh_limit(up):
    up.flow_slider.add("2023001")
    c = TestClient(app)
    fid = _login(c, "2023001").json()["flow_id"]
    codes = [c.post("/api/login/slider/refresh", headers=ORIGIN, json={"flow_id": fid}).status_code
             for _ in range(service.MAX_CAPTCHA_IMAGES + 1)]
    assert codes[-1] == 400 and 200 in codes


def test_otp_mfa_method_is_reported(up):
    up.flow_mfa.add("2023001")
    up.flow_otp.add("2023001")
    c = TestClient(app)
    r = _login(c, "2023001").json()
    assert r["need"] == "mfa" and r["method"] == "otp"
    ok = c.post("/api/login/sms/verify", headers=ORIGIN, json={"flow_id": r["flow_id"], "code": "123456"}).json()
    assert ok["ok"] is True


def test_unsupported_mfa_fails_with_clear_message(up):
    up.flow_mfa_unsupported.add("2023001")
    r = _login(TestClient(app), "2023001")
    assert r.status_code == 400 and "密保问题" in r.json()["detail"]
    assert not service._pending  # 流程已释放，不留密码在内存里


def test_cookie_mode_expiry_renews_via_castgc(up):
    """教务会话过期但 CASTGC 还有效：静默续期，不要求用户重新登录，也不动用密码。"""
    c = TestClient(app)
    _login(c, "2023001")
    up.expired.add("2023001")
    up.tgc_alive.add("2023001")
    logins_before = len(up.logins)
    r = c.post("/api/refresh", headers=_csrf(c), json={}).json()
    assert r["ok"] is True and up.renewals == 1 and len(up.logins) == logins_before
    assert c.get("/api/me").json()["status"] == "ok"


def test_password_mode_prefers_castgc_over_password(up):
    c = TestClient(app)
    _login(c, "2023001", remember=True)
    up.expired.add("2023001")
    up.tgc_alive.add("2023001")
    logins_before = len(up.logins)
    with scheduler.background_upstream():
        assert service.refresh_user(service_uid("2023001"), "scheduled")["ok"] is True
    assert up.renewals == 1 and len(up.logins) == logins_before


def test_sso_renewal_outage_is_retryable_not_rebind(up, monkeypatch):
    c = TestClient(app)
    _login(c, "2023001")
    up.expired.add("2023001")

    def broken(_session):
        raise cas.CasError("统一身份认证服务暂时不可用")

    monkeypatch.setattr(cas, "renew_with_tgc", broken)
    r = c.post("/api/refresh", headers=_csrf(c), json={}).json()
    assert r["ok"] is False and r["status"] == "error"
    me = c.get("/api/me").json()
    assert me["status"] == "error" and me["next_refresh_at"] is not None


def test_manual_refresh_min_gap(up, monkeypatch):
    c = TestClient(app)
    _login(c, "2023001")
    up.expired.add("2023001")
    assert c.post("/api/refresh", headers=_csrf(c), json={}).json()["status"] == "needs_rebind"
    # 状态不正常时不会走缓存；紧接着再点就要等
    monkeypatch.setattr(service, "MANUAL_REFRESH_MIN_GAP", 30)
    r = c.post("/api/refresh", headers=_csrf(c), json={})
    assert r.status_code == 429 and 1 <= int(r.headers["Retry-After"]) <= 30
    assert "秒后再试" in r.json()["detail"]


def test_manual_refresh_serves_fresh_cache(up, monkeypatch):
    c = TestClient(app)
    _login(c, "2023001")
    monkeypatch.setattr(service, "MANUAL_REFRESH_FRESH_SECONDS", 120)
    fetches = up.fetches
    r = c.post("/api/refresh", headers=_csrf(c), json={}).json()
    assert r["ok"] is True and r["cached"] is True and r["timetable"]["semester_id"] == 161
    assert up.fetches == fetches  # 没有打扰学校
    # 状态不正常时不走缓存：用户点刷新就是想恢复
    up.expired.add("2023001")
    with db.tx() as conn:
        conn.execute("UPDATE users SET status='error'")
    assert c.post("/api/refresh", headers=_csrf(c), json={}).json()["status"] == "needs_rebind"


def test_refresh_right_after_login_is_cached_not_too_soon(up, monkeypatch):
    c = TestClient(app)
    _login(c, "2023001")
    monkeypatch.setattr(service, "MANUAL_REFRESH_MIN_GAP", 30)
    monkeypatch.setattr(service, "MANUAL_REFRESH_FRESH_SECONDS", 120)
    r = c.post("/api/refresh", headers=_csrf(c), json={})
    assert r.status_code == 200 and r.json()["cached"] is True


def test_upstream_cooldown_during_manual_refresh(up, monkeypatch):
    from app import polite
    c = TestClient(app)
    _login(c, "2023001")

    def cooling(*_a, **_k):
        raise polite.UpstreamCooldown(42)

    monkeypatch.setattr(jwxt, "fetch_timetable", cooling)
    r = c.post("/api/refresh", headers=_csrf(c), json={}).json()
    assert r["ok"] is False and r["status"] == "cooldown" and r["retry_after"] == 42
    me = c.get("/api/me").json()
    assert me["status"] == "ok" and me["fail_count"] == 0  # 不算这个用户的失败


def test_login_upstream_error_is_503(up, monkeypatch):
    from app import polite

    def cooling(*_a, **_k):
        raise polite.UpstreamCooldown(15)

    monkeypatch.setattr(service, "login_start", cooling)
    r = _login(TestClient(app), "2023001")
    assert r.status_code == 503 and r.headers["Retry-After"] == "15"
    assert "学校服务器" in r.json()["detail"]


def _session_row(uid):
    return db.connect().execute(
        "SELECT expires_at, persistent FROM sessions WHERE user_id=? ORDER BY created_at DESC", (uid,)).fetchone()


def test_session_sliding_renewal_keep_login(up):
    from app import config
    c = TestClient(app)
    _login(c, "2023001", keep=True)
    uid = service_uid("2023001")
    assert _session_row(uid)["persistent"] == 1
    # 一天之内重复打开页面：不续期、不重发 cookie
    r = c.get("/api/me")
    assert "kb_session" not in r.headers.get("set-cookie", "")
    # 快到期了（只剩 3 天）：打开页面就续满，并重发带 max-age 的 cookie
    with db.tx() as conn:
        conn.execute("UPDATE sessions SET expires_at=? WHERE user_id=?", (db.now() + 3 * 86400, uid))
    r = c.get("/api/me")
    assert r.status_code == 200
    cookie = r.headers.get("set-cookie", "")
    assert "kb_session=" in cookie and f"Max-Age={config.SESSION_DAYS_LONG * 86400}" in cookie
    left = _session_row(uid)["expires_at"] - db.now()
    assert left > (config.SESSION_DAYS_LONG - 1) * 86400


def test_session_sliding_renewal_browser_session(up):
    from app import config
    c = TestClient(app)
    _login(c, "2023001")
    uid = service_uid("2023001")
    assert _session_row(uid)["persistent"] == 0
    with db.tx() as conn:
        conn.execute("UPDATE sessions SET expires_at=? WHERE user_id=?", (db.now() + 3600, uid))
    r = c.get("/api/me")
    assert r.status_code == 200
    assert "kb_session" not in r.headers.get("set-cookie", "")  # 仍是关浏览器即失效的会话 cookie
    left = _session_row(uid)["expires_at"] - db.now()
    assert left > (config.SESSION_DAYS_SHORT - 1) * 86400


def test_sessions_migration_infers_persistent(up):
    from app import config
    conn = db.connect()
    conn.execute("DELETE FROM sessions")
    conn.execute("ALTER TABLE sessions DROP COLUMN persistent")
    try:
        uid = conn.execute("INSERT INTO users(student_id, created_at) VALUES('2029999', ?)", (db.now(),)).lastrowid
        now = db.now()
        for tag, days in (("long", config.SESSION_DAYS_LONG), ("short", config.SESSION_DAYS_SHORT)):
            conn.execute("INSERT INTO sessions(token_hash, user_id, csrf, created_at, expires_at) VALUES(?,?,?,?,?)",
                         (tag, uid, "x", now, now + days * 86400))
    finally:
        db.init()
    rows = dict(conn.execute("SELECT token_hash, persistent FROM sessions").fetchall())
    assert rows == {"long": 1, "short": 0}


def test_password_mode_auto_relogin(up):
    c = TestClient(app)
    _login(c, "2023001", remember=True)
    up.expired.add("2023001")
    assert c.post("/api/refresh", headers=_csrf(c), json={}).json()["ok"] is True
    assert up.logins == ["2023001", "2023001"]
    # cookie 仍有效时不应再次用密码登录（清掉冷却，这一条测的是凭据策略）
    c.post("/api/refresh", headers=_csrf(c), json={})
    assert up.logins == ["2023001", "2023001"]


def test_captcha_stops_retry(up):
    c = TestClient(app)
    _login(c, "2023001", remember=True)
    up.expired.add("2023001")
    up.captcha.add("2023001")
    r = c.post("/api/refresh", headers=_csrf(c), json={}).json()
    assert r["status"] == "captcha"
    assert _queued(far_future=True) == []


def _queued(far_future=False):
    now = db.now() + (60 * 86400 if far_future else 0)
    return [uid for _score, uid in service.candidates(now)]


def _queued_at(at):
    return [uid for _score, uid in service.candidates(at)]


def _budget(tokens=10):
    b = scheduler.Budget(max_rps=0.65, tick=30)
    b.tokens = tokens
    b.refill = lambda: b.tokens
    return b


def test_scheduler_refreshes_overdue_users(up, monkeypatch):
    c = TestClient(app)
    _login(c, "2023001")
    before = c.get("/api/me").json()["last_refresh_at"]
    assert _queued() == []  # 刚登录，数据新鲜
    with db.tx() as conn:
        conn.execute("UPDATE users SET last_success_at=last_success_at-30*86400")
    monkeypatch.setattr(scheduler._stop, "wait", lambda t: False)
    assert scheduler.run_once(_budget()) == 1
    me = c.get("/api/me").json()
    assert me["last_refresh_at"] > before and me["next_refresh_at"] > me["last_refresh_at"]
    assert c.get("/api/refresh-log").json()[0]["trigger"] == "scheduled"


def test_ics_and_delete(up):
    c = TestClient(app)
    _login(c, "2023001")
    url = c.post("/api/ics/rotate", headers=_csrf(c)).json()["ics_url"]
    path = url.removeprefix("http://testserver")
    feed = TestClient(app).get(path)
    assert feed.status_code == 200 and feed.text.count("BEGIN:VEVENT") == 16
    assert TestClient(app).get("/ics/wrong.ics").status_code == 404

    assert c.post("/api/account/delete", headers=_csrf(c)).json()["ok"]
    assert TestClient(app).get(path).status_code == 404
    assert c.get("/api/me").status_code == 401
    conn = db.connect()
    for t in ("users", "sessions", "timetables", "refresh_log"):
        assert conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0
    conn.close()


def test_captcha_forwarded_to_user(up):
    up.flow_captcha.add("2023001")
    c = TestClient(app)
    r = _login(c, "2023001").json()
    assert r["ok"] is False and r["need"] == "captcha" and r["captcha_image"].startswith("data:image/png;base64,")
    assert c.get("/api/me").status_code == 401
    bad = c.post("/api/login/captcha", headers=ORIGIN, json={"flow_id": r["flow_id"], "captcha": "zzzz"}).json()
    assert bad["need"] == "captcha" and bad["error"] == "验证码错误"
    ok = c.post("/api/login/captcha", headers=ORIGIN, json={"flow_id": r["flow_id"], "captcha": "abcd"}).json()
    assert ok["ok"] is True
    assert c.get("/api/me").json()["student_id"] == "2023001"
    assert service._pending == {}  # 完成后内存里不再持有流程（及密码）


def test_sms_mfa_forwarded_to_user(up):
    up.flow_mfa.add("2023002")
    c = TestClient(app)
    r = _login(c, "2023002", remember=True).json()
    assert r["need"] == "mfa" and r["phone"] == "138****0000"
    fid = r["flow_id"]
    assert c.post("/api/login/sms/send", headers=ORIGIN, json={"flow_id": fid}).json()["message"] == "验证码已发送"
    bad = c.post("/api/login/sms/verify", headers=ORIGIN, json={"flow_id": fid, "code": "000000"}).json()
    assert bad["need"] == "mfa" and "错误" in bad["error"]
    ok = c.post("/api/login/sms/verify", headers=ORIGIN, json={"flow_id": fid, "code": "123456"}).json()
    assert ok["ok"] is True
    me = c.get("/api/me").json()
    assert me["student_id"] == "2023002" and me["cred_mode"] == "password"
    # 流程已结束，旧 flow_id 不能再用
    assert c.post("/api/login/sms/send", headers=ORIGIN, json={"flow_id": fid}).status_code == 400


def test_sms_limits(up):
    up.flow_mfa.add("2023001")
    c = TestClient(app)
    fid = _login(c, "2023001").json()["flow_id"]
    for _ in range(service.MAX_SMS_SENDS):
        assert c.post("/api/login/sms/send", headers=ORIGIN, json={"flow_id": fid}).status_code == 200
    assert c.post("/api/login/sms/send", headers=ORIGIN, json={"flow_id": fid}).status_code == 400
    for _ in range(service.MAX_VERIFY_ATTEMPTS):
        c.post("/api/login/sms/verify", headers=ORIGIN, json={"flow_id": fid, "code": "000000"})
    r = c.post("/api/login/sms/verify", headers=ORIGIN, json={"flow_id": fid, "code": "123456"})
    assert r.status_code == 400 and "过多" in r.json()["detail"]


def test_background_mfa_stops_retry(up):
    c = TestClient(app)
    _login(c, "2023001", remember=True)
    up.expired.add("2023001")
    up.mfa.add("2023001")
    r = c.post("/api/refresh", headers=_csrf(c), json={}).json()
    assert r["status"] == "needs_rebind" and "短信" in r["message"]
    assert _queued(far_future=True) == []


def test_device_id_is_stable_per_user(up):
    c = TestClient(app)
    _login(c, "2023001", remember=True)
    _login(c, "2023001", remember=True)
    up.expired.add("2023001")
    c.post("/api/refresh", headers=_csrf(c), json={})
    assert len(up.devices) == 3 and len(set(up.devices)) == 1  # 两次站点登录 + 一次后台重登，同一设备
    _login(TestClient(app), "2023002")
    assert up.devices[-1] != up.devices[0]


def _qr_start(c, keep=False):
    return c.post("/api/login/qr/start", headers=ORIGIN, json={"agree_terms": True, "keep_login": keep}).json()


def _qr_poll(c, fid):
    """先让调度侧向上游问一次，再读内存里的状态。"""
    service.pump_qr_once()
    return c.post("/api/login/qr/poll", headers=ORIGIN, json={"flow_id": fid})


def test_qr_login(up):
    c = TestClient(app)
    r = _qr_start(c)
    assert r["need"] == "qr" and r["qr_status"] == "waiting" and r["qr_image"].startswith("data:image/png;base64,")
    fid = r["flow_id"]
    def poll():
        return _qr_poll(c, fid).json()
    assert poll()["qr_status"] == "waiting"
    up.qr = "scanned"
    assert poll()["qr_status"] == "scanned"
    up.qr = "2023002"
    assert poll()["ok"] is True
    assert c.get("/api/me").json()["student_id"] == "2023002"  # 学号取自教务数据
    assert service._pending == {}


def test_qr_login_keeps_saved_password(up):
    c = TestClient(app)
    _login(c, "2023001", remember=True)
    c2 = TestClient(app)
    fid = _qr_start(c2)["flow_id"]
    up.qr = "2023001"
    assert _qr_poll(c2, fid).json()["ok"]
    assert c2.get("/api/me").json()["cred_mode"] == "password"


def test_qr_login_with_mfa_and_expiry(up):
    c = TestClient(app)
    fid = _qr_start(c)["flow_id"]
    up.qr = "expired"
    assert _qr_poll(c, fid).json()["qr_status"] == "expired"
    r = c.post("/api/login/qr/refresh", headers=ORIGIN, json={"flow_id": fid}).json()
    assert r["qr_status"] == "waiting" and r["qr_image"]
    up.qr, up.qr_mfa = "2023001", True
    assert _qr_poll(c, fid).json()["need"] == "mfa"
    ok = c.post("/api/login/sms/verify", headers=ORIGIN, json={"flow_id": fid, "code": "123456"}).json()
    assert ok["ok"] and c.get("/api/me").json()["student_id"] == "2023001"


def test_qr_poll_limit(up):
    c = TestClient(app)
    fid = _qr_start(c)["flow_id"]
    for _ in range(service.MAX_QR_POLLS):
        c.post("/api/login/qr/poll", headers=ORIGIN, json={"flow_id": fid})
    assert c.post("/api/login/qr/poll", headers=ORIGIN, json={"flow_id": fid}).json()["qr_status"] == "expired"


def test_qr_requires_terms(up):
    r = TestClient(app).post("/api/login/qr/start", headers=ORIGIN, json={"agree_terms": False})
    assert r.status_code == 400


def _set_cookie(r, name):
    return next(h for h in r.headers.get_list("set-cookie") if h.startswith(name + "="))


def test_keep_login_cookie_lifetime(up):
    def session_row(c):
        conn = db.connect()
        row = conn.execute("SELECT created_at, expires_at FROM sessions ORDER BY created_at DESC LIMIT 1").fetchone()
        conn.close()
        return (row["expires_at"] - row["created_at"]) / 86400

    c = TestClient(app)
    r = c.post("/api/login", headers=ORIGIN, json={"student_id": "2023001", "password": "pw-a",
                                                   "agree_terms": True, "keep_login": True})
    sc = _set_cookie(r, "kb_session")
    assert "Max-Age=15552000" in sc and "HttpOnly" in sc
    assert round(session_row(c)) == 180

    r = c.post("/api/login", headers=ORIGIN, json={"student_id": "2023001", "password": "pw-a", "agree_terms": True})
    sc = _set_cookie(r, "kb_session")
    assert "Max-Age" not in sc and "expires" not in sc.lower()  # 浏览器会话 cookie
    assert round(session_row(c)) == 7

    # 扫码登录同样支持
    c2 = TestClient(app)
    fid = _qr_start(c2, keep=True)["flow_id"]
    up.qr = "2023002"
    r = _qr_poll(c2, fid)
    assert "Max-Age=15552000" in _set_cookie(r, "kb_session")


def test_qr_login_reuses_this_browsers_device(up):
    c = TestClient(app)
    _login(c, "2023001")                      # 这个浏览器之前用账号密码登录过
    trusted = up.devices[-1]
    assert "kb_dev=" in _set_cookie(_login(c, "2023001"), "kb_dev")
    c.post("/api/logout", headers=_csrf(c))   # 退出后提示 cookie 仍在
    fid = _qr_start(c)["flow_id"]
    assert up.devices[-1] == trusted          # 扫码复用已信任的设备 ID
    up.qr = "2023001"
    assert _qr_poll(c, fid).json()["ok"]

    other = TestClient(app)                   # 没登录过的浏览器：新设备
    _qr_start(other)
    assert up.devices[-1] != trusted


def test_device_hint_tamper_and_delete(up):
    c = TestClient(app)
    _login(c, "2023001")
    trusted = up.devices[-1]
    c.cookies.set("kb_dev", "garbage", path="/")
    _qr_start(c)
    assert up.devices[-1] != trusted          # 篡改过的提示被忽略

    c2 = TestClient(app)
    _login(c2, "2023001")
    r = c2.post("/api/account/delete", headers=_csrf(c2))
    assert 'kb_dev=""' in _set_cookie(r, "kb_dev") or "Max-Age=0" in _set_cookie(r, "kb_dev")


def test_existing_session_gets_device_hint(up):
    c = TestClient(app)
    _login(c, "2023001")
    c.cookies.delete("kb_dev")
    r = c.get("/api/me")
    assert "kb_dev=" in _set_cookie(r, "kb_dev")
    r = c.get("/api/me")
    assert not any(h.startswith("kb_dev=") for h in r.headers.get_list("set-cookie"))  # 已有就不重复下发


def test_manual_refresh_repeats_when_gates_off(up):
    c = TestClient(app)
    _login(c, "2023001")
    for _ in range(3):  # 闸门关掉（fixture 里设为 0）时，连续刷新都真的打到上游
        r = c.post("/api/refresh", headers=_csrf(c), json={})
        assert r.status_code == 200 and r.json()["ok"] is True and r.json()["timetable"]["semester_id"] == 161


def test_sessions_on_multiple_devices_and_logout_all(up):
    from app import security
    phone, laptop = TestClient(app), TestClient(app)
    _login(phone, "2023001")
    _login(laptop, "2023001")
    assert phone.get("/api/me").status_code == 200 and laptop.get("/api/me").status_code == 200
    # 会话数有上限：最早的被挤掉
    extra = []
    for _ in range(security.MAX_SESSIONS_PER_USER):
        security.login_by_student._hits.clear()  # 这里只测会话上限，不测登录限速
        security.login_by_ip._hits.clear()
        c = TestClient(app)
        assert _login(c, "2023001").status_code == 200
        extra.append(c)
    assert phone.get("/api/me").status_code == 401 and extra[-1].get("/api/me").status_code == 200
    # 退出所有设备
    assert extra[-1].post("/api/logout-all", headers=_csrf(extra[-1])).json()["ok"]
    assert all(c.get("/api/me").status_code == 401 for c in extra)


def test_password_login_without_device_hint_is_new_device(up):
    c = TestClient(app)
    _login(c, "2023001")
    trusted = up.devices[-1]
    other = TestClient(app)
    _login(other, "2023001")
    assert up.devices[-1] != trusted


def test_qr_refresh_capped_without_extending_ttl(up):
    c = TestClient(app)
    fid = _qr_start(c)["flow_id"]
    exp = service._pending[fid].expires_at
    for _ in range(service.MAX_QR_REFRESHES):
        assert c.post("/api/login/qr/refresh", headers=ORIGIN, json={"flow_id": fid}).status_code == 200
    assert service._pending[fid].expires_at == exp
    r = c.post("/api/login/qr/refresh", headers=ORIGIN, json={"flow_id": fid})
    assert r.status_code == 400 and "过多" in r.json()["detail"]


def test_captcha_image_fetch_limit(up):
    up.flow_captcha.add("2023001")
    c = TestClient(app)
    fid = _login(c, "2023001").json()["flow_id"]
    for _ in range(service.MAX_CAPTCHA_IMAGES - 1):
        assert c.post("/api/login/captcha-image", headers=ORIGIN, json={"flow_id": fid}).status_code == 200
    r = c.post("/api/login/captcha-image", headers=ORIGIN, json={"flow_id": fid})
    assert r.status_code == 400 and "过多" in r.json()["detail"]


def test_refresh_error_hides_exception_text(up, monkeypatch):
    c = TestClient(app)
    _login(c, "2023001")

    def boom(*_a, **_k):
        raise RuntimeError("https://secret.example/ticket")

    monkeypatch.setattr(jwxt, "fetch_timetable", boom)
    body = c.post("/api/refresh", headers=_csrf(c), json={}).json()
    assert body["ok"] is False and "secret.example" not in body["message"] and "RuntimeError" not in body["message"]
    assert "secret.example" not in (c.get("/api/me").json()["last_error"] or "")


def test_delete_drops_pending_login(up):
    c = TestClient(app)
    _login(c, "2023001")
    up.flow_captcha.add("2023001")
    fid = _login(TestClient(app), "2023001").json()["flow_id"]
    pending = service._pending[fid]
    assert c.post("/api/account/delete", headers=_csrf(c)).json()["ok"]
    assert fid not in service._pending
    with pytest.raises(service.LoginRejected):
        service._complete_login(pending)
    assert db.connect().execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def test_ics_etag_and_log_trim(up):
    c = TestClient(app)
    _login(c, "2023001")
    url = c.post("/api/ics/rotate", headers=_csrf(c)).json()["ics_url"]
    path = url.removeprefix("http://testserver")
    feed = TestClient(app).get(path)
    assert feed.status_code == 200 and "ETag" in feed.headers
    cached = TestClient(app).get(path, headers={"If-None-Match": feed.headers["etag"]})
    assert cached.status_code == 304
    uid = c.get("/api/me").json()["student_id"]
    assert uid == "2023001"
    with db.tx() as conn:
        uid = conn.execute("SELECT id FROM users WHERE student_id=?", ("2023001",)).fetchone()["id"]
        for i in range(60):
            conn.execute("INSERT INTO refresh_log(user_id, at, trigger, ok, message) VALUES(?,?,?,1,?)",
                         (uid, i, "manual", "x"))
    service.housekeeping()
    n = db.connect().execute("SELECT COUNT(*) FROM refresh_log WHERE user_id=?", (uid,)).fetchone()[0]
    assert n == service.LOG_KEEP


def test_concurrent_refresh_same_user_is_busy(up):
    c = TestClient(app)
    _login(c, "2023001")
    lock = service._locks[1]
    lock.acquire()  # 模拟同一用户正在刷新
    try:
        assert c.post("/api/refresh", headers=_csrf(c), json={}).status_code == 409
    finally:
        lock.release()


# ---------- 回归：坏数据 / 并发删除 / 前端资源 ----------

def test_timetable_parse_error_is_user_facing(up, monkeypatch):
    """教务页面结构变了：登录接口应返回 400 中文提示，而不是 500。"""
    def boom(*_a, **_k):
        raise ValueError("personId / currentSemester missing")

    monkeypatch.setattr(jwxt, "fetch_timetable", boom)
    c = TestClient(app)
    r = _login(c, "2023001")
    assert r.status_code == 400 and "无法解析" in r.json()["detail"]
    assert c.get("/api/me").status_code == 401


def test_corrupt_stored_cookies_need_rebind(up):
    c = TestClient(app)
    _login(c, "2023001")
    with db.tx() as conn:
        conn.execute("UPDATE users SET enc_cookies=? WHERE student_id=?", (b"\x01garbage", "2023001"))
    r = c.post("/api/refresh", headers=_csrf(c), json={}).json()
    assert r["ok"] is False and r["status"] == "needs_rebind"
    assert "无法读取" in r["message"]


def test_corrupt_stored_password_need_rebind(up):
    c = TestClient(app)
    _login(c, "2023001", remember=True)
    up.expired.add("2023001")
    with db.tx() as conn:
        conn.execute("UPDATE users SET enc_password=? WHERE student_id=?", (b"\x01garbage", "2023001"))
    r = c.post("/api/refresh", headers=_csrf(c), json={}).json()
    assert r["ok"] is False and r["status"] == "needs_rebind"


def test_missing_user_is_graceful(up):
    assert service._save_failure(999999, "manual", "error", "x")["status"] == "deleted"
    with pytest.raises(service.UserGone), db.tx() as c:
        service._save_success(c, 999999, object(), {
            "semester_id": 1, "semester_name": "x", "student": {}, "activities": [],
            "current_semester_id": 1, "semesters": [],
        }, "manual")


def test_login_cancel_releases_pending_flow(up):
    up.flow_captcha.add("2023001")
    c = TestClient(app)
    fid = _login(c, "2023001").json()["flow_id"]
    assert fid in service._pending
    assert c.post("/api/login/cancel", headers=ORIGIN, json={"flow_id": fid}).json()["ok"]
    assert fid not in service._pending
    r = c.post("/api/login/captcha", headers=ORIGIN, json={"flow_id": fid, "captcha": "abcd"})
    assert r.status_code == 400 and "过期" in r.json()["detail"]


def test_sms_send_returns_cooldown(up):
    up.flow_mfa.add("2023001")
    c = TestClient(app)
    fid = _login(c, "2023001").json()["flow_id"]
    body = c.post("/api/login/sms/send", headers=ORIGIN, json={"flow_id": fid}).json()
    assert body["message"] == "验证码已发送" and 0 <= body["retry_after"] <= 300


def test_validation_errors_have_string_detail(up):
    c = TestClient(app)
    r = c.post("/api/login", headers=ORIGIN, json={})
    assert r.status_code == 422 and isinstance(r.json()["detail"], str)
    _login(c, "2023001")
    r = c.get("/api/timetable?semester_id=0")
    assert r.status_code == 422 and isinstance(r.json()["detail"], str)


def test_corrupt_timetable_is_hidden_not_500(up):
    c = TestClient(app)
    _login(c, "2023001")
    with db.tx() as conn:
        conn.execute("UPDATE timetables SET data_json=? WHERE semester_id=161", ("{not json",))
    assert c.get("/api/timetable").status_code == 404
    url = c.post("/api/ics/rotate", headers=_csrf(c)).json()["ics_url"]
    feed = TestClient(app).get(url.removeprefix("http://testserver"))
    assert feed.status_code == 200 and "BEGIN:VCALENDAR" in feed.text and "BEGIN:VEVENT" not in feed.text


def test_corrupt_semester_json_does_not_break_me(up):
    c = TestClient(app)
    _login(c, "2023001")
    with db.tx() as conn:
        conn.execute("UPDATE users SET semesters_json=?, enc_ics_token=? WHERE student_id=?",
                     ("not json", b"\x01bad", "2023001"))
    me = c.get("/api/me").json()
    assert me["semesters"] == [] and me["ics_url"] is None


def test_frontend_index_references_existing_assets(up):
    from bs4 import BeautifulSoup
    c = TestClient(app)
    r = c.get("/")
    assert r.status_code == 200 and "?v=" in r.text
    soup = BeautifulSoup(r.text, "html.parser")
    refs = [t["src"] for t in soup.find_all(src=True)] + [t["href"] for t in soup.find_all(href=True)]
    local = [ref for ref in refs if ref.startswith("static/")]
    assert local, "index.html 应该引用 static/ 下的前端资源"
    for ref in local:
        path, _, query = ref.partition("?")
        assert query.startswith("v="), ref
        assert c.get("/" + path).status_code == 200, path
    # CSP 不允许内联脚本/样式，页面里也不该有
    assert not soup.find_all("script", src=False)
    assert not soup.find_all("style")


def test_healthz_and_unknown_api(up):
    c = TestClient(app)
    assert c.get("/healthz").json()["ok"] is True
    assert c.get("/health").json()["ok"] is True
    r = c.get("/api/does-not-exist")
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")


def test_housekeeping_reclaims_locks_and_pending(up):
    c = TestClient(app)
    _login(c, "2023001")
    up.flow_captcha.add("2023002")
    fid = _login(TestClient(app), "2023002").json()["flow_id"]
    service._pending[fid].expires_at = 0  # 人为过期
    service.housekeeping()
    assert fid not in service._pending
    # 已删除用户不应继续占着刷新锁
    uid = 1
    service._locks[uid]
    with db.tx() as conn:
        conn.execute("DELETE FROM users WHERE id=?", (uid,))
    service.housekeeping()
    assert uid not in service._locks


def test_frontend_module_graph_and_security_hygiene(up):
    """前端模块 import 必须全部存在；不出现 HTML 字符串拼接/eval 之类的危险写法。"""
    import re
    from pathlib import Path
    static = Path(__file__).resolve().parent.parent / "static"
    todo, seen = [static / "js" / "main.js"], set()
    while todo:
        path = todo.pop().resolve()
        if path in seen:
            continue
        assert path.exists(), f"missing module: {path}"
        seen.add(path)
        text = path.read_text("utf-8")
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
            assert sink not in text, f"{path.name} uses {sink}"
        for spec in re.findall(r"""from\s+["'](\.[^"']+)["']""", text):
            target = (path.parent / spec).resolve()
            assert target.exists(), f"{path.name} imports missing {spec}"
            todo.append(target)
    assert len(seen) >= 10

    c = TestClient(app)
    css = c.get("/static/css/tokens.css")
    assert css.status_code == 200 and "must-revalidate" in css.headers["cache-control"]
    js = c.get("/static/js/main.js")
    assert js.status_code == 200 and "must-revalidate" in js.headers["cache-control"]
    html = c.get("/").text
    assert "theme-init.js" in html and "static/js/main.js" in html
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html)


def test_ics_access_log_and_error_paths_are_redacted():
    import logging

    from app.main import _RedactTokenPaths, _safe_log_path

    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, "", 0,
        '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4", "GET", "/ics/secret-token.ics", "1.1", 200), None)
    assert _RedactTokenPaths().filter(record) is True
    assert "secret-token" not in record.getMessage() and "<redacted>" in record.getMessage()
    assert _safe_log_path("/ics/secret-token.ics") == "/ics/<redacted>"
    assert _safe_log_path("/widget/secret-token.json?x=1") == "/widget/<redacted>"
    assert _safe_log_path("/api/me") == "/api/me"


def test_static_index_template_is_not_served_and_csp_is_strict(up):
    c = TestClient(app)
    assert c.get("/static/index.html").status_code == 404
    csp = c.get("/").headers["content-security-policy"]
    assert "require-trusted-types-for 'script'" in csp
    assert "default-src 'self'" in csp


def test_sec_fetch_site_fallback(up):
    """Referrer 被隐私设置去掉时，浏览器给的同源标记仍应通过。"""
    c = TestClient(app)
    r = c.post("/api/login", headers={"Sec-Fetch-Site": "same-origin"},
               json={"student_id": "2023001", "password": "pw-a", "agree_terms": True})
    assert r.status_code == 200 and r.json()["ok"] is True
    r = c.post("/api/login", headers={"Sec-Fetch-Site": "cross-site"},
               json={"student_id": "2023001", "password": "pw-a", "agree_terms": True})
    assert r.status_code == 403


def test_ics_cache_invalidated_when_calendar_changes(up, monkeypatch):
    c = TestClient(app)
    _login(c, "2023001")
    url = c.post("/api/ics/rotate", headers=_csrf(c)).json()["ics_url"]
    path = url.removeprefix("http://testserver")
    first_resp = TestClient(app).get(path)
    first = first_resp.text
    assert "20260907T082000" in first  # 2026-09-07 第 1 周周一

    monkeypatch.setattr(service, "semester_overrides", lambda: {"2026-2027-1": "2026-09-14"})
    second_resp = TestClient(app).get(path, headers={"If-None-Match": first_resp.headers["etag"]})
    assert second_resp.status_code == 200  # 正文变了，ETag 也必须变
    second = second_resp.text
    assert "20260907T082000" not in second
    assert "20260914T082000" in second


# ---------- 邮件通知 ----------

def _bind_email(c, outbox, email="me@example.com"):
    r = c.post("/api/email/start", headers=_csrf(c), json={"email": email})
    assert r.status_code == 200, r.text
    to, subject, body = outbox[-1]
    code = next(w for w in subject.split() if w.isdigit() and len(w) == 6)
    assert to.lower() == email.lower() and code in body
    r = c.post("/api/email/verify", headers=_csrf(c), json={"code": code})
    assert r.status_code == 200 and r.json()["email"].lower() == email.lower()
    return code


def test_email_bind_verify_and_me(up, outbox):
    c = TestClient(app)
    _login(c, "2023001")
    mail = c.get("/api/me").json()["mail"]
    assert mail["available"] is True and mail["email"] is None
    _bind_email(c, outbox, "Me.Name@EXAMPLE.com")
    mail = c.get("/api/me").json()["mail"]
    assert mail["email"] == "Me.Name@example.com" and mail["notify_status"] and mail["notify_changes"]
    # 邮箱加密落库
    raw = db.connect().execute("SELECT enc_email FROM users WHERE student_id='2023001'").fetchone()[0]
    assert b"example.com" not in raw


def test_email_code_wrong_expired_and_resend_gap(up, outbox):
    from app import notify
    c = TestClient(app)
    _login(c, "2023001")
    assert c.post("/api/email/start", headers=_csrf(c), json={"email": "a@example.com"}).status_code == 200
    again = c.post("/api/email/start", headers=_csrf(c), json={"email": "a@example.com"})
    assert again.status_code == 429 and int(again.headers["Retry-After"]) <= notify.RESEND_GAP
    wrong = c.post("/api/email/verify", headers=_csrf(c), json={"code": "000000"})
    if wrong.status_code == 200:  # 极小概率撞上真验证码
        return
    assert wrong.status_code == 400 and "再试" in wrong.json()["detail"]
    with db.tx() as conn:
        conn.execute("UPDATE email_verifications SET expires_at=?", (db.now() - 1,))
    code = next(w for w in outbox[-1][1].split() if w.isdigit() and len(w) == 6)
    r = c.post("/api/email/verify", headers=_csrf(c), json={"code": code})
    assert r.status_code == 400 and "失效" in r.json()["detail"]
    assert c.get("/api/me").json()["mail"]["email"] is None


def test_email_attempt_limit(up, outbox):
    from app import notify
    c = TestClient(app)
    _login(c, "2023001")
    c.post("/api/email/start", headers=_csrf(c), json={"email": "a@example.com"})
    code = next(w for w in outbox[-1][1].split() if w.isdigit() and len(w) == 6)
    bad = "111111" if code != "111111" else "222222"
    for _ in range(notify.CODE_MAX_ATTEMPTS):
        c.post("/api/email/verify", headers=_csrf(c), json={"code": bad})
    # 试满之后，正确的验证码也不再接受
    assert c.post("/api/email/verify", headers=_csrf(c), json={"code": code}).status_code == 400


def test_email_rejects_bad_address_and_limits_sends(up, outbox, monkeypatch):
    from app import notify
    c = TestClient(app)
    _login(c, "2023001")
    for bad in ("not-an-email", "a@b", "a b@example.com", "a@example.com\nBcc: x@y.com"):
        assert c.post("/api/email/start", headers=_csrf(c), json={"email": bad}).status_code in (400, 422)
    monkeypatch.setattr(notify, "RESEND_GAP", 0)
    codes = [c.post("/api/email/start", headers=_csrf(c), json={"email": "a@example.com"}).status_code
             for _ in range(6)]
    assert codes[:5] == [200] * 5 and codes[5] == 429  # 每人每小时最多 5 封


def test_email_prefs_test_and_remove(up, outbox):
    c = TestClient(app)
    _login(c, "2023001")
    assert c.post("/api/email/test", headers=_csrf(c)).status_code == 400  # 还没绑定
    _bind_email(c, outbox)
    r = c.post("/api/email/prefs", headers=_csrf(c), json={"notify_status": False, "notify_changes": True}).json()
    assert r["notify_status"] is False and r["notify_changes"] is True
    assert c.post("/api/email/test", headers=_csrf(c)).status_code == 200 and "测试" in outbox[-1][1]
    assert c.post("/api/email/remove", headers=_csrf(c)).json()["email"] is None


def _scheduled(student_id):
    with scheduler.background_upstream():
        return service.refresh_user(service_uid(student_id), "scheduled")


def test_paused_refresh_emails_once_and_only_in_background(up, outbox):
    c = TestClient(app)
    _login(c, "2023001")
    _bind_email(c, outbox)
    up.expired.add("2023001")
    before = len(outbox)
    c.post("/api/refresh", headers=_csrf(c), json={})  # 手动刷新：人在屏幕前，不发邮件
    assert len(outbox) == before
    with db.tx() as conn:
        conn.execute("UPDATE users SET status='ok'")
    assert _scheduled("2023001")["status"] == "needs_rebind"
    assert len(outbox) == before + 1 and "重新登录" in outbox[-1][1] and "http://testserver/" in outbox[-1][2]
    _scheduled("2023001")  # 状态没变：不重复发
    assert len(outbox) == before + 1


def test_error_threshold_emails_once(up, outbox, monkeypatch):
    c = TestClient(app)
    _login(c, "2023001")
    _bind_email(c, outbox)
    before = len(outbox)

    def boom(*_a, **_k):
        raise ConnectionError("down")

    monkeypatch.setattr(jwxt, "fetch_timetable", boom)
    for _ in range(service.REFRESH_MAX_FAILS + 1):
        _scheduled("2023001")
    assert len(outbox) == before + 1 and "连续失败" in outbox[-1][1]


def test_timetable_change_email(up, outbox):
    c = TestClient(app)
    _login(c, "2023001")
    _bind_email(c, outbox)
    before = len(outbox)
    _scheduled("2023001")  # 没变化：不发
    assert len(outbox) == before
    up.course_override, up.room_override = "高数", "B202"
    assert _scheduled("2023001")["ok"] is True
    to, subject, body = outbox[-1]
    assert len(outbox) == before + 1 and "课表有变动" in subject
    assert "B202" in body and "A101" in body and "周一" in body
    # 关掉“课表变动”提醒后不再发
    c.post("/api/email/prefs", headers=_csrf(c), json={"notify_status": True, "notify_changes": False})
    up.room_override = "C303"
    _scheduled("2023001")
    assert len(outbox) == before + 1


def test_no_email_when_not_bound(up, outbox):
    c = TestClient(app)
    _login(c, "2023001")
    up.expired.add("2023001")
    _scheduled("2023001")
    assert outbox == []


def test_delete_account_removes_email(up, outbox):
    c = TestClient(app)
    _login(c, "2023001")
    _bind_email(c, outbox)
    c.post("/api/email/start", headers=_csrf(c), json={"email": "other@example.com"})
    c.post("/api/account/delete", headers=_csrf(c))
    conn = db.connect()
    assert conn.execute("SELECT COUNT(*) FROM users WHERE student_id='2023001'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM email_verifications").fetchone()[0] == 0


# ---------- 桌面小组件 ----------

def test_widget_link_lifecycle(up):
    c = TestClient(app)
    _login(c, "2023001")
    assert c.get("/api/me").json()["widget_url"] is None
    assert c.get("/api/widget/script").status_code == 404  # 还没生成链接
    url = c.post("/api/widget/rotate", headers=_csrf(c)).json()["widget_url"]
    assert url.startswith("http://testserver/widget/") and url.endswith(".json")
    assert c.get("/api/me").json()["widget_url"] == url
    path = url.removeprefix("http://testserver")
    anon = TestClient(app)  # 链接本身就是凭证，不需要登录
    r = anon.get(path)
    assert r.status_code == 200 and r.json()["v"] == 1 and r.json()["semester"] == "2026-2027-1"
    assert r.headers["x-robots-tag"].startswith("noindex") and "private" in r.headers["cache-control"]
    script = c.get("/api/widget/script")
    assert script.status_code == 200 and url in script.text and "__WIDGET_URL__" not in script.text
    assert "no-store" in script.headers["cache-control"]
    # 重新生成：旧链接立即失效
    url2 = c.post("/api/widget/rotate", headers=_csrf(c)).json()["widget_url"]
    assert anon.get(path).status_code == 404 and anon.get(url2.removeprefix("http://testserver")).status_code == 200
    c.post("/api/widget/disable", headers=_csrf(c))
    assert anon.get(url2.removeprefix("http://testserver")).status_code == 404
    assert c.get("/api/me").json()["widget_url"] is None


def test_widget_script_self_update_by_token(up):
    import re

    c = TestClient(app)
    _login(c, "2023001")
    url = c.post("/api/widget/rotate", headers=_csrf(c)).json()["widget_url"]
    anon = TestClient(app)
    js = anon.get(url.removeprefix("http://testserver").removesuffix(".json") + ".js")
    assert js.status_code == 200 and url in js.text and "no-store" in js.headers["cache-control"]
    version = re.search(r'const SCRIPT_VERSION = "([^"]+)"', js.text).group(1)
    assert re.fullmatch(r"sha-[0-9a-f]{12}", version) and "__SCRIPT_VERSION__" not in js.text
    assert version > "2026.11.04"  # 旧的日期版本脚本按字符串比较也会提示更新
    payload = anon.get(url.removeprefix("http://testserver")).json()
    assert payload["script_version"] == version and payload["semester_id"] == 161
    # 设置页复制的脚本和自我更新拿到的是同一个版本
    assert f'const SCRIPT_VERSION = "{version}"' in c.get("/api/widget/script").text
    assert anon.get("/widget/nope.js").status_code == 404


def test_widget_requires_login_and_reports_status(up):
    anon = TestClient(app)
    assert anon.get("/api/widget/script").status_code == 401
    assert anon.post("/api/widget/rotate", headers=ORIGIN).status_code == 401
    assert anon.get("/widget/" + "x" * 200 + ".json").status_code == 404
    c = TestClient(app)
    _login(c, "2023001")
    path = c.post("/api/widget/rotate", headers=_csrf(c)).json()["widget_url"].removeprefix("http://testserver")
    up.expired.add("2023001")
    c.post("/api/refresh", headers=_csrf(c), json={})
    assert anon.get(path).json()["status"] == "needs_rebind"  # 小组件据此提示重新登录


def test_widget_token_removed_with_account(up):
    c = TestClient(app)
    _login(c, "2023001")
    path = c.post("/api/widget/rotate", headers=_csrf(c)).json()["widget_url"].removeprefix("http://testserver")
    c.post("/api/account/delete", headers=_csrf(c))
    assert TestClient(app).get(path).status_code == 404


# ---------- 动态刷新 ----------

def _set_week1(monday_iso):
    with db.tx() as conn:
        conn.execute("""INSERT INTO semesters(semester_id, name, week1_monday) VALUES(161, '2026-2027-1', ?)
                        ON CONFLICT(semester_id) DO UPDATE SET week1_monday=excluded.week1_monday""", (monday_iso,))


def _row(student_id, cols="*"):
    return db.connect().execute(f"SELECT {cols} FROM users WHERE student_id=?", (student_id,)).fetchone()


def test_next_class_computed_from_saved_timetable(up):
    import datetime as dt
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Asia/Shanghai")
    today = dt.datetime.now(tz).date()
    _set_week1((today - dt.timedelta(days=today.weekday())).isoformat())  # 本周是第 1 周
    c = TestClient(app)
    _login(c, "2023001")
    at = _row("2023001")["next_class_at"]
    # 示例课程：每周一 08:20，1~16 周
    t = dt.datetime.fromtimestamp(at, tz)
    assert t.weekday() == 0 and (t.hour, t.minute) == (8, 20) and at > db.now()
    me = c.get("/api/me").json()
    assert me["refresh_target_minutes"] >= 15 and me["refresh_reason"]


def test_opening_stale_page_queues_background_refresh(up):
    c = TestClient(app)
    _login(c, "2023001")
    assert c.get("/api/me").json()["refresh_pending"] is False
    with db.tx() as conn:
        conn.execute("UPDATE users SET last_success_at=last_success_at-30*86400")
    me = c.get("/api/me").json()
    assert me["refresh_pending"] is True and me["next_refresh_at"] <= db.now() + 1
    assert _row("2023001", "refresh_request_reason")[0] == "打开时数据已过期"
    assert _queued()[0] == service_uid("2023001")
    # 刷新之后请求清掉
    c.post("/api/refresh", headers=_csrf(c), json={})
    assert c.get("/api/me").json()["refresh_pending"] is False


def test_widget_push_refresh(up):
    c = TestClient(app)
    _login(c, "2023001")
    path = c.post("/api/widget/rotate", headers=_csrf(c)).json()["widget_url"].removeprefix("http://testserver")
    anon = TestClient(app)

    fresh = anon.get(path + "?refresh=1")
    assert fresh.json()["refresh"] == {"pending": False, "due": False, "fresh": True}
    assert "max-age=60" in fresh.headers["cache-control"]
    assert _row("2023001", "refresh_requested_at")[0] is None

    # 10 分钟前同步过：还没到目标间隔，普通打开不排队；小组件点了刷新则会排队
    with db.tx() as conn:
        conn.execute("UPDATE users SET last_success_at=? WHERE student_id='2023001'", (db.now() - 600,))
    opened = anon.get(path).json()["refresh"]
    assert opened == {"pending": False, "due": False, "fresh": False}
    pushed = anon.get(path + "?refresh=1")
    assert pushed.json()["refresh"]["pending"] is True
    assert "no-store" in pushed.headers["cache-control"]
    assert _row("2023001", "refresh_request_reason")[0] == "小组件请求刷新"
    anon.get(path + "?refresh=1")  # 已经在排队：不改原因
    assert _row("2023001", "refresh_request_reason")[0] == "小组件请求刷新"

    with db.tx() as conn:
        conn.execute(
            """UPDATE users SET last_success_at=?, refresh_requested_at=NULL, refresh_request_reason=NULL
               WHERE student_id='2023001'""", (db.now() - 30 * 86400,))
    assert anon.get(path).json()["refresh"]["pending"] is True
    assert _row("2023001", "refresh_request_reason")[0] == "打开时数据已过期"

    up.expired.add("2023001")
    c.post("/api/refresh", headers=_csrf(c), json={})
    denied = anon.get(path + "?refresh=1").json()
    assert denied["status"] == "needs_rebind" and denied["refresh"]["pending"] is False


def test_widget_and_ics_record_activity(up, monkeypatch):
    monkeypatch.setattr(service, "_touched", {})
    c = TestClient(app)
    _login(c, "2023001")
    with db.tx() as conn:
        conn.execute("UPDATE users SET last_seen_at=NULL")
    service._touched.clear()
    path = c.post("/api/widget/rotate", headers=_csrf(c)).json()["widget_url"].removeprefix("http://testserver")
    service._touched.clear()
    TestClient(app).get(path)
    assert _row("2023001", "last_seen_at")[0] > db.now() - 5
    with db.tx() as conn:
        conn.execute("UPDATE users SET last_seen_at=NULL")
    service._touched.clear()
    ics = c.post("/api/ics/rotate", headers=_csrf(c)).json()["ics_url"].removeprefix("http://testserver")
    service._touched.clear()
    TestClient(app).get(ics)
    assert _row("2023001", "last_seen_at")[0] > db.now() - 5


def test_touch_is_throttled(up, monkeypatch):
    _login(TestClient(app), "2023001")
    uid = service_uid("2023001")
    service._touched.clear()
    service.touch(uid, now=1000.0)
    service.touch(uid, now=1000.0 + 60)  # 10 分钟内不再写库
    assert _row("2023001", "last_seen_at")[0] == 1000.0
    service.touch(uid, now=1000.0 + service.TOUCH_EVERY + 1)
    assert _row("2023001", "last_seen_at")[0] == 1000.0 + service.TOUCH_EVERY + 1


def test_change_alerts_classmates(up):
    up.course_override = "高数"  # 两个人上同一节课
    a, b = TestClient(app), TestClient(app)
    _login(a, "2023001")
    _login(b, "2023002")
    assert _row("2023002", "refresh_requested_at")[0] is None
    up.room_override = "B202"  # 调了教室
    a.post("/api/refresh", headers=_csrf(a), json={})
    row = _row("2023002", "refresh_requested_at, refresh_request_reason")
    assert row[0] is not None and row[1] == "同班同学的课表有变动"
    assert _queued()[0] == service_uid("2023002")  # 同学排到队首
    assert _row("2023001", "last_change_at")[0] is not None
    # 同学刷新后（也看到了新教室）请求清掉，不会连锁再提醒回去
    b.post("/api/refresh", headers=_csrf(b), json={})
    assert _row("2023002", "refresh_requested_at")[0] is None
    assert _row("2023001", "refresh_requested_at")[0] is None


def test_makeup_class_alerts_classmates(up):
    """补课 / 加课：没有旧安排被改掉，按课程找同学，还没有这条新排课的人排到队首。"""
    up.course_override = "高数"
    a, b = TestClient(app), TestClient(app)
    _login(a, "2023001")
    _login(b, "2023002")
    makeup = {"courseName": "高数", "teachers": ["T"], "weekday": 6, "startUnit": 1, "endUnit": 2,
              "weeksStr": "9", "weekIndexes": [9], "room": "C303", "building": "", "campus": "",
              "startTime": "8:20", "endTime": "9:45", "credits": 2}
    up.extra_activities["2023001"] = [makeup]
    a.post("/api/refresh", headers=_csrf(a), json={})
    assert _row("2023002", "refresh_request_reason")[0] == "同班同学的课表有变动"
    # 同学刷到了补课：清掉请求；再有人刷新也不会被反复叫醒
    up.extra_activities["2023002"] = [makeup]
    b.post("/api/refresh", headers=_csrf(b), json={})
    assert _row("2023002", "refresh_requested_at")[0] is None
    assert service.alert_classmates(service_uid("2023001"), 161, [], [makeup]) == 0


def test_classmate_covers_pre_class_check(up):
    """出门前检查点：同一节课的同学刚刷过，就不用再替我向学校要一次。"""
    import datetime as dt
    from zoneinfo import ZoneInfo
    from app import refresh_policy as P
    tz = ZoneInfo("Asia/Shanghai")
    up.course_override = "高数"
    a, b = TestClient(app), TestClient(app)
    _login(a, "2023001")
    _login(b, "2023002")
    now = db.now()
    today = dt.datetime.fromtimestamp(now, tz).date()
    _set_week1((today - dt.timedelta(days=today.weekday())).isoformat())
    a_id, b_id = service_uid("2023001"), service_uid("2023002")
    for uid in (a_id, b_id):
        with db.tx() as conn:
            service._store_next_class(conn, uid, now)
    cls = _row("2023001", "next_class_at")[0]
    assert cls and _row("2023002", "next_class_at")[0] == cls
    window = cls - P.CHECK_LEAD - P.CHECK_WINDOW + 60
    with db.tx() as conn:  # 两个人都在窗口开始前刷过
        conn.execute("UPDATE users SET last_success_at=?, last_seen_at=?", (window - 3600, window))
        conn.execute("UPDATE timetables SET fetched_at=?", (window - 3600,))
    assert set(_queued_at(window)) == {a_id, b_id}
    with db.tx() as conn:  # A 在窗口里刷到了：B 的这节课也算检查过
        conn.execute("UPDATE users SET last_success_at=? WHERE id=?", (window, a_id))
        conn.execute("UPDATE timetables SET fetched_at=? WHERE user_id=?", (window, a_id))
    assert _queued_at(window + 60) == []
    assert service.still_due(b_id, window + 60) is False


def test_widget_marks_changed_and_cancelled_lessons(up):
    import datetime as dt
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Asia/Shanghai")
    today = dt.datetime.now(tz).date()
    monday = today - dt.timedelta(days=today.weekday())
    _set_week1(monday.isoformat())  # 本周第 1 周；示例课每周一 8:20
    c = TestClient(app)
    _login(c, "2023001")
    path = c.post("/api/widget/rotate", headers=_csrf(c)).json()["widget_url"].removeprefix("http://testserver")
    up.course_override = "高数"
    up.room_override = "B202"  # 改教室
    c.post("/api/refresh", headers=_csrf(c), json={})
    feed = TestClient(app).get(path).json()
    mon = feed["week"]["days"][0]["lessons"][0]
    assert mon["changed"] is True and mon["was"] == "原 A101"
    assert feed["changed_at"] and abs(feed["changed_at"] - db.now()) < 30
    assert feed["next_week"]["days"][0]["lessons"][0]["changed"] is True  # 整条排课都改了

    # 这门课停了、同一时间换成另一门：新课标“调”，旧课列进 cancelled
    up.course_override, up.room_override = "线代", None
    c.post("/api/refresh", headers=_csrf(c), json={})
    mon = TestClient(app).get(path).json()["week"]["days"][0]
    assert [(x["name"], x.get("changed")) for x in mon["lessons"]] == [("线代", True)]
    assert [x["name"] for x in mon["cancelled"]] == ["高数"]


def test_unrelated_users_not_alerted(up):
    a, b = TestClient(app), TestClient(app)
    _login(a, "2023001")  # 高数
    _login(b, "2023002")  # 英语
    up.course_override = "线代"
    a.post("/api/refresh", headers=_csrf(a), json={})
    assert _row("2023002", "refresh_requested_at")[0] is None


def test_failure_clears_pending_request(up):
    c = TestClient(app)
    _login(c, "2023001")
    service.request_refresh(service_uid("2023001"), "test")
    up.expired.add("2023001")
    c.post("/api/refresh", headers=_csrf(c), json={})
    assert _row("2023001", "refresh_requested_at")[0] is None
    assert service_uid("2023001") not in _queued(far_future=True)  # needs_rebind：暂停


def test_scheduler_tick_summary_log(up, monkeypatch, caplog):
    import logging
    c = TestClient(app)
    _login(c, "2023001")
    _login(TestClient(app), "2023002")
    monkeypatch.setattr(scheduler._stop, "wait", lambda t: False)
    caplog.set_level(logging.INFO, logger="kebiao.scheduler")
    scheduler.run_once(_budget())  # 都是刚登录的新数据：没人到期，不记
    assert not [r for r in caplog.records if "tick:" in r.getMessage()]
    with db.tx() as conn:
        conn.execute("UPDATE users SET last_success_at=last_success_at-30*86400")
    scheduler.run_once(_budget(tokens=1))  # 两人到期，预算只够刷一个
    msg = [r.getMessage() for r in caplog.records if "tick:" in r.getMessage()][-1]
    assert "due=2 refreshed=1" in msg and "budget=0.65/s" in msg
    # 学校报错：预算减半并写明原因
    from app import polite
    monkeypatch.setattr(polite, "health", lambda: (2.0, 0.0))
    b = _budget(tokens=0)
    scheduler.run_once(b)
    msg = [r.getMessage() for r in caplog.records if "tick:" in r.getMessage()][-1]
    assert "budget=0.33/s" in msg and "减半" in msg
