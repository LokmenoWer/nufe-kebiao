"""Web 层端到端测试：上游（CAS/教务）全部打桩，不访问学校服务器。"""
import base64
import hashlib
import json
import time
from types import SimpleNamespace

import pytest
import requests
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient

from app import admin, android_releases, cas, crypto, db, jwxt, main, mobile_devices, scheduler, service
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
        self.sso_dead = set()     # 认证中心已不认这些人的 CASTGC（保活时发现）
        self.touches = []         # 保活请求：("sso" | "jwxt", 学号)
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
        session.cookies.set("CASTGC", f"tgc-{username}", domain="authserver.nufe.edu.cn", path="/authserver",
                            expires=TGC_REMEMBER_UNTIL)
        self.expired.discard(username)

    def touch_sso(self, session):
        sid = (session.cookies.get("CASTGC", domain="authserver.nufe.edu.cn", path="/authserver") or "").removeprefix("tgc-")
        self.touches.append(("sso", sid))
        return bool(sid) and sid not in self.sso_dead

    def touch_jwxt(self, session):
        sid = (session.cookies.get("CASTGC", domain="authserver.nufe.edu.cn", path="/authserver") or "").removeprefix("tgc-")
        self.touches.append(("jwxt", sid))
        return bool(sid) and sid not in self.expired

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


TGC_REMEMBER_UNTIL = 4_102_444_800  # 2100 年：账号密码登录（rememberMe）拿到的长效 CASTGC


class FakeFlow:
    """模拟 cas.LoginFlow 的状态机。"""
    up: "FakeUpstream"
    can_trust_device = True

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
        # 和学校一样：账号密码登录带“记住我”，CASTGC 有过期时间；扫码登录只给会话级 CASTGC
        self.session.cookies.set("CASTGC", f"tgc-{who}", domain="authserver.nufe.edu.cn", path="/authserver",
                                 expires=None if not self.username else TGC_REMEMBER_UNTIL)
        self.up.expired.discard(who)
        self.stage = "done"
        self.wipe_password()
        return self.stage


@pytest.fixture()
def up(monkeypatch):
    db.init()
    with db.tx() as c:
        c.execute("DELETE FROM users")
        c.execute("DELETE FROM semesters")  # 别让上一个用例写的开学日期漏到下一个
        c.execute("DELETE FROM admin_sessions")
    service._locks.clear()
    service._pending.clear()
    service._completed.clear()
    service._ics_cache.clear()
    from app import security
    security.login_by_ip._hits.clear()
    security.login_by_student._hits.clear()
    main.mobile_pair_by_user._hits.clear()
    fake = FakeUpstream()
    monkeypatch.setattr(cas, "login", fake.login)
    FakeFlow.up = fake
    monkeypatch.setattr(cas, "LoginFlow", FakeFlow)
    monkeypatch.setattr(jwxt, "fetch_timetable", fake.fetch)
    monkeypatch.setattr(cas, "renew_with_tgc", fake.renew)
    monkeypatch.setattr(cas, "touch_sso", fake.touch_sso)
    monkeypatch.setattr(jwxt, "touch", fake.touch_jwxt)
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


def test_qr_poll_while_login_completes(up):
    """手机已确认、调度线程还在读课表建号时，浏览器轮询看到的是“已确认”，接着轮询就拿到登录结果。"""
    c = TestClient(app)
    fid = _qr_start(c)["flow_id"]
    p = service._pending[fid]
    p.flow.stage = "done"  # 调度线程拿着 p.lock 正在 _complete_login
    r = c.post("/api/login/qr/poll", headers=ORIGIN, json={"flow_id": fid}).json()
    assert r == {"ok": False, "need": "qr", "flow_id": fid, "error": None, "qr_status": "confirmed"}
    p.flow.stage = "qr"
    up.qr = "2023002"
    assert _qr_poll(c, fid).json()["ok"] is True


def test_qr_login_keeps_saved_password(up):
    c = TestClient(app)
    _login(c, "2023001", remember=True)
    c2 = TestClient(app)
    fid = _qr_start(c2)["flow_id"]
    up.qr = "2023001"
    assert _qr_poll(c2, fid).json()["ok"]
    assert c2.get("/api/me").json()["cred_mode"] == "password"


def test_qr_login_warns_session_only_tgc(up):
    """扫码登录只拿到会话级 CASTGC：提醒用户改用账号密码登录；账号密码登录后提醒消失。"""
    c = TestClient(app)
    fid = _qr_start(c)["flow_id"]
    up.qr = "2023002"
    assert _qr_poll(c, fid).json()["ok"]
    me = c.get("/api/me").json()
    assert me["sso_short"] is True
    assert c.post("/api/refresh", headers=_csrf(c), json={}).json()["account"]["sso_short"] is True
    uid = db.connect().execute("SELECT id FROM users WHERE student_id='2023002'").fetchone()["id"]
    assert [i["sso_short"] for i in admin.schedule()["items"] if i["uid"] == uid] == [True]

    c2 = TestClient(app)
    _login(c2, "2023002")
    assert c2.get("/api/me").json()["sso_short"] is False


def test_password_login_has_long_tgc(up):
    c = TestClient(app)
    _login(c, "2023001")
    assert c.get("/api/me").json()["sso_short"] is False


def test_qr_login_with_saved_password_not_warned(up):
    # 存了密码的人过期后会自动重新登录，不用提醒
    c = TestClient(app)
    _login(c, "2023001", remember=True)
    c2 = TestClient(app)
    fid = _qr_start(c2)["flow_id"]
    up.qr = "2023001"
    assert _qr_poll(c2, fid).json()["ok"]
    assert c2.get("/api/me").json()["sso_short"] is False


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
    c.cookies.delete("kb_dev")   # 同名不同 domain 是两个桶：先删掉服务端那份，只留篡改值
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
    assert "VALARM" not in feed.text
    alarmed = TestClient(app).get(path + "?alarm=10")
    assert alarmed.status_code == 200 and alarmed.text.count("TRIGGER:-PT10M") == feed.text.count("BEGIN:VEVENT")
    assert alarmed.headers["etag"] != feed.headers["etag"]
    # 换了提醒分钟数，旧 ETag 不能命中 304
    assert TestClient(app).get(path + "?alarm=10", headers={"If-None-Match": feed.headers["etag"]}).status_code == 200
    assert "VALARM" not in TestClient(app).get(path + "?alarm=bad").text
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
    assert r.status_code == 200 and "{{" not in r.text
    soup = BeautifulSoup(r.text, "html.parser")
    refs = [t["src"] for t in soup.find_all(src=True)] + [t["href"] for t in soup.find_all(href=True)]
    local = [ref for ref in refs if ref.startswith("assets/")]
    assert local, "index.html 应该引用 assets/<版本>/ 下的前端资源"
    for ref in local:
        assert c.get("/" + ref).status_code == 200, ref
    # 所有模块都预加载，不用等 import 一层层解析
    preloaded = {t["href"].split("/", 2)[2] for t in soup.find_all("link", rel="modulepreload")}
    assert {"js/api.js", "js/views/timetable.js", "js/views/settings-email.js"} <= preloaded
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

    from app.main import _asset_version
    c = TestClient(app)
    ver = _asset_version()
    css = c.get(f"/assets/{ver}/css/tokens.css")
    assert css.status_code == 200 and "immutable" in css.headers["cache-control"]
    js = c.get(f"/assets/{ver}/js/main.js", headers={"Accept-Encoding": "gzip"})
    assert js.status_code == 200 and "immutable" in js.headers["cache-control"]
    assert js.headers.get("content-encoding") == "gzip"
    # 旧版本号拿到的是新文件，不能被长期缓存
    assert "immutable" not in c.get("/assets/000000000000/js/main.js").headers["cache-control"]
    # 部署前打开的页面仍用不带版本的旧地址
    old = c.get("/static/js/main.js")
    assert old.status_code == 200 and "must-revalidate" in old.headers["cache-control"]
    for bad in ("index.html", "../app/main.py", "js/missing.js", ".gitignore"):
        assert c.get(f"/assets/{ver}/{bad}").status_code == 404, bad
    html = c.get("/").text
    assert "theme-init.js" in html and f"assets/{ver}/js/main.js" in html
    # /api 带 CSRF token，不压缩（防 BREACH）
    assert "content-encoding" not in c.get("/api/me", headers={"Accept-Encoding": "gzip"}).headers
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
    assert _safe_log_path("/mobile/secret-token.json") == "/mobile/<redacted>"
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
    payload = r.json()
    assert r.status_code == 200 and payload["v"] == 1 and payload["semester"] == "2026-2027-1"
    # 个人信息不随只读链接走：只有配对过的设备能读（见 test_mobile_profile_*）
    assert "student" not in payload and "mail" not in payload and "学生1" not in r.text
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


def test_widget_feed_returns_only_masked_email(up, outbox):
    c = TestClient(app)
    _login(c, "2023001")
    _bind_email(c, outbox, "Me.Name@Example.com")
    path = c.post("/api/widget/rotate", headers=_csrf(c)).json()["widget_url"].removeprefix("http://testserver")
    for feed in (path, path.replace("/widget/", "/mobile/")):
        r = TestClient(app).get(feed)
        assert r.status_code == 200
        assert "mail" not in r.json() and "@example.com" not in r.text


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


def test_widget_calendar_subscription(up):
    c = TestClient(app)
    _login(c, "2023001")
    path = c.post("/api/widget/rotate", headers=_csrf(c)).json()["widget_url"].removeprefix("http://testserver")
    cal = path.removesuffix(".json") + "/calendar"
    anon = TestClient(app)
    # 还没生成 ICS 链接：顺手生成一个，网站设置里也能看到同一个
    r = anon.post(cal)
    assert r.status_code == 200 and "no-store" in r.headers["cache-control"]
    url = r.json()["ics_url"]
    assert url.startswith("http://testserver/ics/") and url.endswith(".ics")
    assert c.get("/api/me").json()["ics_url"] == url
    assert anon.get(url.removeprefix("http://testserver")).status_code == 200
    # 已有链接原样返回，不让别处的订阅失效
    assert anon.post(cal).json()["ics_url"] == url
    rotated = c.post("/api/ics/rotate", headers=_csrf(c)).json()["ics_url"]
    assert anon.post(cal).json()["ics_url"] == rotated
    assert anon.get(cal).status_code == 405
    assert anon.post("/widget/nope/calendar").status_code == 404
    # 浏览器从别站发起仍按非法来源拒绝
    assert anon.post(cal, headers={"Origin": "https://evil.example"}).status_code == 403
    # 停用后再点：重新生成一个新的
    c.post("/api/ics/disable", headers=_csrf(c))
    again = anon.post(cal).json()["ics_url"]
    assert again != rotated and c.get("/api/me").json()["ics_url"] == again


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
    # 这里只测小组件主动刷新的排队语义：清掉“下一节课”，免得周一 07:40–07:50 撞上出门前检查点
    with db.tx() as conn:
        conn.execute("UPDATE users SET next_class_at=NULL, next_class_slots='[]' WHERE student_id='2023001'")
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


# ---------- 服务器管理员页 ----------

def _admin(client, pw="admin-test-pw"):
    from app import main
    main.admin_login_by_ip._hits.clear()
    return client.post("/api/admin/login", headers=ORIGIN, json={"password": pw})


def test_admin_requires_password(up):
    c = TestClient(app)
    assert c.get("/admin").status_code == 200
    assert c.get("/api/admin/overview").status_code == 401
    assert c.get("/api/admin/schedule").status_code == 401
    assert _admin(c, "wrong").status_code == 401
    assert c.get("/api/admin/logs").status_code == 401
    # 用户的会话不能当管理员会话用
    assert _login(c, "2023001").status_code == 200
    assert c.get("/api/admin/overview").status_code == 401


def test_admin_login_rate_limited(up):
    from app import main
    main.admin_login_by_ip._hits.clear()
    c = TestClient(app)
    codes = [c.post("/api/admin/login", headers=ORIGIN, json={"password": "x"}).status_code for _ in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 429


def test_admin_dashboard(up):
    user = TestClient(app)
    assert _login(user, "2023001").status_code == 200
    user.get("/api/me")
    c = TestClient(app)
    r = _admin(c)
    assert r.status_code == 200
    csrf = r.json()["csrf"]
    assert c.get("/api/admin/me").json()["csrf"] == csrf

    o = c.get("/api/admin/overview").json()
    assert o["users"] == 1 and o["online"]["users"] >= 1 and o["online"]["sessions"] >= 1

    s = c.get("/api/admin/schedule").json()
    item = s["items"][0]
    assert item["student"] == "2023*01" and item["name"] == "学**"
    assert "2023001" not in str(s)          # 学号脱敏
    assert item["status"] == "ok" and item["next_refresh_at"]

    refreshes = c.get("/api/admin/refreshes").json()
    assert refreshes and refreshes[0]["trigger"] == "login"

    logs = c.get("/api/admin/logs").json()
    assert any("admin login" in i["message"] for i in logs["items"])
    assert all(i["seq"] > logs["seq"] for i in c.get("/api/admin/logs", params={"after": logs["seq"]}).json()["items"])

    # 写操作要 CSRF
    uid = item["uid"]
    assert c.post("/api/admin/request-refresh", headers=ORIGIN, json={"uid": uid}).status_code == 403
    assert c.post("/api/admin/request-refresh", headers={**ORIGIN, "X-CSRF-Token": csrf},
                  json={"uid": uid}).status_code == 200
    assert c.get("/api/admin/schedule").json()["items"][0]["pending"] is True

    assert c.post("/api/admin/logout", headers={**ORIGIN, "X-CSRF-Token": csrf}).status_code == 200
    assert c.get("/api/admin/overview").status_code == 401


def test_admin_disabled_without_hash(up, monkeypatch):
    from app import admin
    monkeypatch.setattr(admin, "ENABLED", False)
    c = TestClient(app)
    assert c.get("/admin").status_code == 404
    assert _admin(c).status_code == 404
    assert c.get("/api/admin/overview").status_code == 404


def test_admin_template_not_served_raw(up):
    c = TestClient(app)
    assert c.get("/static/admin.html").status_code == 404


def test_admin_password_hash_roundtrip():
    from app import admin
    stored = admin.hash_password("correct horse", iterations=1000)
    assert admin.verify_password("correct horse", stored)
    assert not admin.verify_password("wrong", stored)
    assert not admin.verify_password("x", "garbage")
    assert admin.mask_id("2023012345") == "2023****45"


# ---------- 小组件代抓：手机用自己的网络向教务要课表 ----------

COURSE_HTML = ('<script>var personId = 1; var currentSemester = {"id": 161, "nameZh": "x"};</script>'
               '<select id="allSemesters"><option value="161">2026-2027-1</option></select>')


def _print_data(code="2023001", room="B202"):
    import json as _json
    return _json.dumps({"studentTableVms": [{"name": "学生1", "code": code, "activities": [{
        "courseName": "高数", "teachers": [{"name": "T"}], "weekday": 1, "startUnit": 1, "endUnit": 2,
        "weeksStr": "1~16", "weekIndexes": list(range(1, 17)), "room": room, "startTime": "8:20", "endTime": "9:45"}]}]})


def _jar(sid):
    from app import crypto
    uid = service_uid(sid)
    row = db.connect().execute("SELECT enc_cookies FROM users WHERE id=?", (uid,)).fetchone()
    return crypto.decrypt_json(row["enc_cookies"], "cookies", uid)


def _give_jwxt_session(sid, value="jw-1"):
    from app import crypto
    uid = service_uid(sid)
    jar = [c for c in _jar(sid) if c["name"] != "SESSION"] + [
        {"name": "SESSION", "value": value, "domain": "jwxt.nufe.edu.cn", "path": "/student", "secure": True,
         "expires": None}]
    with db.tx() as c:
        c.execute("UPDATE users SET enc_cookies=? WHERE id=?", (crypto.encrypt_json(jar, "cookies", uid), uid))


@pytest.fixture
def widget_user(up):
    c = TestClient(app)
    _login(c, "2023001")
    _give_jwxt_session("2023001")
    path = c.post("/api/widget/rotate", headers=_csrf(c)).json()["widget_url"].removeprefix("http://testserver")
    with db.tx() as conn:  # 数据早就过期了：打开小组件就该刷新
        conn.execute("UPDATE users SET last_success_at=? WHERE student_id='2023001'", (db.now() - 30 * 86400,))
    return c, path


def _upload(path, body, **kw):
    return TestClient(app).post(path.removesuffix(".json") + "/upload", json=body, **kw)


def _keyed_get(path):
    """设置页复制出来的 iOS 脚本：带代抓密钥请求。"""
    token = path.removeprefix("/widget/").removesuffix(".json")
    return TestClient(app).get(path, headers={"X-Widget-Key": crypto.widget_fetch_key(token)})


APK = b"fake-apk-bytes"


def _github_release(tag, pre=False, data=APK, **over):
    return {"tag_name": tag, "prerelease": pre, "draft": False, "assets": [{
        "name": f"app-{tag}.apk", "size": len(data), "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
        "browser_download_url": f"https://github.com/LokmenoWer/nufe-kebiao/releases/download/{tag}/app-{tag}.apk",
    }], **over}


@pytest.fixture
def github(monkeypatch, tmp_path):
    """假的 GitHub：测试改 state 里的内容；calls 记请求，broken 模拟连不上。"""
    state = SimpleNamespace(releases=[_github_release("1.3.0-beta.1", pre=True), _github_release("1.2.0")],
                            apk=APK, calls=[], broken=False, tmp_cache=tmp_path)

    class Reply:
        def __init__(self, url):
            self.url = url

        def raise_for_status(self):
            pass

        def json(self):
            return state.releases

        def iter_content(self, _size):
            yield state.apk

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    def get(url, **_kw):
        state.calls.append(url)
        if state.broken:
            raise requests.ConnectionError
        return Reply(url)

    monkeypatch.setattr(android_releases.requests, "get", get)
    monkeypatch.setattr(android_releases, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(android_releases, "_releases", [])
    monkeypatch.setattr(android_releases, "_next_refresh", 0.0)
    monkeypatch.setattr(android_releases, "_last_ok_at", None)
    monkeypatch.setattr(android_releases, "_last_error_at", None)
    monkeypatch.setattr(android_releases, "_last_error", None)
    return state


def test_admin_android_view(github, up):
    user = TestClient(app)
    assert _login(user, "2023001").status_code == 200
    uid = db.connect().execute("SELECT id FROM users").fetchone()["id"]
    now = db.now()
    with db.tx() as c:
        c.execute("UPDATE users SET widget_token_hash='t', widget_fetch_ok_at=?, widget_fetch_fails=0 WHERE id=?",
                  (now - 60, uid))
        c.execute("INSERT INTO mobile_devices(id, user_id, public_key, name, created_at, last_seen_at) "
                  "VALUES('d1',?,?,'Pixel 9',?,?)", (uid, b"secret-key", now, now))
    c = TestClient(app)
    assert c.get("/api/admin/android").status_code == 401
    assert _admin(c).status_code == 200

    (github.tmp_cache / "1.2.0.apk").write_bytes(APK)
    a = c.get("/api/admin/android").json()
    assert a["phone"] == {"linked": 1, "enabled": 1, "healthy": 1, "failing": 0, "running": 0}
    assert a["device_stats"] == {"devices": 1, "users": 1, "active_7d": 1}
    assert a["devices"][0]["name"] == "Pixel 9" and a["devices"][0]["student"] == "2023*01"
    assert "2023001" not in str(a) and "d1" not in str(a) and "secret-key" not in str(a)
    assert a["releases"]["latest_stable"] == "1.2.0" and a["releases"]["failing"] is False
    assert {r["tag"]: r["mirrored"] for r in a["releases"]["releases"]} == {"1.3.0-beta.1": False, "1.2.0": True}

    item = c.get("/api/admin/schedule").json()["items"][0]
    assert item["devices"] == 1 and item["phone"]["linked"] is True and item["phone"]["waiting"] is False

    o = c.get("/api/admin/overview").json()
    assert o["device_stats"]["devices"] == 1 and o["phone"]["healthy"] == 1 and "alive" in o["scheduler"]


def test_admin_release_sync_failure_keeps_old_list(github, up, monkeypatch):
    c = TestClient(app)
    assert _admin(c).status_code == 200
    assert c.get("/api/admin/android").json()["releases"]["failing"] is False
    github.broken = True
    monkeypatch.setattr(android_releases, "_next_refresh", 0.0)
    rel = c.get("/api/admin/android").json()["releases"]
    assert rel["failing"] is True and rel["last_error"] == "ConnectionError" and len(rel["releases"]) == 2
    assert c.get("/api/admin/overview").json()["release_sync"]["failing"] is True
    github.broken = False
    monkeypatch.setattr(android_releases, "_next_refresh", 0.0)
    assert c.get("/api/admin/android").json()["releases"]["failing"] is False


def test_admin_shows_user_waiting_for_phone(up):
    assert _login(TestClient(app), "2023001").status_code == 200
    uid = db.connect().execute("SELECT id FROM users").fetchone()["id"]
    with db.tx() as c:
        c.execute("UPDATE users SET widget_token_hash='t', widget_fetch_pending_until=? WHERE id=?",
                  (db.now() + 300, uid))
    service.request_refresh(uid, "测试")
    item = admin.schedule()["items"][0]
    assert item["phone"]["running"] is True and item["phone"]["waiting"] is True and item["score"] is None


def test_admin_refreshes_filter(up):
    assert _login(TestClient(app), "2023001").status_code == 200
    uid = db.connect().execute("SELECT id FROM users").fetchone()["id"]
    with db.tx() as c:
        c.execute("INSERT INTO refresh_log(user_id, at, trigger, ok, message) VALUES(?,?,'widget',0,'x')",
                  (uid, db.now()))
    c = TestClient(app)
    assert _admin(c).status_code == 200
    assert {r["trigger"] for r in c.get("/api/admin/refreshes").json()} == {"login", "widget"}
    assert [r["trigger"] for r in c.get("/api/admin/refreshes", params={"trigger": "login"}).json()] == ["login"]
    assert [r["trigger"] for r in c.get("/api/admin/refreshes", params={"failed": "true"}).json()] == ["widget"]
    assert c.get("/api/admin/refreshes", params={"trigger": "nope"}).status_code == 422


def test_android_release_feed_mirrors_github(github, up):
    r = TestClient(app).get("/android/releases.json")
    assert r.status_code == 200 and "public" in r.headers["cache-control"]
    feed = r.json()
    assert [x["tag_name"] for x in feed] == ["1.3.0-beta.1", "1.2.0"]
    assert feed[0]["prerelease"] is True and feed[0]["assets"][0]["name"].endswith(".apk")
    assert feed[1]["html_url"].endswith("/android/apk/1.2.0.apk")
    # 不是每个请求都去问 GitHub
    TestClient(app).get("/android/releases.json")
    assert len(github.calls) == 1


def test_android_release_feed_skips_unusable_and_survives_outage(github, up, monkeypatch):
    bad_host = _github_release("9.0.0")
    bad_host["assets"][0]["browser_download_url"] = "https://evil.example/app.apk"
    no_digest = _github_release("9.0.1")
    no_digest["assets"][0].pop("digest")
    github.releases = [bad_host, no_digest, _github_release("android-v1.0.0-preview"),
                       _github_release("9.0.2", draft=True), _github_release("1.2.0")]
    assert [x["tag_name"] for x in TestClient(app).get("/android/releases.json").json()] == ["1.2.0"]
    # GitHub 连不上：继续给旧结果，隔一会儿才再试
    github.broken = True
    monkeypatch.setattr(android_releases, "_next_refresh", 0.0)
    calls = len(github.calls)
    assert [x["tag_name"] for x in TestClient(app).get("/android/releases.json").json()] == ["1.2.0"]
    TestClient(app).get("/android/releases.json")
    assert len(github.calls) == calls + 1


def test_android_apk_mirror_downloads_once_and_verifies(github, up, tmp_path):
    c = TestClient(app)
    r = c.get("/android/apk/1.2.0.apk")
    assert r.status_code == 200 and r.content == APK
    assert r.headers["content-type"] == "application/vnd.android.package-archive"
    assert "app-1.2.0.apk" in r.headers["content-disposition"] and "content-encoding" not in {
        k for k, v in r.headers.items() if v == "gzip"}
    downloads = [u for u in github.calls if u.endswith(".apk")]
    assert len(downloads) == 1
    c.get("/android/apk/1.2.0.apk")
    assert len([u for u in github.calls if u.endswith(".apk")]) == 1
    # 只认发布列表里的版本，路径里塞不进别的东西
    assert c.get("/android/apk/6.6.6.apk").status_code == 404
    assert c.get("/android/apk/..%2F..%2Fkebiao.apk").status_code == 404
    # 校验不过：不留文件，不发给用户
    github.apk = b"tampered"
    assert c.get("/android/apk/1.3.0-beta.1.apk").status_code == 502
    assert not (tmp_path / "1.3.0-beta.1.apk").exists() and not list(tmp_path.glob("*.tmp"))
    # 从发布列表里消失的版本：缓存删掉
    (tmp_path / "0.0.1.apk").write_bytes(b"old")
    android_releases._next_refresh = 0.0
    c.get("/android/releases.json")
    assert not (tmp_path / "0.0.1.apk").exists() and (tmp_path / "1.2.0.apk").exists()


def test_android_latest_apk_points_at_stable(github, up):
    r = TestClient(app).get("/android/latest.apk", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "apk/1.2.0.apk"
    github.releases = [_github_release("1.3.0-beta.1", pre=True)]
    android_releases._next_refresh = 0.0
    assert TestClient(app).get("/android/latest.apk", follow_redirects=False).status_code == 404


def test_mobile_feed_shares_token_without_fetch_task(widget_user, up):
    c, path = widget_user
    links = c.get("/api/me").json()
    token = path.removeprefix("/widget/").removesuffix(".json")
    assert links["mobile_url"] == f"http://testserver/mobile/{token}.json"
    feed = TestClient(app).get(f"/mobile/{token}.json")
    assert feed.status_code == 200
    data = feed.json()
    assert data["today"] == TestClient(app).get(path).json()["today"]
    # 代抓任务（含教务 cookie）要设备签名才下发，只有链接领不到
    assert data["refresh"]["pending"] is True and "no-store" in feed.headers["cache-control"]
    assert "fetch" not in data and "delegate" not in data and "SESSION=jw-1" not in feed.text
    assert TestClient(app).get("/mobile/nope.json").status_code == 404
    c.post("/api/widget/disable", headers=_csrf(c))
    assert TestClient(app).get(f"/mobile/{token}.json").status_code == 404
    assert c.get("/api/me").json()["mobile_url"] is None


def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _Phone:
    """测试里的安卓 App：一把 P-256 设备密钥，会配对、会签名。"""

    def __init__(self, c):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.token = c.post("/api/widget/rotate", headers=_csrf(c)).json()["widget_url"].split("/")[-1][:-5]
        self.device_id = None

    def public(self):
        der = self.key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        return base64.b64encode(der).decode()

    def pair(self, code, **kw):
        r = TestClient(app).post(f"/mobile/{self.token}/pair", json={
            "code": code, "public_key": kw.pop("public_key", self.public()), "name": "Test Phone"}, **kw)
        if r.status_code == 200:
            self.device_id = r.json()["device_id"]
        return r

    def signed(self, at=None, key=None, device_id=None):
        device_id = device_id or self.device_id
        ts = str(int(at if at is not None else time.time()))
        message = mobile_devices.signed_message(self.token, device_id, ts)
        sig = (key or self.key).sign(message, ec.ECDSA(hashes.SHA256()))
        return {"X-Device-Id": device_id, "X-Device-Time": ts, "X-Device-Signature": base64.b64encode(sig).decode()}

    def profile(self, **kw):
        return TestClient(app).get(f"/mobile/{self.token}/profile", headers=self.signed(**kw))


def _pair_code(c):
    r = c.post("/api/mobile/pair", headers=_csrf(c))
    assert r.status_code == 200 and "no-store" in r.headers["cache-control"]
    url = r.json()["pair_url"]
    assert url.startswith("nufekebiao://setup?url=http%3A%2F%2Ftestserver%2Fmobile%2F")
    return url.split("&pair=")[1]


def test_mobile_local_fetch_only_for_signed_devices(widget_user, up):
    c, _path = widget_user
    phone = _Phone(c)
    feed = f"/mobile/{phone.token}.json"
    anon = TestClient(app)
    # 只拿到链接：任务和其中的教务 cookie 都拿不到，也不占着任务（服务器照常刷）
    r = anon.get(feed)
    assert "fetch" not in r.json() and "SESSION=jw-1" not in r.text and "delegate" not in r.json()
    assert service.widget_refresh_state(service_uid("2023001"))["pending"] is True
    assert phone.pair(_pair_code(c)).status_code == 200
    # 冒充的钥匙、过期的时间戳：同样当没绑定
    fake = anon.get(feed, headers=phone.signed(key=ec.generate_private_key(ec.SECP256R1())))
    assert "fetch" not in fake.json()
    stale = anon.get(feed, headers=phone.signed(at=time.time() - mobile_devices.SIG_SKEW - 60))
    assert stale.status_code == 200 and "fetch" not in stale.json()
    # 绑定过的设备：和 iOS 一样领任务，只含教务 cookie，不缓存
    r = anon.get(feed, headers=phone.signed())
    data = r.json()
    task = data["fetch"]
    assert data["delegate"] is True and data["poll_after"] >= 60 and "no-store" in r.headers["cache-control"]
    assert "CASTGC" not in r.text and all(s["cookie"] == "SESSION=jw-1" for s in task["steps"])
    assert anon.get(feed, headers=phone.signed()).json()["fetch"]["id"] == task["id"]
    # 手机抓不到：如实上报，服务器兜底
    up_url = f"/mobile/{phone.token}/upload"
    assert anon.post(up_url, json={"id": "nope", "error": "network"}).status_code == 400
    r = anon.post(up_url, json={"id": task["id"], "error": "network"})
    assert r.status_code == 200 and r.json()["status"] == "fallback"
    assert "student" not in r.json()["widget"] and "fetch" not in r.json()["widget"]
    assert anon.post(up_url, json={"id": task["id"]}, headers={"Origin": "https://evil.example"}).status_code == 403
    # 撤销设备后不再发任务
    c.post(f"/api/mobile/devices/{phone.device_id}/revoke", headers=_csrf(c))
    assert "fetch" not in anon.get(feed, headers=phone.signed()).json()


def test_mobile_local_fetch_upload_saves_timetable(widget_user, up):
    c, _path = widget_user
    phone = _Phone(c)
    assert phone.pair(_pair_code(c)).status_code == 200
    anon = TestClient(app)
    task = anon.get(f"/mobile/{phone.token}.json", headers=phone.signed()).json()["fetch"]
    r = anon.post(f"/mobile/{phone.token}/upload", json={
        "id": task["id"], "course_html": COURSE_HTML, "print_data": _print_data(),
        "final_urls": [s["url"] for s in task["steps"]]})
    assert r.status_code == 200 and r.json()["ok"] is True
    row = db.connect().execute("SELECT widget_fetch_ok_at FROM users WHERE student_id='2023001'").fetchone()
    assert row["widget_fetch_ok_at"] is not None


def test_mobile_profile_needs_paired_device(up):
    c = TestClient(app)
    _login(c, "2023001")
    phone = _Phone(c)
    anon = TestClient(app)
    # 只拿到链接：课表能读，个人信息读不到，也绑不上
    assert anon.get(f"/mobile/{phone.token}.json").status_code == 200
    assert anon.get(f"/mobile/{phone.token}/profile").status_code == 401
    assert phone.pair("guess-" * 8).status_code == 400
    code = _pair_code(c)
    assert TestClient(app).post("/mobile/nope/pair", json={"code": code, "public_key": phone.public()}).status_code == 404
    assert phone.pair(code, public_key="bm90LWEta2V5").status_code == 400  # 公钥不对不消耗配对码
    rsa = base64.b64encode(rsa_key().public_key().public_bytes(
        Encoding.DER, PublicFormat.SubjectPublicKeyInfo)).decode()
    assert phone.pair(code, public_key=rsa).status_code == 400
    assert phone.pair(code, headers={"Origin": "https://evil.example"}).status_code == 403
    assert phone.pair(code).status_code == 200
    assert phone.pair(code).status_code == 400  # 配对码只能用一次
    r = phone.profile()
    assert r.status_code == 200 and "no-store" in r.headers["cache-control"]
    assert r.json()["student"]["name"] == "学生1" and r.json()["student"]["code"] == "2023001"
    assert "credits" not in r.json()["student"] and r.json()["mail"] == {"bound": False, "email": None}
    # 别的密钥冒充、请求里没签名：都不行
    assert phone.profile(key=ec.generate_private_key(ec.SECP256R1())).status_code == 401
    assert "server_time" not in phone.profile(key=ec.generate_private_key(ec.SECP256R1()), at=1).json()
    # 手机时钟不准：真设备才告诉它校正
    stale = phone.profile(at=time.time() - mobile_devices.SIG_SKEW - 60)
    assert stale.status_code == 401 and abs(stale.json()["server_time"] - time.time()) < 5


def test_mobile_pair_code_expires_and_is_per_user(up):
    c, other = TestClient(app), TestClient(app)
    _login(c, "2023001")
    _login(other, "2023002")
    phone, stranger = _Phone(c), _Phone(other)
    code = _pair_code(c)
    assert stranger.pair(code).status_code == 400  # 别人的配对码
    old = code
    code = _pair_code(c)
    assert phone.pair(old).status_code == 400  # 新码发出后旧码作废
    with db.tx() as conn:
        conn.execute("UPDATE mobile_pairings SET expires_at=?", (time.time() - 1,))
    assert phone.pair(code).status_code == 400
    assert phone.pair(_pair_code(c)).status_code == 200
    assert stranger.profile(device_id=phone.device_id).status_code == 401  # 别人的设备 id


def test_mobile_device_management_and_revocation(up):
    c = TestClient(app)
    _login(c, "2023001")
    phone = _Phone(c)
    assert c.post("/api/mobile/pair", headers=ORIGIN).status_code == 403  # 没带 CSRF
    assert phone.pair(_pair_code(c)).status_code == 200
    devices = c.get("/api/mobile/devices").json()["devices"]
    assert [d["name"] for d in devices] == ["Test Phone"] and "public_key" not in devices[0]
    # 同一把钥匙重新配对只留一条
    assert phone.pair(_pair_code(c)).status_code == 200
    assert len(c.get("/api/mobile/devices").json()["devices"]) == 1
    # 撤销：个人信息读不到了，课表还能读
    assert c.post("/api/mobile/devices/nope/revoke", headers=_csrf(c)).status_code == 404
    device = phone.device_id
    assert c.post(f"/api/mobile/devices/{device}/revoke", headers=_csrf(c)).status_code == 200
    assert phone.profile().status_code == 401
    assert TestClient(app).get(f"/mobile/{phone.token}.json").status_code == 200
    # 数量上限，满了不消耗配对码
    phones = []
    for _ in range(mobile_devices.MAX_DEVICES):
        extra = _Phone.__new__(_Phone)
        extra.key, extra.token, extra.device_id = ec.generate_private_key(ec.SECP256R1()), phone.token, None
        assert extra.pair(_pair_code(c)).status_code == 200
        phones.append(extra)
    assert phone.pair(_pair_code(c)).status_code == 400
    # 重新生成 / 停用链接：所有设备和配对码都失效
    code = _pair_code(c)
    c.post("/api/widget/rotate", headers=_csrf(c))
    assert c.get("/api/mobile/devices").json()["devices"] == []
    with db.tx() as conn:
        assert conn.execute("SELECT COUNT(*) FROM mobile_pairings").fetchone()[0] == 0
    assert phones[0].profile().status_code == 404 and phones[0].pair(code).status_code == 404
    c.post("/api/widget/disable", headers=_csrf(c))
    assert c.post("/api/mobile/pair", headers=_csrf(c)).status_code == 404


def test_mobile_bind_sends_security_mail(up, outbox):
    c = TestClient(app)
    _login(c, "2023001")
    _bind_email(c, outbox, "me@example.com")
    phone = _Phone(c)
    assert phone.pair(_pair_code(c)).status_code == 200
    to, subject, body = outbox[-1]
    assert to == "me@example.com" and "新设备" in subject and "Test Phone" in body
    assert phone.profile().json()["mail"] == {"bound": True, "email": "m***@example.com"}


def test_widget_feed_poll_after_only_when_delegating(widget_user, up):
    c, path = widget_user
    assert _keyed_get(path).json()["poll_after"] >= 60
    assert "poll_after" not in TestClient(app).get(path.replace("/widget/", "/mobile/")).json()  # 安卓 App 不代抓
    service.set_widget_fetch(service_uid("2023001"), False)
    assert _keyed_get(path).json()["poll_after"] is None


def test_widget_link_alone_gets_no_fetch_task(widget_user, up):
    c, path = widget_user
    token = path.removeprefix("/widget/").removesuffix(".json")
    anon = TestClient(app)
    # 只有链接、或密钥不对：没有任务和教务 cookie，也不占着任务，课表照常由服务器刷
    for headers in ({}, {"X-Widget-Key": "wrong"}, {"X-Widget-Key": "错".encode()},
                    {"X-Widget-Key": crypto.widget_fetch_key("other-token")}):
        r = anon.get(path, headers=headers)
        data = r.json()
        assert r.status_code == 200 and "fetch" not in data and "SESSION=jw-1" not in r.text
        assert "delegate" not in data and "poll_after" not in data and data["fetch_key_required"] is True
    assert db.connect().execute("SELECT COUNT(*) FROM widget_tasks").fetchone()[0] == 0
    assert _row("2023001", "widget_fetch_pending_until")[0] is None
    assert data["refresh"]["pending"] is True
    # 链接换不到密钥：自我更新的脚本里没有，也不能凭 token 算出来
    key = crypto.widget_fetch_key(token)
    assert key not in anon.get(f"/widget/{token}.js").text and key not in r.text
    assert 'const FETCH_KEY = "__WIDGET_FETCH_KEY__"' in anon.get(f"/widget/{token}.js").text
    # 登录后从设置页复制的脚本带密钥，带上就能领任务
    script = c.get("/api/widget/script").text
    assert f'const FETCH_KEY = "{key}"' in script
    keyed = anon.get(path, headers={"X-Widget-Key": key}).json()
    assert keyed["fetch"]["steps"][0]["cookie"] == "SESSION=jw-1" and "fetch_key_required" not in keyed
    # /mobile 不认这把密钥，仍然只认设备签名
    assert "fetch" not in anon.get(path.replace("/widget/", "/mobile/"), headers={"X-Widget-Key": key}).json()
    # 用户关了手机代抓就不提示重新复制
    service.set_widget_fetch(service_uid("2023001"), False)
    assert anon.get(path).json()["fetch_key_required"] is False
    # 重新生成链接后，旧密钥和新链接配不上
    service.set_widget_fetch(service_uid("2023001"), True)
    new = c.post("/api/widget/rotate", headers=_csrf(c)).json()["widget_url"].removeprefix("http://testserver")
    assert "fetch" not in anon.get(new, headers={"X-Widget-Key": key}).json()
    assert "fetch" in _keyed_get(new).json()


def test_widget_delegated_fetch_roundtrip(widget_user, up):
    c, path = widget_user
    feed = _keyed_get(path)
    data = feed.json()
    task = data["fetch"]
    assert data["delegate"] is True and data["refresh"]["pending"] is True
    assert "no-store" in feed.headers["cache-control"]
    # 只下发教务域名的 cookie：统一身份认证的 CASTGC 不出服务器
    assert "tgc-" not in feed.text and "CASTGC" not in feed.text
    assert [s["kind"] for s in task["steps"]][:2] == ["course", "print"]
    assert all(s["cookie"] == "SESSION=jw-1" for s in task["steps"])
    assert "/semester/161/print-data" in task["steps"][1]["url"]
    # 还没交回来时再来要：同一个任务，不顺延
    assert _keyed_get(path).json()["fetch"]["id"] == task["id"]
    # 手机抓着的时候服务器不抢
    uid = service_uid("2023001")
    assert uid not in [u for _s, u in service.candidates()]

    fetches = up.fetches
    r = _upload(path, {"id": task["id"], "course_html": COURSE_HTML, "print_data": _print_data(),
                       "final_urls": ["https://jwxt.nufe.edu.cn/student/for-std/course-table"],
                       "set_cookies": [{"name": "SESSION", "value": "jw-2"}, {"name": "EVIL", "value": "x"}]})
    out = r.json()
    assert r.status_code == 200 and out["ok"] is True, out
    assert out["widget"]["v"] == 1 and out["widget"]["refresh"]["pending"] is False
    assert up.fetches == fetches  # 服务器没有访问学校
    assert c.get("/api/timetable").json()["activities"][0]["room"] == "B202"
    assert db.connect().execute("SELECT trigger, ok FROM refresh_log WHERE user_id=? ORDER BY id DESC LIMIT 1",
                                (uid,)).fetchone()[:] == ("widget", 1)
    row = _row("2023001", "widget_fetch_ok_at, widget_fetch_fails, widget_fetch_pending_until")
    assert row[0] and row[1] == 0 and row[2] is None
    # cookie：已有的教务 cookie 换成新值；没见过的名字不收；CASTGC 原样
    jar = {x["name"]: x["value"] for x in _jar("2023001")}
    assert jar["SESSION"] == "jw-2" and "EVIL" not in jar and jar["CASTGC"] == "tgc-2023001"
    # 同一个任务只能交一次
    again = _upload(path, {"id": task["id"], "course_html": COURSE_HTML, "print_data": _print_data()})
    assert again.status_code == 400
    # 刚更新过：不再发任务
    assert "fetch" not in _keyed_get(path).json()


def test_widget_upload_expired_session_falls_back_to_server(widget_user, up):
    _c, path = widget_user
    task = _keyed_get(path).json()["fetch"]
    r = _upload(path, {"id": task["id"], "final_urls": ["https://authserver.nufe.edu.cn/authserver/login?service=x"]})
    assert r.json()["status"] == "fallback" and r.json()["widget"]["refresh"]["pending"] is True
    assert _row("2023001", "widget_fetch_fails")[0] == 1
    # 交给服务器：调度器马上就会选中他
    assert service_uid("2023001") in [u for _s, u in service.candidates()]
    fetches = up.fetches
    scheduler.run_once()
    assert up.fetches == fetches + 1 and _row("2023001", "status")[0] == "ok"


def test_widget_upload_rejections(widget_user, up):
    _c, path = widget_user
    task = _keyed_get(path).json()["fetch"]
    assert _upload(path, {"id": "nope"}).status_code == 400
    assert _upload(path, {"id": task["id"]}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert TestClient(app).post("/widget/nope/upload", json={"id": task["id"]}).status_code == 404
    big = TestClient(app).post(path.removesuffix(".json") + "/upload", content=b"x" * (5 * 1024 * 1024),
                               headers={"Content-Type": "application/json"})
    assert big.status_code == 413
    # 解析不了：拒绝并交给服务器
    bad = _upload(path, {"id": task["id"], "course_html": "<html>login</html>", "print_data": _print_data()})
    assert bad.status_code == 400 and _row("2023001", "widget_fetch_fails")[0] == 1
    # 别人的课表
    task = _keyed_get(path).json()["fetch"]
    other = _upload(path, {"id": task["id"], "course_html": COURSE_HTML, "print_data": _print_data(code="2023999")})
    assert other.status_code == 400
    assert c_room(_c) == "A101"


@pytest.mark.parametrize("print_data", [
    _print_data(code=""),
    json.dumps({"studentTableVms": []}),
    json.dumps({"studentTableVms": [{"activities": [{"weekday": 1, "startUnit": 1, "courseName": "伪造"}]}]}),
])
def test_widget_upload_without_student_code_is_rejected(widget_user, up, monkeypatch, print_data):
    c, path = widget_user
    alerted = []
    monkeypatch.setattr(service, "alert_classmates", lambda *a: alerted.append(a) or 0)
    task = _keyed_get(path).json()["fetch"]
    r = _upload(path, {"id": task["id"], "course_html": COURSE_HTML, "print_data": print_data})
    assert r.status_code == 400
    # 课表没被改，也没有惊动同班同学；任务作废后仍在排队，由服务器兜底
    assert c_room(c) == "A101" and not alerted
    uid = service_uid("2023001")
    assert _row("2023001", "widget_fetch_fails")[0] == 1 and service.widget_refresh_state(uid)["pending"] is True


def c_room(client):
    return client.get("/api/timetable").json()["activities"][0]["room"]


def test_scheduler_waits_for_healthy_widget(widget_user, up):
    uid = service_uid("2023001")
    now = db.now()
    with db.tx() as conn:
        conn.execute("UPDATE users SET last_success_at=?, refresh_requested_at=?, refresh_request_reason='x' WHERE id=?",
                     (now - 3600, now - 60, uid))
    due = lambda: uid in [u for _s, u in service.candidates(now)]  # noqa: E731
    assert due()  # 从没代抓成功过：服务器照常刷
    with db.tx() as conn:
        conn.execute("UPDATE users SET widget_fetch_ok_at=? WHERE id=?", (now - 600, uid))
    assert not due() and not service.still_due(uid, now)  # 小组件最近都能代抓：请求后先等它 10 分钟
    assert due() is False and uid in [u for _s, u in service.candidates(now + 700)]
    with db.tx() as conn:
        conn.execute("UPDATE users SET widget_fetch_fails=1 WHERE id=?", (uid,))
    assert due()  # 上次失败了：不等
    with db.tx() as conn:
        conn.execute("UPDATE users SET widget_fetch_fails=0, widget_fetch=0 WHERE id=?", (uid,))
    assert due()  # 用户关掉了


def test_widget_fetch_pref(widget_user, up):
    c, path = widget_user
    assert c.get("/api/me").json()["widget_fetch"] is True
    assert c.post("/api/widget/fetch-pref", headers=_csrf(c), json={"enabled": False}).json() == {"widget_fetch": False}
    data = _keyed_get(path).json()
    assert "fetch" not in data and data["delegate"] is False and data["refresh"]["pending"] is True
    assert c.get("/api/me").json()["widget_fetch"] is False
    assert c.post("/api/widget/fetch-pref", headers=ORIGIN, json={"enabled": True}).status_code == 403


def test_widget_task_lapses_count_as_failure(widget_user, up):
    _c, path = widget_user
    task = _keyed_get(path).json()["fetch"]
    with db.tx() as conn:
        conn.execute("UPDATE widget_tasks SET expires_at=? WHERE task_id=?", (db.now() - 1, task["id"]))
    assert _keyed_get(path).json()["fetch"]["id"] != task["id"]
    assert _row("2023001", "widget_fetch_fails")[0] == 1


def test_widget_no_task_without_jwxt_session(up):
    c = TestClient(app)
    _login(c, "2023001")  # 只有 CASTGC：续期只能服务器做
    path = c.post("/api/widget/rotate", headers=_csrf(c)).json()["widget_url"].removeprefix("http://testserver")
    with db.tx() as conn:
        conn.execute("UPDATE users SET last_success_at=? WHERE student_id='2023001'", (db.now() - 30 * 86400,))
    assert "fetch" not in _keyed_get(path).json()


def _qr_login(sid):
    c = TestClient(app)
    fid = _qr_start(c)["flow_id"]
    FakeFlow.up.qr = sid
    assert _qr_poll(c, fid).json()["ok"]
    return c, db.connect().execute("SELECT id FROM users WHERE student_id=?", (sid,)).fetchone()["id"]


def _keepalive_row(uid):
    return db.connect().execute(
        "SELECT keepalive_at, sso_touched_at, sso_lost_at, refresh_requested_at FROM users WHERE id=?", (uid,)).fetchone()


def _age_keepalive(seconds=None):
    with db.tx() as conn:
        conn.execute("UPDATE users SET keepalive_at=keepalive_at-?", (seconds or service.KEEPALIVE_EVERY + 1,))


def test_keepalive_covers_all_logins_qr_first(up, monkeypatch):
    c1 = TestClient(app)
    _login(c1, "2023001", remember=True)               # 账号密码登录、存了密码：教务会话一样要保
    _, qr_uid = _qr_login("2023002")                    # 扫码、没存密码：最不经放，排前面
    pwd_uid = db.connect().execute("SELECT id FROM users WHERE student_id='2023001'").fetchone()["id"]
    assert service.keepalive_candidates() == []         # 刚登录，不到间隔
    _age_keepalive()
    with db.tx() as conn:  # 账号密码用户更久没保活，也还是扫码用户先
        conn.execute("UPDATE users SET keepalive_at=keepalive_at-600 WHERE id=?", (pwd_uid,))
    assert service.keepalive_candidates() == [qr_uid, pwd_uid]
    monkeypatch.setattr(scheduler._stop, "wait", lambda t: False)
    assert scheduler.keepalive_once(_budget(tokens=1)) == 1   # 预算只够一个：保扫码的
    assert up.touches == [("jwxt", "2023002"), ("sso", "2023002")]
    assert scheduler.keepalive_once(_budget()) == 1
    assert up.touches[2:] == [("jwxt", "2023001"), ("sso", "2023001")]
    for uid in (qr_uid, pwd_uid):
        row = _keepalive_row(uid)
        assert row["keepalive_at"] > db.now() - 5 and row["sso_touched_at"] > db.now() - 5 and row["sso_lost_at"] is None
    assert service.keepalive_candidates() == []


def test_password_relogin_restarts_sso_keepalive(up):
    c = TestClient(app)
    _login(c, "2023001", remember=True)
    uid = db.connect().execute("SELECT id FROM users WHERE student_id='2023001'").fetchone()["id"]
    with db.tx() as conn:
        conn.execute("UPDATE users SET sso_lost_at=?, sso_touched_at=1 WHERE id=?", (db.now(), uid))
    up.expired.add("2023001")  # 教务过期、CASTGC 也续不上：用保存的密码重新登录
    assert service.refresh_user(uid, "scheduled")["ok"]
    row = _keepalive_row(uid)
    assert row["sso_lost_at"] is None and row["sso_touched_at"] > db.now() - 5


def test_keepalive_records_lost_sso_and_keeps_jwxt_alive(up):
    _, uid = _qr_login("2023002")
    up.sso_dead.add("2023002")
    _age_keepalive()
    assert service.keepalive_user(uid) == {"jwxt": True, "sso": False}
    assert _keepalive_row(uid)["sso_lost_at"] is not None
    # 认证中心不认了：之后只保教务会话
    up.touches.clear()
    _age_keepalive()
    assert service.keepalive_user(uid) == {"jwxt": True, "sso": None}
    assert up.touches == [("jwxt", "2023002")]
    # 重新登录后重新开始保活认证中心
    up.sso_dead.clear()
    _qr_login("2023002")
    assert _keepalive_row(uid)["sso_lost_at"] is None


def test_keepalive_requests_refresh_when_jwxt_expired(up):
    _, uid = _qr_login("2023002")
    up.expired.add("2023002")
    _age_keepalive()
    assert service.keepalive_user(uid)["jwxt"] is False
    assert _keepalive_row(uid)["refresh_requested_at"] is not None
    assert uid in _queued()


def test_keepalive_upstream_error_waits_for_next_interval(up, monkeypatch):
    _, uid = _qr_login("2023002")
    _age_keepalive()
    monkeypatch.setattr(jwxt, "touch", lambda s: (_ for _ in ()).throw(ConnectionError("boom")))
    assert service.keepalive_user(uid) == {"jwxt": None, "sso": None}
    row = _keepalive_row(uid)
    assert row["sso_lost_at"] is None and row["refresh_requested_at"] is None
    assert service.keepalive_candidates() == []


def test_keepalive_uses_leftover_budget(up, monkeypatch):
    _qr_login("2023002")
    _age_keepalive()
    monkeypatch.setattr(scheduler._stop, "wait", lambda t: False)
    assert scheduler.keepalive_once(_budget(tokens=0.5)) == 0
    assert up.touches == []
    b = _budget(tokens=1)
    assert scheduler.keepalive_once(b) == 1 and b.tokens == 1 - scheduler.KEEPALIVE_COST


def test_renewal_marks_sso_touched(up):
    _, uid = _qr_login("2023002")
    with db.tx() as conn:
        conn.execute("UPDATE users SET sso_touched_at=1 WHERE id=?", (uid,))
    up.expired.add("2023002")
    up.tgc_alive.add("2023002")
    assert service.refresh_user(uid, "scheduled")["ok"]
    assert _keepalive_row(uid)["sso_touched_at"] > db.now() - 5


def test_keepalive_covers_error_backoff_and_skips_idle_users(up):
    _, uid = _qr_login("2023002")
    _age_keepalive()
    with db.tx() as conn:
        conn.execute("UPDATE users SET status='error', next_refresh_at=? WHERE id=?", (db.now() + 3600, uid))
    assert service.keepalive_candidates() == [uid]  # 学校一时出错，登录态照样要保
    with db.tx() as conn:
        conn.execute("UPDATE users SET status='ok', last_seen_at=?, last_success_at=? WHERE id=?",
                     (db.now() - service.KEEPALIVE_IDLE - 60, db.now() - service.KEEPALIVE_IDLE - 60, uid))
    assert service.keepalive_candidates() == []     # 太久没用：不再打扰学校
    with db.tx() as conn:
        conn.execute("UPDATE users SET status='needs_rebind', last_seen_at=? WHERE id=?", (db.now(), uid))
    assert service.keepalive_candidates() == []


def test_keepalive_state_in_api(up):
    c, uid = _qr_login("2023002")
    keep = c.get("/api/me").json()["keepalive"]
    assert keep["every_minutes"] == service.SSO_KEEPALIVE_MINUTES and keep["sso_ok"] is True
    assert keep["last_at"] and keep["paused"] is False
    up.sso_dead.add("2023002")
    _age_keepalive()
    service.keepalive_user(uid)
    keep = c.post("/api/refresh", headers=_csrf(c), json={}).json()["account"]["keepalive"]
    assert keep["sso_ok"] is False and keep["sso_lost_at"]
    # 账号密码登录的人一样有保活状态，只是不算“扫码短效”
    c2 = TestClient(app)
    _login(c2, "2023001")
    me = c2.get("/api/me").json()
    assert me["keepalive"]["sso_ok"] is True and me["sso_short"] is False


def test_keepalive_saves_cookies_when_only_sso_answers(up, monkeypatch):
    _, uid = _qr_login("2023002")
    up.expired.add("2023002")
    _age_keepalive()
    before = db.connect().execute("SELECT enc_cookies FROM users WHERE id=?", (uid,)).fetchone()[0]

    def touch_sso(session):
        session.cookies.set("route", "new", domain="authserver.nufe.edu.cn", path="/")
        return True
    monkeypatch.setattr(cas, "touch_sso", touch_sso)
    assert service.keepalive_user(uid) == {"jwxt": False, "sso": True}
    after = db.connect().execute("SELECT enc_cookies FROM users WHERE id=?", (uid,)).fetchone()[0]
    assert after != before
    from app import crypto
    assert any(ck["name"] == "route" and ck["value"] == "new"
               for ck in crypto.decrypt_json(after, "cookies", uid))


# ---------- 热重载与安全/性能加固 ----------

def test_hot_reload_preserves_inflight_login_admin_session_and_widget_tasks(widget_user, up):
    from app import reloader
    c_user, widget_path = widget_user
    task = _keyed_get(widget_path).json()["fetch"]

    # 1. 启动一个卡在图形验证码的半途登录流程
    up.flow_captcha.add("2023002")
    c_login = TestClient(app)
    r_login = _login(c_login, "2023002").json()
    assert r_login["need"] == "captcha"
    flow_id = r_login["flow_id"]

    # 2. 管理员登录
    c_admin = TestClient(app)
    r_admin = _admin(c_admin)
    assert r_admin.status_code == 200
    csrf = r_admin.json()["csrf"]
    before_gen = c_admin.get("/api/admin/overview").json()["reload"]["generation"]

    # 注册热重载后钩子以恢复测试打桩（与 demo_server 一致）
    def _repatch():
        cas.login = up.login
        cas.LoginFlow = FakeFlow
        jwxt.fetch_timetable = up.fetch
        cas.renew_with_tgc = up.renew
        cas.touch_sso = up.touch_sso
        jwxt.touch = up.touch_jwxt
        service.MANUAL_REFRESH_MIN_GAP = 0
        service.MANUAL_REFRESH_FRESH_SECONDS = 0

    reloader.register_post_reload(_repatch)
    try:
        # 3. 缺少 CSRF 时拒绝热重载
        assert c_admin.post("/api/admin/reload", headers=ORIGIN).status_code == 403
        # 4. 触发进程内热重载
        res = c_admin.post("/api/admin/reload", headers={**ORIGIN, "X-CSRF-Token": csrf})
        assert res.status_code == 200
        body = res.json()
        assert body["ok"] is True
        assert body["generation"] == before_gen + 1
        assert body["last_reload_trigger"] == "admin"
        assert body["last_reload_error"] is None
        assert body["code_version"].startswith("sha-")

        # 5. 热重载后：管理员会话未丢失（已落库 SQLite）
        ov = c_admin.get("/api/admin/overview")
        assert ov.status_code == 200
        assert ov.json()["reload"]["generation"] == before_gen + 1

        # 6. 热重载后：正在进行中的验证码登录流程完好无损，直接提交验证码即可完成登录
        ok_login = c_login.post("/api/login/captcha", headers=ORIGIN, json={"flow_id": flow_id, "captcha": "abcd"})
        assert ok_login.status_code == 200 and ok_login.json()["ok"] is True
        assert c_login.get("/api/me").json()["student_id"] == "2023002"

        # 7. 热重载后：手机小组件已领走的代抓任务仍在 SQLite 中，回传照常落库
        up_res = _upload(widget_path, {
            "id": task["id"],
            "course_html": COURSE_HTML,
            "print_data": _print_data(room="D404"),
            "final_urls": ["https://jwxt.nufe.edu.cn/student/for-std/course-table"],
        })
        assert up_res.status_code == 200 and up_res.json()["ok"] is True
        assert c_user.get("/api/timetable").json()["activities"][0]["room"] == "D404"
    finally:
        if _repatch in reloader._post_reload_hooks:
            reloader._post_reload_hooks.remove(_repatch)


def test_hot_reload_rejects_real_syntax_error(up, monkeypatch, tmp_path):
    from app import reloader
    c_admin = TestClient(app)
    csrf = _admin(c_admin).json()["csrf"]
    before_gen = reloader.status()["generation"]
    bad = tmp_path / "broken.py"
    bad.write_text("def broken(:\n", "utf-8")
    monkeypatch.setattr(reloader, "_py_files", lambda: [bad])
    monkeypatch.setattr(reloader.importlib, "reload", lambda _m: pytest.fail("预检失败不应碰线上模块"))
    r = c_admin.post("/api/admin/reload", headers={**ORIGIN, "X-CSRF-Token": csrf})
    assert r.status_code == 400 and "语法错误（broken.py:1）" in r.json()["detail"]
    st = reloader.status()
    assert st["generation"] == before_gen and "语法错误" in st["last_reload_error"] and st["needs_restart"] is False
    assert c_admin.get("/healthz").json()["ok"] is True


def test_hot_reload_import_failure_is_caught_before_touching_modules(up, monkeypatch):
    from app import reloader
    before_gen = reloader.status()["generation"]
    monkeypatch.setattr(reloader, "_PREFLIGHT_CODE", "raise RuntimeError('boom at import')")
    monkeypatch.setattr(reloader.importlib, "reload", lambda _m: pytest.fail("预检失败不应碰线上模块"))
    with pytest.raises(reloader.ReloadError, match="boom at import"):
        reloader.reload("test")
    st = reloader.status()
    assert st["generation"] == before_gen and st["needs_restart"] is False


def test_hot_reload_failure_after_preflight_flags_restart(up, monkeypatch):
    from app import reloader
    monkeypatch.setattr(reloader, "_preflight", lambda: None)

    def boom(_mod):
        raise RuntimeError("late failure")

    monkeypatch.setattr(reloader.importlib, "reload", boom)
    with pytest.raises(reloader.ReloadError):
        reloader.reload("test")
    assert reloader.status()["needs_restart"] is True
    monkeypatch.undo()
    reloader._needs_restart = False
    reloader._last_reload_error = None


def test_hot_reload_refuses_when_reloader_itself_changed(up, monkeypatch):
    from app import reloader
    monkeypatch.setattr(reloader, "_self_hash", "changed-on-disk")
    with pytest.raises(reloader.ReloadError, match="需要重启"):
        reloader.reload("test")
    monkeypatch.undo()
    reloader._last_reload_error = None


def test_hot_reload_status_reports_running_version_not_disk(up, monkeypatch):
    import json
    import os

    from app import reloader
    running = reloader.status()["code_version"]
    monkeypatch.setattr(reloader, "_disk_version", lambda: "sha-newondisk")
    st = reloader.status()
    assert st["code_version"] == running and st["disk_version"] == "sha-newondisk" and st["in_sync"] is False
    reloader._write_status()
    saved = json.loads(reloader.status_file().read_text("utf-8"))
    assert saved["in_sync"] is False and saved["pid"] == os.getpid()


def test_hot_reload_swaps_in_new_classes_for_kept_instances(up):
    import logging

    from app import admin, reloader, scheduler
    admin.install_log_capture()
    logging.getLogger("kebiao.test").warning("before reload marker")
    scheduler._budget.rate = 0.123
    old_budget_cls, old_ring_cls = type(scheduler._budget), type(admin.ring)
    gateway = main.app

    def _repatch():
        service.MANUAL_REFRESH_MIN_GAP = 0
        service.MANUAL_REFRESH_FRESH_SECONDS = 0
        cas.login, cas.LoginFlow = up.login, FakeFlow
        jwxt.fetch_timetable = up.fetch

    reloader.register_post_reload(_repatch)
    try:
        reloader.reload("test")
    finally:
        reloader._post_reload_hooks.remove(_repatch)
    assert main.app is gateway
    assert type(scheduler._budget) is scheduler.Budget is not old_budget_cls and scheduler._budget.rate == 0.123
    assert type(admin.ring) is admin.RingHandler is not old_ring_cls
    assert any("before reload marker" in r["message"] for r in admin.ring.records)
    root = logging.getLogger()
    assert [h for h in root.handlers if type(h).__name__ == "RingHandler"] == [admin.ring]
    filters = [f for f in logging.getLogger("uvicorn.access").filters if type(f).__name__ == "_RedactTokenPaths"]
    assert len(filters) == 1 and type(filters[0]) is main._RedactTokenPaths


def test_changing_admin_password_invalidates_existing_sessions(up, monkeypatch):
    from app import admin
    c = TestClient(app)
    assert _admin(c).status_code == 200
    assert c.get("/api/admin/overview").status_code == 200
    monkeypatch.setattr(admin, "ADMIN_PASSWORD_HASH", admin.hash_password("rotated", 1000))
    assert c.get("/api/admin/overview").status_code == 401
    assert _admin(c, pw="rotated").status_code == 200
    assert c.get("/api/admin/overview").status_code == 200


def test_me_queues_refresh_for_returning_dormant_user(up):
    c = TestClient(app)
    _login(c, "2023001")
    uid = service_uid("2023001")
    now = db.now()
    # 沉寂 45 天，数据 12 小时前刷过：按沉寂算还不到期，按“刚回来”算已经逾期
    with db.tx() as conn:
        conn.execute("UPDATE users SET last_seen_at=?, last_success_at=?, refresh_requested_at=NULL WHERE id=?",
                     (now - 45 * 86400, now - 12 * 3600, uid))
    service._touched.clear()
    assert c.get("/api/me").status_code == 200
    assert _row("2023001", "refresh_requested_at")[0] is not None


def test_wait_reload_script(tmp_path):
    import json
    import subprocess
    import sys
    from pathlib import Path

    script = str(Path(main.__file__).resolve().parent.parent / "deploy" / "wait_reload.py")
    status = tmp_path / "reload-status.json"

    def run():
        return subprocess.run([sys.executable, script, str(status), "0.5"], capture_output=True, text=True,
                              check=False).returncode

    assert run() == 1  # 超时：服务没写结果
    for data, code in (({"in_sync": True, "last_reload_error": None, "needs_restart": False}, 0),
                       ({"in_sync": True, "last_reload_error": "语法错误", "needs_restart": False}, 1),
                       ({"in_sync": True, "last_reload_error": None, "needs_restart": True}, 1),
                       ({"in_sync": False, "last_reload_error": None, "needs_restart": False}, 1)):
        status.write_text(json.dumps(data), "utf-8")
        assert run() == code, data


def test_widget_upload_rejected_early_when_no_active_task(widget_user, up):
    _c, path = widget_user
    r = _upload(path, {"id": "any", "course_html": COURSE_HTML, "print_data": _print_data()})
    assert r.status_code == 400 and "任务不存在" in r.json()["detail"]
    m_path = path.replace("/widget/", "/mobile/").removesuffix(".json") + "/upload"
    r_m = TestClient(app).post(m_path, json={"id": "any"})
    assert r_m.status_code == 400 and "任务不存在" in r_m.json()["detail"]


def test_pending_login_capacity_cap_evicts_oldest_and_wipes_password(up, monkeypatch):
    monkeypatch.setattr(service, "MAX_PENDING_FLOWS", 3)
    up.flow_captcha.add("2023001")
    fids = []
    flows = []
    for _ in range(4):
        from app import security
        security.login_by_ip._hits.clear()
        security.login_by_student._hits.clear()
        c = TestClient(app)
        fid = _login(c, "2023001").json()["flow_id"]
        fids.append(fid)
        flows.append(service._pending[fid].flow)
    assert len(service._pending) == 3
    assert fids[0] not in service._pending
    assert flows[0].password == ""  # 被挤出的半途流程立即擦除内存中的明文密码
    assert fids[-1] in service._pending


# ---------- 安卓 App 2.x 托管（双层兜底） ----------

def _app_cookies(sid):
    return [{"name": "CASTGC", "value": f"tgc-{sid}", "domain": "authserver.nufe.edu.cn", "path": "/authserver",
             "expires": TGC_REMEMBER_UNTIL},
            {"name": "SESSION", "value": "jw", "domain": "jwxt.nufe.edu.cn", "path": "/student"},
            {"name": "evil", "value": "x", "domain": "example.com", "path": "/"},
            {"name": "CASTGC", "value": "bad", "domain": "jwxt.nufe.edu.cn", "path": "/"}]


class HostedApp:
    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.token = self.device_id = None

    def public(self):
        return base64.b64encode(self.key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)).decode()

    def enroll(self, sid, **kw):
        main.app_enroll_by_ip._hits.clear()
        r = TestClient(app).post("/api/app/enroll", json={"cookies": kw.pop("cookies", _app_cookies(sid)),
                                                          "public_key": self.public(), "name": "Pixel"}, **kw)
        if r.status_code == 200:
            self.token, self.device_id = r.json()["token"], r.json()["device_id"]
        return r

    def signed(self):
        ts = str(int(time.time()))
        sig = self.key.sign(mobile_devices.signed_message(self.token, self.device_id, ts), ec.ECDSA(hashes.SHA256()))
        return {"X-Device-Id": self.device_id, "X-Device-Time": ts, "X-Device-Signature": base64.b64encode(sig).decode()}


def test_app_enroll_creates_account_and_binds_device(up):
    phone = HostedApp()
    r = phone.enroll("2023001")
    assert r.status_code == 200 and r.json()["student"]["code"] == "2023001"
    uid = service_uid("2023001")
    row = db.connect().execute("SELECT status, cred_mode, enc_password FROM users WHERE id=?", (uid,)).fetchone()
    assert (row["status"], row["cred_mode"], row["enc_password"]) == ("ok", "cookie", None)
    # 只留下学校两个域名、格式正确的 cookie；教务域名下冒充的 CASTGC 被丢弃
    from app import crypto
    jar = crypto.decrypt_json(db.connect().execute("SELECT enc_cookies FROM users WHERE id=?", (uid,)).fetchone()[0],
                              "cookies", uid)
    assert sorted((c["name"], c["domain"]) for c in jar) == [("CASTGC", "authserver.nufe.edu.cn"),
                                                             ("SESSION", "jwxt.nufe.edu.cn")]
    t = TestClient(app).get(f"/mobile/{phone.token}/timetable", headers=phone.signed()).json()
    assert t["status"] == "ok" and t["student"] == {"name": "学生1", "code": "2023001"}
    assert t["activities"][0]["courseName"] == "高数" and t["semester_id"] == 161
    assert mobile_devices.list_devices(uid)[0]["name"] == "Pixel"


def test_app_enroll_rejects_dead_session(up):
    up.expired.add("2023001")
    r = HostedApp().enroll("2023001")
    assert r.status_code == 400 and "重新登录" in r.json()["detail"]
    assert db.connect().execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def test_app_enroll_needs_school_cookies(up):
    r = HostedApp().enroll("2023001", cookies=[{"name": "a", "value": "b", "domain": "example.com", "path": "/"}])
    assert r.status_code == 400


def test_app_enroll_keeps_existing_website_account_and_link(up):
    c = TestClient(app)
    _login(c, "2023001", remember=True)
    old_token = c.post("/api/widget/rotate", headers=_csrf(c)).json()["widget_url"].split("/")[-1][:-5]
    phone = HostedApp()
    assert phone.enroll("2023001").status_code == 200
    assert phone.token == old_token  # 不重新生成链接：用户的 iOS 小组件 / 旧设备不受影响
    row = db.connect().execute("SELECT cred_mode FROM users WHERE student_id='2023001'").fetchone()
    assert row["cred_mode"] == "password"  # 网站上存的密码不动
    # 同一把设备公钥再托管：只换 id，不重复占名额
    assert phone.enroll("2023001").status_code == 200
    assert len(mobile_devices.list_devices(service_uid("2023001"))) == 1


def test_app_timetable_requires_device_signature(up):
    phone = HostedApp()
    phone.enroll("2023001")
    assert TestClient(app).get(f"/mobile/{phone.token}/timetable").status_code == 401
    other = HostedApp()
    other.token, other.device_id = phone.token, phone.device_id
    assert TestClient(app).get(f"/mobile/{phone.token}/timetable", headers=other.signed()).status_code == 401


def test_app_leave_forgets_school_session(up):
    phone = HostedApp()
    phone.enroll("2023001")
    assert TestClient(app).post(f"/mobile/{phone.token}/leave", headers=phone.signed()).json() == {"ok": True}
    uid = service_uid("2023001")
    row = db.connect().execute("SELECT status, enc_cookies FROM users WHERE id=?", (uid,)).fetchone()
    assert row["enc_cookies"] is None and row["status"] == "needs_rebind"
    assert mobile_devices.list_devices(uid) == []
    assert TestClient(app).get(f"/mobile/{phone.token}/timetable", headers=phone.signed()).status_code == 401


# ---------- 设备数据源：安卓手机给 iOS 小组件与日历供数 ----------

def _push_body(sid="2023001", course="手机推的课", updated=None, semester_id=161):
    return {"semester_id": semester_id, "semester_name": "2026-2027-1", "week1_monday": "2026-09-07",
            "updated_at": updated or time.time(), "student": {"name": "学生1", "code": sid},
            "activities": [{"courseName": course, "teachers": ["T"], "weekday": 2, "startUnit": 3, "endUnit": 4,
                            "weekIndexes": list(range(1, 17)), "weeksStr": "1~16", "room": "C301", "building": "",
                            "campus": "", "startTime": "10:00", "endTime": "11:35"}]}


def _push(phone, body):
    return TestClient(app).post(f"/mobile/{phone.token}/push", json=body, headers=phone.signed())


def test_data_source_only_enroll_keeps_no_school_session(up):
    phone = HostedApp()
    main.app_enroll_by_ip._hits.clear()
    r = TestClient(app).post("/api/app/enroll", json={"cookies": _app_cookies("2023001"), "public_key": phone.public(),
                                                      "name": "Pixel", "keep_session": False})
    assert r.status_code == 200
    phone.token, phone.device_id = r.json()["token"], r.json()["device_id"]
    uid = service_uid("2023001")
    row = db.connect().execute("SELECT cred_mode, enc_cookies, status FROM users WHERE id=?", (uid,)).fetchone()
    assert (row["cred_mode"], row["enc_cookies"], row["status"]) == ("device", None, "ok")
    # 调度器不替他刷；网站上点刷新也不会把他打成“需重新登录”
    assert uid not in {u for _s, u in service.candidates(now=time.time() + 30 * 86400, limit=100)}
    assert service.refresh_user(uid, "manual")["status"] == "device"
    assert db.connect().execute("SELECT status FROM users WHERE id=?", (uid,)).fetchone()["status"] == "ok"

    links = TestClient(app).get(f"/mobile/{phone.token}/links", headers=phone.signed()).json()
    assert links["widget_url"].endswith(f"/widget/{phone.token}.json") and links["ics_url"].endswith(".ics")
    assert links["webcal_url"].startswith("webcal://") and links["server_refresh"] is False

    assert _push(phone, _push_body()).json() == {"ok": True, "stored": True}
    feed = TestClient(app).get(f"/widget/{phone.token}.json").json()
    assert "手机推的课" in json.dumps(feed, ensure_ascii=False)
    ics = TestClient(app).get(links["ics_url"].removeprefix("http://testserver")).text
    assert "手机推的课" in ics
    row = db.connect().execute("SELECT enc_cookies, cred_mode FROM users WHERE id=?", (uid,)).fetchone()
    assert (row["enc_cookies"], row["cred_mode"]) == (None, "device")
    assert db.connect().execute("SELECT trigger FROM refresh_log WHERE user_id=? ORDER BY at DESC", (uid,)).fetchone()[0] == "device"


def test_device_push_checks_owner_shape_and_freshness(up):
    phone = HostedApp()
    phone.enroll("2023001")
    assert _push(phone, _push_body(sid="2023002")).status_code == 400  # 不是本人的课表
    # 本机数据没读到学号：设备签名已证明身份，照收
    no_code = _push_body(course="无学号", updated=time.time() + 1)
    no_code["student"] = {}
    assert _push(phone, no_code).json()["stored"] is True
    assert _push(phone, {**_push_body(), "activities": "x"}).status_code == 400
    assert TestClient(app).post(f"/mobile/{phone.token}/push", json=_push_body()).status_code == 401  # 没签名
    # 本站已有更新的那份：旧的推送不覆盖
    old = _push(phone, _push_body(course="旧数据", updated=time.time() - 3600)).json()
    assert old["stored"] is False
    assert "旧数据" not in json.dumps(TestClient(app).get(f"/widget/{phone.token}.json").json(), ensure_ascii=False)
    # 托管用户（服务器也在刷）推更新的数据：照收，两边谁新用谁
    assert _push(phone, _push_body(course="新数据")).json()["stored"] is True
    from app import crypto
    uid = service_uid("2023001")
    jar = crypto.decrypt_json(db.connect().execute("SELECT enc_cookies FROM users WHERE id=?", (uid,)).fetchone()[0],
                              "cookies", uid)
    assert any(c["name"] == "CASTGC" for c in jar)  # 推送不会冲掉服务器托管的登录态


def test_forget_session_keeps_device_as_data_source(up):
    phone = HostedApp()
    phone.enroll("2023001")
    assert TestClient(app).post(f"/mobile/{phone.token}/forget-session", headers=phone.signed()).json() == {"ok": True}
    row = db.connect().execute("SELECT cred_mode, enc_cookies FROM users WHERE student_id='2023001'").fetchone()
    assert (row["cred_mode"], row["enc_cookies"]) == ("device", None)
    assert _push(phone, _push_body()).json()["stored"] is True


def test_data_source_only_enroll_keeps_website_password(up):
    c = TestClient(app)
    _login(c, "2023001", remember=True)
    phone = HostedApp()
    main.app_enroll_by_ip._hits.clear()
    r = TestClient(app).post("/api/app/enroll", json={"cookies": _app_cookies("2023001"), "public_key": phone.public(),
                                                      "name": "Pixel", "keep_session": False})
    assert r.status_code == 200
    row = db.connect().execute("SELECT cred_mode, enc_cookies FROM users WHERE student_id='2023001'").fetchone()
    assert row["cred_mode"] == "password" and row["enc_cookies"] is not None  # 服务器照常刷新
