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

    def fetch(self, session, semester_id=None, page_html=None):
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
    return fake


def _login(client, sid, remember=False, pw=None):
    r = client.post("/api/login", headers=ORIGIN, json={
        "student_id": sid, "password": pw or PASSWORDS[sid], "remember_password": remember, "agree_terms": True})
    return r


def _csrf(client):
    return {**ORIGIN, "X-CSRF-Token": client.get("/api/me").json()["csrf"]}


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
    raw = open(db.DB_PATH, "rb").read()
    import glob
    for f in glob.glob(db.DB_PATH + "*"):
        raw += open(f, "rb").read()
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
    poll = lambda: _qr_poll(c, fid).json()
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


def test_manual_refresh_not_rate_limited_per_user(up):
    c = TestClient(app)
    _login(c, "2023001")
    for _ in range(3):  # 不再有单用户冷却；对学校的频率由全站限速保证
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
