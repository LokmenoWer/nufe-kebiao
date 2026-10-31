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
        self.sms_sent = 0
        self.devices = []
        self.qr = "waiting"       # waiting | scanned | expired | <学号>（= 该学号已在手机上确认）
        self.qr_mfa = False
        self.tgc_alive = set()    # 教务会话过期但 CASTGC 仍有效：可以静默续期
        self.renewals = 0
        self.fetches = 0

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
        if self.username in self.up.flow_mfa:
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
    return fake


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
    assert service.due_users(10) == []


def test_scheduler_updates_last_refresh(up, monkeypatch):
    c = TestClient(app)
    _login(c, "2023001")
    before = c.get("/api/me").json()["last_refresh_at"]
    with db.tx() as conn:
        conn.execute("UPDATE users SET next_refresh_at=0")
    monkeypatch.setattr(scheduler, "_allowed_now", lambda: True)
    monkeypatch.setattr(scheduler._stop, "wait", lambda t: False)
    assert scheduler.run_once() == 1
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
    assert service.due_users(10) == []


def test_device_id_is_stable_per_user(up):
    c = TestClient(app)
    _login(c, "2023001", remember=True)
    _login(c, "2023001", remember=True)
    up.expired.add("2023001")
    c.post("/api/refresh", headers=_csrf(c), json={})
    assert len(up.devices) == 3 and len(set(up.devices)) == 1  # 两次站点登录 + 一次后台重登，同一设备
    _login(TestClient(app), "2023002")
    assert up.devices[-1] != up.devices[0]


def test_next_scheduled_picks_next_chosen_hour():
    import datetime as dt
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Asia/Shanghai")
    now = dt.datetime(2026, 10, 28, 14, 50, tzinfo=tz).timestamp()
    for _ in range(20):
        t = dt.datetime.fromtimestamp(service.next_scheduled([6, 22], now), tz)
        assert (t.day, t.hour) == (28, 22) and t.minute <= 45
    # 距离不足 1 小时的整点跳过，顺延到下一个
    now = dt.datetime(2026, 10, 28, 21, 30, tzinfo=tz).timestamp()
    t = dt.datetime.fromtimestamp(service.next_scheduled([6, 22], now), tz)
    assert (t.day, t.hour) == (29, 6)
    # 没选 → 默认间隔 ±15%
    d = service.next_scheduled(None, now) - now
    assert 12 * 3600 * 0.85 <= d <= 12 * 3600 * 1.15


def test_refresh_hours_api(up):
    import datetime as dt
    from zoneinfo import ZoneInfo
    c = TestClient(app)
    _login(c, "2023001")
    me = c.get("/api/me").json()
    assert me["refresh_hours"] == [] and 22 in me["refresh_allowed_hours"] and me["refresh_max_times"] == 3
    r = c.post("/api/settings/refresh-hours", headers=_csrf(c), json={"hours": [5, 22]}).json()
    assert dt.datetime.fromtimestamp(r["next_refresh_at"], ZoneInfo("Asia/Shanghai")).hour in (5, 22)
    assert c.get("/api/me").json()["refresh_hours"] == [5, 22]
    # 上课时段 / 超过 3 个 / 无 CSRF 都拒绝
    assert c.post("/api/settings/refresh-hours", headers=_csrf(c), json={"hours": [9]}).status_code == 400
    assert c.post("/api/settings/refresh-hours", headers=_csrf(c), json={"hours": [0, 1, 2, 3]}).status_code == 400
    assert c.post("/api/settings/refresh-hours", headers=ORIGIN, json={"hours": [1]}).status_code == 403
    # 成功刷新后也按自选时间排下一次
    c.post("/api/refresh", headers=_csrf(c), json={})
    nxt = c.get("/api/me").json()["next_refresh_at"]
    assert dt.datetime.fromtimestamp(nxt, ZoneInfo("Asia/Shanghai")).hour in (5, 22)
    # 清空 = 恢复默认
    c.post("/api/settings/refresh-hours", headers=_csrf(c), json={"hours": []})
    assert c.get("/api/me").json()["refresh_hours"] == []


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
        conn.execute("UPDATE users SET semesters_json=?, refresh_hours=?, enc_ics_token=? WHERE student_id=?",
                     ("not json", "[[", b"\x01bad", "2023001"))
    me = c.get("/api/me").json()
    assert me["semesters"] == [] and me["refresh_hours"] == [] and me["ics_url"] is None


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

    from app.main import _RedactIcsPath, _safe_log_path

    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, "", 0,
        '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4", "GET", "/ics/secret-token.ics", "1.1", 200), None)
    assert _RedactIcsPath().filter(record) is True
    assert "secret-token" not in record.getMessage() and "<redacted>" in record.getMessage()
    assert _safe_log_path("/ics/secret-token.ics") == "/ics/<redacted>"
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
