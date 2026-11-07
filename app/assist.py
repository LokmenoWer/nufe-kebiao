"""本地优先安卓 App 的匿名统计与“协助刷新”（人人为我，我为人人）。

一、匿名统计
App 2.0 起不再登录本站，只定期发一次心跳：随机安装 id、版本号、系统版本、是否已导入课表、是否开了协助刷新。
本站只存安装 id 的 sha256，不收学号、姓名或课表，管理员页据此统计装机量与活跃度。

二、协助刷新
网页用户的后台刷新原本全部由服务器直连教务。打开了“协助刷新”的 App 在连着 Wi-Fi 时会来领任务，
服务器把一次后台刷新的网络出口借给这台手机——但手机只是一个“盲转发”的 TCP 中继：

    服务器 ──(本站 HTTPS 长轮询)── 手机 ──(TCP 443)── jwxt.nufe.edu.cn
      └──────────── 服务器与教务之间端到端的 TLS ────────────┘

- TLS 握手由服务器完成，并按系统 CA 校验教务的证书；手机经手的只有密文，看不到任何人的 cookie、
  学号或课表，也改不了内容（改了 TLS 就校验失败）。所以不需要信任手机，任何装了 App 的人都能帮忙。
- 只中继教务（jwxt）的 443 端口。需要去统一身份认证（CASTGC 续期、密码登录）时立刻放弃中继，
  交回服务器直连：登录类请求换出口 IP 可能触发学校的异地风控，不值得冒险。手机端也只接受这一个目标。
- 中继失败（手机断开、超时、证书不对）不算这个用户的刷新失败，也不计入学校的健康度；用户仍在队列里，
  由服务器稍后照常刷新。同一台手机连续失败几次就冷却一段时间。
- 对学校的礼貌不变：中继的请求同样走 polite 的全站限速，并占用调度器的后台预算。协助刷新减轻的是
  服务器出口 IP 的压力（请求分散到各个手机的网络），而不是让学校多挨请求。

默认关闭，服务端 .env 里设 ASSIST_ENABLED=1 才会派活（心跳统计始终开启）。
"""
from __future__ import annotations

import base64
import binascii
import http.client
import io
import logging
import os
import re
import secrets
import ssl
import threading
import time
from urllib.parse import urlsplit

import certifi
import requests
from requests.adapters import HTTPAdapter
from urllib3 import HTTPResponse
from urllib3._collections import HTTPHeaderDict

from . import crypto, db, polite, scheduler, service
from .polite import RelayFailed

log = logging.getLogger("kebiao.assist")

ENABLED = os.getenv("ASSIST_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")
ALLOWED_HOSTS = frozenset({"jwxt.nufe.edu.cn"})
ALLOWED_PORT = 443

MAX_SESSIONS = 3           # 同时最多借几台手机
LONG_POLL = 2.0            # 秒：交换接口最多挂起多久等待要发给手机的数据
IDLE_TIMEOUT = 20.0        # 秒：手机这么久没来交换就当它走了
RECV_TIMEOUT = 20.0        # 秒：一次 HTTPS 往返（含握手）最多等多久
SESSION_MAX = 120.0        # 秒：一个协助会话最长存活
MAX_SESSION_BYTES = 8 * 1024 * 1024   # 一个会话经手的密文上限（双向合计）
MAX_RESPONSE_BYTES = 4 * 1024 * 1024  # 单个响应体上限（压缩前）
FAIL_STREAK_LIMIT = 3      # 同一台手机连续中继失败几次后冷却
FAIL_COOLDOWN = 6 * 3600
SKIP_USER_FOR = 2 * 3600   # 中继发现需要去认证中心的用户：这段时间内不再派给手机
INSTALL_KEEP = 180 * 86400  # 半年没心跳的安装记录清掉

INSTALL_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
VERSION_RE = re.compile(r"^[0-9A-Za-z.+_-]{1,40}$")

_lock: threading.Lock = globals().get("_lock") or threading.Lock()
_sessions: dict[str, Session] = globals().get("_sessions") if "_sessions" in globals() else {}
_skip_users: dict[int, float] = globals().get("_skip_users") if "_skip_users" in globals() else {}
_ssl_ctx: ssl.SSLContext | None = None


def _context() -> ssl.SSLContext:
    global _ssl_ctx
    if _ssl_ctx is None:
        ctx = ssl.create_default_context(cafile=certifi.where())
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        _ssl_ctx = ctx
    return _ssl_ctx


# ---------- 匿名统计 ----------

def install_hash(install_id: str) -> str:
    return crypto.token_hash(f"app-install:{install_id}")


def valid_install_id(install_id) -> bool:
    return isinstance(install_id, str) and bool(INSTALL_ID_RE.match(install_id))


def heartbeat(install_id: str, version: str | None, version_code: int | None, sdk: int | None,
              has_timetable: bool, assist: bool, *, allow_new: bool = True, now: float | None = None) -> bool:
    """记一次心跳。allow_new=False 时只更新已有记录（新装机按 IP 限量，防止有人灌爆表）。返回是否已记录。"""
    now = db.now() if now is None else now
    version = version if isinstance(version, str) and VERSION_RE.match(version) else None
    key = install_hash(install_id)
    with db.tx() as c:
        updated = c.execute(
            """UPDATE app_installs SET version=?, version_code=?, sdk=?, last_seen_at=?, has_timetable=?, assist=?
               WHERE id_hash=?""",
            (version, version_code, sdk, now, int(has_timetable), int(assist), key)).rowcount
        if updated:
            return True
        if not allow_new:
            return False
        c.execute(
            """INSERT INTO app_installs(id_hash, version, version_code, sdk, first_seen_at, last_seen_at,
                                        has_timetable, assist) VALUES(?,?,?,?,?,?,?,?)""",
            (key, version, version_code, sdk, now, now, int(has_timetable), int(assist)))
    return True


def install_known(install_id: str) -> bool:
    return db.connect().execute("SELECT 1 FROM app_installs WHERE id_hash=?",
                                (install_hash(install_id),)).fetchone() is not None


def install_stats(now: float | None = None) -> dict:
    now = db.now() if now is None else now
    conn = db.connect()
    row = conn.execute(
        """SELECT COUNT(*) AS total,
                  COALESCE(SUM(last_seen_at >= ?), 0) AS active_1d,
                  COALESCE(SUM(last_seen_at >= ?), 0) AS active_7d,
                  COALESCE(SUM(last_seen_at >= ?), 0) AS active_30d,
                  COALESCE(SUM(first_seen_at >= ?), 0) AS new_7d,
                  COALESCE(SUM(has_timetable AND last_seen_at >= ?), 0) AS with_timetable_7d,
                  COALESCE(SUM(assist AND last_seen_at >= ?), 0) AS assist_7d,
                  COALESCE(SUM(assist_ok), 0) AS assist_ok,
                  COALESCE(SUM(assist_fail), 0) AS assist_fail
           FROM app_installs""",
        (now - 86400, now - 7 * 86400, now - 30 * 86400, now - 7 * 86400, now - 7 * 86400,
         now - 7 * 86400)).fetchone()
    versions = [dict(r) for r in conn.execute(
        """SELECT COALESCE(version, '未知') AS version, COUNT(*) AS n FROM app_installs
           WHERE last_seen_at >= ? GROUP BY version ORDER BY MAX(version_code) DESC, n DESC LIMIT 12""",
        (now - 30 * 86400,))]
    return {**dict(row), "versions_30d": versions}


def recent_installs(limit: int = 200) -> list[dict]:
    rows = db.connect().execute(
        """SELECT id_hash, version, sdk, first_seen_at, last_seen_at, has_timetable, assist, assist_ok, assist_fail,
                  assist_at, assist_block_until FROM app_installs ORDER BY last_seen_at DESC LIMIT ?""",
        (limit,)).fetchall()
    # 管理员页只给哈希的前 8 位：够区分是哪一台，也不是能拿来冒充的东西
    out = []
    for r in rows:
        item = dict(r)
        item["id"] = item.pop("id_hash")[:8]
        out.append(item)
    return out


def housekeeping(now: float | None = None) -> None:
    now = db.now() if now is None else now
    with db.tx() as c:
        c.execute("DELETE FROM app_installs WHERE last_seen_at < ?", (now - INSTALL_KEEP,))
    with _lock:
        for uid, until in list(_skip_users.items()):
            if until <= now:
                _skip_users.pop(uid, None)
        _reap_locked(time.monotonic())


# ---------- 中继会话 ----------

class Session:
    """一台手机的一次协助。刷新线程通过 open/send/recv 用它，手机通过 exchange 把字节搬来搬去。"""

    def __init__(self, install_key: str):
        self.id = secrets.token_urlsafe(12)
        self.token = secrets.token_urlsafe(24)
        self.install_key = install_key
        self.started = self.last_poll = time.monotonic()
        self.cond = threading.Condition()
        self.outbox: list[dict] = []
        self.inbox: dict[int, bytearray] = {}
        self.eof: dict[int, str | None] = {}   # 连接号 → None 表示正常关闭，否则是手机报的错误
        self.seq = 0
        self.bytes = 0
        self.done = False
        self.gone = False
        self.uid: int | None = None
        self.outcome: str | None = None

    # -- 刷新线程一侧 --

    def _alive(self) -> None:
        if self.gone or self.done:
            raise RelayFailed("session closed")
        now = time.monotonic()
        if now - self.last_poll > IDLE_TIMEOUT or now - self.started > SESSION_MAX:
            self.gone = True
            raise RelayFailed("phone went away")

    def open(self, host: str, port: int) -> int:
        with self.cond:
            self._alive()
            self.seq += 1
            self.inbox[self.seq] = bytearray()
            self.outbox.append({"op": "open", "conn": self.seq, "host": host, "port": port})
            self.cond.notify_all()
            return self.seq

    def send(self, conn: int, data: bytes) -> None:
        if not data:
            return
        with self.cond:
            self._alive()
            self._count(len(data))
            self.outbox.append({"op": "data", "conn": conn, "data": base64.b64encode(data).decode()})
            self.cond.notify_all()

    def recv(self, conn: int, deadline: float) -> bytes:
        """收到的密文；b"" 表示对端正常关闭。超时、手机走了、手机报错都抛 RelayFailed。"""
        with self.cond:
            while True:
                buf = self.inbox.get(conn)
                if buf:
                    data = bytes(buf)
                    buf.clear()
                    return data
                if conn in self.eof:
                    if self.eof[conn]:
                        raise RelayFailed(f"phone socket error: {self.eof[conn]}")
                    return b""
                self._alive()
                left = deadline - time.monotonic()
                if left <= 0:
                    raise RelayFailed("relay timeout")
                self.cond.wait(min(left, 1.0))

    def close(self, conn: int) -> None:
        with self.cond:
            self.inbox.pop(conn, None)
            self.eof.pop(conn, None)
            if not (self.gone or self.done):
                self.outbox.append({"op": "close", "conn": conn})
                self.cond.notify_all()

    def finish(self, outcome: str) -> None:
        with self.cond:
            self.outcome = outcome
            self.done = True
            self.cond.notify_all()

    def _count(self, n: int) -> None:
        self.bytes += n
        if self.bytes > MAX_SESSION_BYTES:
            self.gone = True
            raise RelayFailed("session byte budget exceeded")

    # -- 手机一侧 --

    def exchange(self, events: list[dict]) -> dict:
        with self.cond:
            self.last_poll = time.monotonic()
            for ev in events:
                conn = ev.get("conn")
                if not isinstance(conn, int) or conn < 1 or conn > self.seq or conn not in self.inbox:
                    continue  # 已关闭或不存在的连接：迟到的数据直接丢
                data = ev.get("data")
                if isinstance(data, str) and data:
                    try:
                        raw = base64.b64decode(data, validate=True)
                    except (ValueError, binascii.Error):
                        self.eof[conn] = "bad data"
                        continue
                    try:
                        self._count(len(raw))
                    except RelayFailed:
                        break
                    self.inbox[conn] += raw
                if ev.get("error"):
                    self.eof[conn] = str(ev["error"])[:40]
                elif ev.get("closed"):
                    self.eof.setdefault(conn, None)
            self.cond.notify_all()
            wait_until = time.monotonic() + LONG_POLL
            while not self.outbox and not self.done and not self.gone:
                left = wait_until - time.monotonic()
                if left <= 0:
                    break
                self.cond.wait(left)
            out, self.outbox = self.outbox, []
            finished = (self.done or self.gone) and not out
            return {"events": out, "done": finished, "outcome": self.outcome if finished else None}


def _reap_locked(now: float) -> None:
    for sid, s in list(_sessions.items()):
        if (s.done or s.gone) and now - s.last_poll > 5 or now - s.started > SESSION_MAX + 30:
            s.gone = True
            with s.cond:
                s.cond.notify_all()
            _sessions.pop(sid, None)


def active_sessions() -> int:
    with _lock:
        return sum(1 for s in _sessions.values() if not (s.done or s.gone))


def state() -> dict:
    with _lock:
        live = [s for s in _sessions.values() if not (s.done or s.gone)]
        return {"enabled": ENABLED, "max_sessions": MAX_SESSIONS, "active": len(live),
                "skipped_users": len(_skip_users)}


# ---------- 经中继的 HTTPS ----------

class _TlsChannel:
    """在中继连接上跑一条服务器作为客户端的 TLS：握手、证书校验、加解密都在这里，手机只见密文。"""

    def __init__(self, session: Session, host: str, port: int, deadline: float):
        self.s = session
        self.deadline = deadline
        self.incoming = ssl.MemoryBIO()
        self.outgoing = ssl.MemoryBIO()
        self.tls = _context().wrap_bio(self.incoming, self.outgoing, server_hostname=host)
        self.conn = session.open(host, port)

    def _flush(self) -> None:
        data = self.outgoing.read()
        if data:
            self.s.send(self.conn, data)

    def _fill(self) -> None:
        data = self.s.recv(self.conn, self.deadline)
        if data:
            self.incoming.write(data)
        else:
            self.incoming.write_eof()

    def handshake(self) -> None:
        while True:
            try:
                self.tls.do_handshake()
                self._flush()
                return
            except ssl.SSLWantReadError:
                self._flush()
                if self.incoming.eof:
                    raise RelayFailed("closed during handshake") from None
                self._fill()

    def write(self, data: bytes) -> None:
        self.tls.write(data)
        self._flush()

    def readinto(self, b) -> int:
        while True:
            try:
                data = self.tls.read(len(b))
                b[:len(data)] = data
                return len(data)
            except ssl.SSLWantReadError:
                self._flush()
                if self.incoming.eof:
                    return 0
                self._fill()
            except (ssl.SSLZeroReturnError, ssl.SSLEOFError):
                return 0  # 学校发完就断（Connection: close），长度由 Content-Length / chunked 把关

    def close(self) -> None:
        self.s.close(self.conn)


class _Raw(io.RawIOBase):
    def __init__(self, channel: _TlsChannel):
        self.channel = channel

    def readable(self) -> bool:
        return True

    def readinto(self, b) -> int:
        return self.channel.readinto(memoryview(b).cast("B"))


class _Sock:
    """http.client.HTTPResponse 只需要 makefile("rb")。"""

    def __init__(self, channel: _TlsChannel):
        self.channel = channel

    def makefile(self, *_args, **_kwargs):
        return io.BufferedReader(_Raw(self.channel))


def _raw_request(request: requests.PreparedRequest, host: str) -> bytes:
    body = request.body
    if body is None:
        body = b""
    elif isinstance(body, str):
        body = body.encode()
    elif not isinstance(body, bytes):
        raise RelayFailed("streaming body not supported")
    lines = [f"{request.method} {request.path_url} HTTP/1.1", f"Host: {host}"]
    for k, v in request.headers.items():
        if k.lower() in ("host", "connection", "content-length", "transfer-encoding"):
            continue
        if any(ch in f"{k}{v}" for ch in "\r\n"):
            raise RelayFailed("bad header")
        lines.append(f"{k}: {v}")
    if body or request.method not in ("GET", "HEAD"):
        lines.append(f"Content-Length: {len(body)}")
    lines.append("Connection: close")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body


class _RelayTransport(HTTPAdapter):
    """requests 的传输层：每个请求在中继上开一条新连接（Connection: close），收完整个响应再交回。"""

    def __init__(self, session: Session):
        self.relay = session
        super().__init__()

    def send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):  # type: ignore[override]
        url = urlsplit(request.url)
        if url.scheme != "https" or url.hostname not in ALLOWED_HOSTS or (url.port or 443) != ALLOWED_PORT:
            # 典型情况：教务会话过期被跳到认证中心。登录类请求不走手机，交回服务器
            raise RelayFailed(f"host not relayed: {url.hostname}")
        deadline = time.monotonic() + RECV_TIMEOUT
        channel = _TlsChannel(self.relay, url.hostname, ALLOWED_PORT, deadline)
        try:
            channel.handshake()
            channel.write(_raw_request(request, url.hostname))
            resp = http.client.HTTPResponse(_Sock(channel), method=request.method)
            resp.begin()
            body = resp.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise RelayFailed("response too large")
        except RelayFailed:
            raise
        except ssl.SSLCertVerificationError as e:
            # 证书对不上：要么手机把连接接去了别处，要么网络被劫持。无论哪种都不能继续
            raise RelayFailed("certificate verification failed") from e
        except (ssl.SSLError, http.client.HTTPException, OSError, ValueError) as e:
            raise RelayFailed(type(e).__name__) from e
        finally:
            channel.close()
        headers = HTTPHeaderDict()
        for k, v in resp.getheaders():
            if k.lower() not in ("transfer-encoding", "content-length"):
                headers.add(k, v)
        headers["Content-Length"] = str(len(body))
        raw = HTTPResponse(body=io.BytesIO(body), headers=headers, status=resp.status, reason=resp.reason,
                           preload_content=False, decode_content=True, original_response=resp,
                           request_method=request.method, request_url=request.url)
        return self.build_response(request, raw)


class RelayAdapter(polite._PoliteAdapter, _RelayTransport):
    """先过 polite 的全站限速和健康度记录，再走中继。MRO：_PoliteAdapter.send → _RelayTransport.send。"""


def relay_session(session: Session) -> polite.PoliteSession:
    s = polite.PoliteSession()
    adapter = RelayAdapter(session)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


# ---------- 派活 ----------

def _pick_user(now: float) -> int | None:
    conn = db.connect()
    for _score, uid in service.candidates(now=now, limit=30):
        if _skip_users.get(uid, 0) > now or service._locks[uid].locked():
            continue
        row = conn.execute("SELECT status, enc_cookies FROM users WHERE id=?", (uid,)).fetchone()
        if row and row["status"] == "ok" and row["enc_cookies"]:
            return uid
    return None


def _take_budget() -> bool:
    """协助刷新同样占后台预算：学校挨的请求数不因为换了出口而变多。"""
    b = scheduler._budget
    if b.tokens < 1:
        return False
    b.tokens -= 1
    return True


def claim(install_id: str, now: float | None = None) -> dict:
    """手机来领任务。没活、没开、名额满都返回 task=None（手机下个周期再来）。"""
    now = db.now() if now is None else now
    if not ENABLED:
        return {"task": None, "reason": "disabled"}
    key = install_hash(install_id)
    row = db.connect().execute("SELECT assist, assist_block_until FROM app_installs WHERE id_hash=?",
                               (key,)).fetchone()
    if not row or not row["assist"]:
        return {"task": None, "reason": "not_enrolled"}
    if (row["assist_block_until"] or 0) > now:
        return {"task": None, "reason": "cooldown"}
    if polite.cooldown_remaining() > 0 or polite.interactive_waiting():
        return {"task": None, "reason": "busy"}
    with _lock:
        _reap_locked(time.monotonic())
        live = [s for s in _sessions.values() if not (s.done or s.gone)]
        if len(live) >= MAX_SESSIONS or any(s.install_key == key for s in live):
            return {"task": None, "reason": "busy"}
        uid = _pick_user(now)
        if uid is None or uid in {s.uid for s in live}:
            return {"task": None, "reason": "idle"}
        if not _take_budget():
            return {"task": None, "reason": "budget"}
        session = Session(key)
        session.uid = uid
        _sessions[session.id] = session
    with db.tx() as c:
        c.execute("UPDATE app_installs SET assist_at=? WHERE id_hash=?", (now, key))
    threading.Thread(target=_run, args=(session,), name=f"assist-{session.id[:6]}", daemon=True).start()
    return {"task": {"session": session.id, "token": session.token, "hosts": sorted(ALLOWED_HOSTS),
                     "port": ALLOWED_PORT}}


def _run(session: Session) -> None:
    uid = session.uid
    outcome = "error"
    try:
        if not service.still_due(uid):
            outcome = "not_due"
            return
        with polite.background_upstream():
            r = service.refresh_user(uid, "assist", session=relay_session(session))
        outcome = "ok" if r.get("ok") else f"school:{r.get('status')}"
        log.info("assist refresh user=%s ok=%s", uid, r.get("ok"))
    except RelayFailed as e:
        outcome = "relay_failed"
        reason = str(e)
        log.info("assist relay failed user=%s: %s", uid, reason)
        if reason.startswith("host not relayed"):
            # 这个人需要去认证中心续期：之后一段时间交给服务器，别反复派给手机
            outcome = "needs_server"
            with _lock:
                _skip_users[uid] = db.now() + SKIP_USER_FOR
    except service.Busy:
        outcome = "busy"
    except polite.UpstreamYield:
        outcome = "yield"
    except KeyError:
        outcome = "gone"
    except Exception as e:  # 只记类型：原文可能带 ticket
        log.error("assist refresh crashed user=%s: %s", uid, type(e).__name__)
    finally:
        session.finish(outcome)
        _record_outcome(session.install_key, outcome)


def _record_outcome(key: str, outcome: str) -> None:
    """手机这边的成败：教务自己报错、没轮到、要交回服务器，都不怪手机。"""
    if outcome == "relay_failed":
        sql = """UPDATE app_installs SET assist_fail=assist_fail+1, assist_streak=assist_streak+1,
                     assist_block_until=CASE WHEN assist_streak+1 >= ? THEN ? ELSE assist_block_until END
                 WHERE id_hash=?"""
        args = (FAIL_STREAK_LIMIT, db.now() + FAIL_COOLDOWN, key)
    elif outcome == "ok" or outcome.startswith("school:") or outcome == "needs_server":
        sql = "UPDATE app_installs SET assist_ok=assist_ok+?, assist_streak=0 WHERE id_hash=?"
        args = (1 if outcome == "ok" else 0, key)
    else:
        return
    try:
        with db.tx() as c:
            c.execute(sql, args)
    except Exception as e:
        log.warning("assist outcome not recorded: %s", type(e).__name__)


def exchange(session_id: str, token: str, events: list[dict]) -> dict | None:
    """手机交换数据；会话不存在或 token 不对返回 None。"""
    with _lock:
        session = _sessions.get(session_id)
    if session is None or not secrets.compare_digest(session.token, token):
        return None
    return session.exchange(events)
