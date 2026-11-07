"""App 协助刷新与匿名统计。

中继测试是真的 TLS：本地起一个冒充 jwxt.nufe.edu.cn 的 HTTPS 服务器（证书由测试 CA 签发），
“手机”线程只按 exchange 的指令开 socket、搬字节，和 App 里的实现一致。
"""
from __future__ import annotations

import base64
import contextlib
import datetime as dt
import gzip
import http.server
import queue
import socket
import ssl
import threading
import time

import pytest
import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient

from app import admin, assist, db, polite, scheduler, service
from app.main import app

ORIGIN = {"Origin": "http://testserver"}
INSTALL = "test-install-0123456789abcdef"


# ---------- 测试 CA 与冒充教务的 HTTPS 服务器 ----------

def _cert(subject: str, issuer_key, issuer_name, key, ca: bool, sans=()):
    now = dt.datetime.now(dt.timezone.utc)
    b = (x509.CertificateBuilder()
         .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)]))
         .issuer_name(issuer_name or x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)]))
         .public_key(key.public_key()).serial_number(x509.random_serial_number())
         .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=30))
         .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
         # Python 3.13 默认 VERIFY_X509_STRICT：要有密钥标识与用途，和正规 CA 签发的证书一样
         .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
         .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()), critical=False)
         .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=ca, crl_sign=ca, content_commitment=False,
                                      key_encipherment=False, data_encipherment=False, key_agreement=False,
                                      encipher_only=False, decipher_only=False), critical=True))
    if not ca:
        b = b.add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
    if sans:
        b = b.add_extension(x509.SubjectAlternativeName([x509.DNSName(s) for s in sans]), critical=False)
    return b.sign(issuer_key, hashes.SHA256())


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    d = tmp_path_factory.mktemp("pki")
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = _cert("test-ca", ca_key, None, ca_key, True)

    def leaf(name, host):
        key = ec.generate_private_key(ec.SECP256R1())
        cert = _cert(host, ca_key, ca.subject, key, False, sans=[host])
        (d / f"{name}.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        (d / f"{name}.key").write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                                           serialization.PrivateFormat.PKCS8,
                                                           serialization.NoEncryption()))
        return str(d / f"{name}.pem"), str(d / f"{name}.key")

    (d / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    return {"ca": str(d / "ca.pem"), "jwxt": leaf("jwxt", "jwxt.nufe.edu.cn"), "evil": leaf("evil", "evil.example")}


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    seen: list = []

    def log_message(self, *_a):
        pass

    def do_GET(self):
        type(self).seen.append((self.path, self.headers.get("Cookie"), self.headers.get("Host")))
        if self.path.startswith("/student/to-auth"):
            self.send_response(302)
            self.send_header("Location", "https://authserver.nufe.edu.cn/authserver/login")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = gzip.compress(("课表" * 20000).encode())  # 压缩后的分块响应：考 chunked + gzip 两条路
        self.send_response(200)
        self.send_header("Content-Encoding", "gzip")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Set-Cookie", "SESSION=fresh; Path=/student; Secure; HttpOnly")
        self.send_header("Connection", "close")
        self.end_headers()
        for i in range(0, len(body), 7000):
            part = body[i:i + 7000]
            self.wfile.write(f"{len(part):x}\r\n".encode() + part + b"\r\n")
        self.wfile.write(b"0\r\n\r\n")


def _serve(cert):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(*cert)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture(scope="module")
def school(pki):
    good, evil = _serve(pki["jwxt"]), _serve(pki["evil"])
    yield {"good": good.server_address[1], "evil": evil.server_address[1]}
    good.shutdown()
    evil.shutdown()


@pytest.fixture()
def trust_test_ca(pki, monkeypatch):
    ctx = ssl.create_default_context(cafile=pki["ca"])
    monkeypatch.setattr(assist, "_context", lambda: ctx)
    polite.reset_health()


class FakePhone(threading.Thread):
    """App 中继循环的 Python 版：只认服务器指定的 host:443，实际连到 target_port（本地冒充的学校）。"""

    def __init__(self, session_id: str, token: str, target_port: int):
        super().__init__(daemon=True)
        self.sid, self.token, self.port = session_id, token, target_port
        self.inbound: queue.Queue = queue.Queue()
        self.socks: dict[int, socket.socket] = {}
        self.hosts: list[str] = []
        self.outcome = None

    def _reader(self, conn, sock):
        try:
            while data := sock.recv(65536):
                self.inbound.put({"conn": conn, "data": base64.b64encode(data).decode()})
            self.inbound.put({"conn": conn, "closed": True})
        except OSError:
            self.inbound.put({"conn": conn, "closed": True})

    def run(self):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            events = []
            with contextlib.suppress(queue.Empty):
                events.append(self.inbound.get(timeout=0.05 if self.socks else 0.0))
            while not self.inbound.empty():
                events.append(self.inbound.get_nowait())
            out = assist.exchange(self.sid, self.token, events)
            if out is None:
                return
            for ev in out["events"]:
                if ev["op"] == "open":
                    assert ev["port"] == 443
                    self.hosts.append(ev["host"])
                    s = socket.create_connection(("127.0.0.1", self.port))
                    self.socks[ev["conn"]] = s
                    threading.Thread(target=self._reader, args=(ev["conn"], s), daemon=True).start()
                elif ev["op"] == "data":
                    self.socks[ev["conn"]].sendall(base64.b64decode(ev["data"]))
                elif ev["op"] == "close":
                    s = self.socks.pop(ev["conn"], None)
                    if s:
                        s.close()
            if out["done"]:
                self.outcome = out["outcome"]
                return


def _session_with_phone(port):
    session = assist.Session("k")
    phone = FakePhone(session.id, session.token, port)
    with assist._lock:
        assist._sessions[session.id] = session
    phone.start()
    return session, phone


# ---------- 中继传输层 ----------

def test_relay_round_trip_is_end_to_end_tls(school, trust_test_ca):
    _Handler.seen.clear()
    session, phone = _session_with_phone(school["good"])
    s = assist.relay_session(session)
    s.cookies.set("SESSION", "old", domain="jwxt.nufe.edu.cn", path="/student")
    s.cookies.set("CASTGC", "secret-tgc", domain="authserver.nufe.edu.cn", path="/authserver")
    r = s.get("https://jwxt.nufe.edu.cn/student/for-std/course-table")
    session.finish("ok")
    phone.join(5)
    assert r.status_code == 200 and r.text == "课表" * 20000  # 分块 + gzip 都还原了
    assert s.cookies.get("SESSION", domain="jwxt.nufe.edu.cn") == "fresh"
    path, cookie, host = _Handler.seen[-1]
    assert host == "jwxt.nufe.edu.cn" and "SESSION=old" in cookie
    assert "CASTGC" not in cookie  # 认证中心的凭据不会发往教务，更不会出现在中继上
    assert phone.hosts == ["jwxt.nufe.edu.cn"] and phone.outcome == "ok"


def test_relay_to_wrong_server_fails_certificate_check(school, trust_test_ca):
    """手机把连接接到别处（证书不是 jwxt 的）：TLS 校验不过，不会把请求发出去。"""
    _Handler.seen.clear()
    session, phone = _session_with_phone(school["evil"])
    s = assist.relay_session(session)
    with pytest.raises(polite.RelayFailed, match="certificate"):
        s.get("https://jwxt.nufe.edu.cn/student/for-std/course-table")
    session.finish("relay_failed")
    phone.join(5)
    assert _Handler.seen == []


def test_relay_refuses_other_hosts(school, trust_test_ca):
    _Handler.seen.clear()
    session, phone = _session_with_phone(school["good"])
    s = assist.relay_session(session)
    with pytest.raises(polite.RelayFailed, match="host not relayed"):
        s.get("https://jwxt.nufe.edu.cn/student/to-auth")  # 302 → 认证中心：交回服务器
    session.finish("needs_server")
    phone.join(5)


def test_relay_times_out_when_phone_disappears(monkeypatch, trust_test_ca):
    monkeypatch.setattr(assist, "IDLE_TIMEOUT", 0.3)
    session = assist.Session("k")
    s = assist.relay_session(session)
    with pytest.raises(polite.RelayFailed):
        s.get("https://jwxt.nufe.edu.cn/student/for-std/course-table")


def test_relay_failure_is_not_counted_against_upstream_health(monkeypatch, trust_test_ca):
    monkeypatch.setattr(assist, "IDLE_TIMEOUT", 0.2)
    s = assist.relay_session(assist.Session("k"))
    with pytest.raises(polite.RelayFailed):
        s.get("https://jwxt.nufe.edu.cn/student/for-std/course-table")
    assert polite.health() == (0.0, 0.0)


# ---------- 刷新层：中继失败不算用户的失败 ----------

@pytest.fixture()
def user():
    db.init()
    with db.tx() as c:
        c.execute("DELETE FROM users")
        c.execute("DELETE FROM app_installs")
        c.execute("INSERT INTO users(id, student_id, created_at, status, enc_cookies) VALUES(1, '2023001', ?, 'ok', ?)",
                  (db.now(), service.crypto.encrypt_json([], "cookies", 1)))
    service._locks.clear()
    assist._sessions.clear()
    assist._skip_users.clear()
    yield 1


def test_refresh_through_failed_relay_keeps_user_status(user, monkeypatch, trust_test_ca):
    monkeypatch.setattr(assist, "IDLE_TIMEOUT", 0.2)
    with pytest.raises(polite.RelayFailed):
        service.refresh_user(user, "assist", session=assist.relay_session(assist.Session("k")))
    row = db.connect().execute("SELECT status, fail_count, last_refresh_at FROM users WHERE id=1").fetchone()
    assert (row["status"], row["fail_count"], row["last_refresh_at"]) == ("ok", 0, None)
    assert db.connect().execute("SELECT COUNT(*) FROM refresh_log").fetchone()[0] == 0


# ---------- 统计与派活接口 ----------

def test_heartbeat_records_anonymous_install(user):
    c = TestClient(app)
    r = c.post("/api/app/heartbeat", json={"install_id": INSTALL, "version": "2.1.0", "version_code": 23,
                                           "sdk": 37, "has_timetable": True, "assist": True})
    assert r.status_code == 200 and r.json() == {"ok": True, "assist_available": assist.ENABLED}
    row = db.connect().execute("SELECT * FROM app_installs").fetchone()
    assert row["id_hash"] == assist.install_hash(INSTALL) and INSTALL not in tuple(row)
    assert (row["version"], row["sdk"], row["has_timetable"], row["assist"]) == ("2.1.0", 37, 1, 1)
    stats = assist.install_stats()
    assert stats["total"] == 1 and stats["active_1d"] == 1 and stats["assist_7d"] == 1
    assert stats["versions_30d"] == [{"version": "2.1.0", "n": 1}]


def test_heartbeat_validates_input(user):
    c = TestClient(app)
    assert c.post("/api/app/heartbeat", json={"install_id": "bad id with spaces!!"}).status_code in (400, 422)
    r = c.post("/api/app/heartbeat", json={"install_id": INSTALL, "version": "<script>"})
    assert r.status_code == 200
    assert db.connect().execute("SELECT version FROM app_installs").fetchone()["version"] is None


def test_heartbeat_from_browser_of_other_site_is_rejected(user):
    r = TestClient(app).post("/api/app/heartbeat", headers={"Origin": "https://evil.example"},
                             json={"install_id": INSTALL})
    assert r.status_code == 403


def test_claim_is_off_unless_enabled(user, monkeypatch):
    monkeypatch.setattr(assist, "ENABLED", False)
    assist.heartbeat(INSTALL, "2.1.0", 23, 37, True, True)
    assert TestClient(app).post("/api/assist/claim", json={"install_id": INSTALL}).json() == {
        "task": None, "reason": "disabled"}


def test_claim_requires_opt_in_and_hands_out_one_session(user, monkeypatch):
    monkeypatch.setattr(assist, "ENABLED", True)
    monkeypatch.setattr(service, "candidates", lambda now=None, limit=50: [(5.0, user)])
    monkeypatch.setattr(scheduler._budget, "tokens", 5.0)
    started = []
    monkeypatch.setattr(assist, "_run", lambda s: started.append(s.uid))
    assist.heartbeat(INSTALL, "2.1.0", 23, 37, True, False)
    c = TestClient(app)
    assert c.post("/api/assist/claim", json={"install_id": INSTALL}).json()["reason"] == "not_enrolled"
    assist.heartbeat(INSTALL, "2.1.0", 23, 37, True, True)
    task = c.post("/api/assist/claim", json={"install_id": INSTALL}).json()["task"]
    assert task["hosts"] == ["jwxt.nufe.edu.cn"] and task["port"] == 443
    deadline = time.monotonic() + 2
    while not started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert started == [user]
    # 同一台手机同时只领一个；budget 被占了一次
    assert c.post("/api/assist/claim", json={"install_id": INSTALL}).json()["reason"] == "busy"
    assert scheduler._budget.tokens == 4.0
    # 错的 token 拿不到会话
    r = c.post("/api/assist/exchange", json={"session": task["session"], "token": "x" * 20, "events": []})
    assert r.status_code == 404


def test_relay_failures_put_phone_on_cooldown(user):
    assist.heartbeat(INSTALL, "2.1.0", 23, 37, True, True)
    key = assist.install_hash(INSTALL)
    for _ in range(assist.FAIL_STREAK_LIMIT):
        assist._record_outcome(key, "relay_failed")
    row = db.connect().execute("SELECT assist_fail, assist_block_until FROM app_installs").fetchone()
    assert row["assist_fail"] == assist.FAIL_STREAK_LIMIT and row["assist_block_until"] > db.now()
    assist._record_outcome(key, "ok")
    assert tuple(db.connect().execute("SELECT assist_streak, assist_ok FROM app_installs").fetchone()) == (0, 1)


def test_admin_android_includes_app_installs(user):
    assist.heartbeat(INSTALL, "2.1.0", 23, 37, True, True)
    data = admin.android()
    assert data["app"]["total"] == 1 and data["installs"][0]["id"] == assist.install_hash(INSTALL)[:8]
    assert "id_hash" not in data["installs"][0]
    assert data["assist"]["max_sessions"] == assist.MAX_SESSIONS
    assert admin.overview()["app"]["total"] == 1


def test_requests_exceptions_still_reach_relay_untouched():
    """RelayFailed 不能是 requests 的异常：否则会被当成学校网络错误记进健康度、算作用户失败。"""
    assert not issubclass(polite.RelayFailed, requests.RequestException)
