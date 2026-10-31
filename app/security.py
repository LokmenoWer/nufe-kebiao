from __future__ import annotations

import base64
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque
from urllib.parse import urlparse

from fastapi import Depends, HTTPException, Request, Response

from . import crypto, db
from .config import COOKIE_PATH, COOKIE_SECURE, PUBLIC_BASE_URL, SESSION_DAYS_LONG, SESSION_DAYS_SHORT

SESSION_COOKIE = "kb_session"
# "这个浏览器上次登录的是谁"：扫码登录前还不知道用户，用它复用该用户已被学校信任的设备 ID，
# 避免每次扫码都触发短信二次认证。内容是加密的用户 id，设备 ID 本身不出服务器。
DEVICE_HINT_COOKIE = "kb_dev"
DEVICE_HINT_DAYS = 180
MAX_SESSIONS_PER_USER = 8


def create_session(response: Response, uid: int, keep_login: bool = False) -> str:
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    now = db.now()
    days = SESSION_DAYS_LONG if keep_login else SESSION_DAYS_SHORT
    with db.tx() as c:
        # 用户可能在登录流程结束前删除了账号：不要给不存在的外键建会话
        if not c.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
            raise HTTPException(401, "账号已删除，请重新登录")
        c.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        c.execute("INSERT INTO sessions(token_hash, user_id, csrf, created_at, expires_at) VALUES(?,?,?,?,?)",
                  (crypto.token_hash(token), uid, csrf, now, now + days * 86400))
        # 允许手机、电脑同时在线，但每人最多保留最近 MAX_SESSIONS_PER_USER 个会话
        c.execute("""DELETE FROM sessions WHERE user_id=? AND token_hash NOT IN (
                         SELECT token_hash FROM sessions WHERE user_id=?
                         ORDER BY created_at DESC, rowid DESC LIMIT ?)""",
                  (uid, uid, MAX_SESSIONS_PER_USER))
    # 不保持登录时不设 max_age：浏览器会话 cookie，关闭浏览器即失效
    response.set_cookie(SESSION_COOKIE, token, max_age=days * 86400 if keep_login else None, httponly=True,
                        secure=COOKIE_SECURE, samesite="lax", path=COOKIE_PATH)
    return csrf


def destroy_session(request: Request, response: Response) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        with db.tx() as c:
            c.execute("DELETE FROM sessions WHERE token_hash=?", (crypto.token_hash(token),))
    clear_session_cookie(response)


def destroy_all_sessions(uid: int, response: Response) -> None:
    with db.tx() as c:
        c.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
    clear_session_cookie(response)


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path=COOKIE_PATH, secure=COOKIE_SECURE, httponly=True, samesite="lax")


def set_device_hint(response: Response, uid: int) -> None:
    blob = crypto.encrypt(str(uid).encode(), "devhint", 0)
    response.set_cookie(DEVICE_HINT_COOKIE, base64.urlsafe_b64encode(blob).decode().rstrip("="),
                        max_age=DEVICE_HINT_DAYS * 86400, httponly=True, secure=COOKIE_SECURE,
                        samesite="lax", path=COOKIE_PATH)


def clear_device_hint(response: Response) -> None:
    response.delete_cookie(DEVICE_HINT_COOKIE, path=COOKIE_PATH, secure=COOKIE_SECURE, httponly=True, samesite="lax")


def device_hint_uid(request: Request) -> int | None:
    raw = request.cookies.get(DEVICE_HINT_COOKIE)
    if not raw or len(raw) > 200:
        return None
    try:
        blob = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        return int(crypto.decrypt(blob, "devhint", 0))
    except Exception:  # 被篡改 / 换过密钥：当作没有
        return None


# 会话校验不带出 cookie / 密码 / 设备指纹 / 订阅 token 密文
_SESSION_USER_COLS = """
    s.csrf, s.expires_at, u.id, u.student_id, u.name, u.created_at, u.cred_mode,
    u.password_consent_at, u.status, u.fail_count, u.last_error, u.last_refresh_at,
    u.last_success_at, u.next_refresh_at, u.current_semester_id, u.semesters_json,
    u.ics_token_hash, u.refresh_hours
"""


def current_session(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(401, "未登录")
    row = db.connect().execute(
        f"""SELECT {_SESSION_USER_COLS} FROM sessions s JOIN users u ON u.id = s.user_id
           WHERE s.token_hash=? AND s.expires_at > ?""", (crypto.token_hash(token), db.now())).fetchone()
    if not row:
        raise HTTPException(401, "会话已失效")
    return row


def csrf_protected(request: Request, sess=Depends(current_session)):
    sent = request.headers.get("x-csrf-token", "")
    if not hmac.compare_digest(sent, sess["csrf"]):
        raise HTTPException(403, "CSRF 校验失败")
    return sess


_ALLOWED_ORIGIN = urlparse(PUBLIC_BASE_URL)
_LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def _hostname(netloc: str) -> str:
    host = netloc.rsplit("@", 1)[-1]
    if host.startswith("["):
        return host[1:].split("]", 1)[0]
    return host.split(":", 1)[0]


def origin_matches(src: str) -> bool:
    """来源必须和 PUBLIC_BASE_URL 一致。

    配置本身是本机地址时，localhost 和 127.0.0.1 互相放行，方便本地预览。
    不看请求里的 Host 头，避免非浏览器客户端自报 Host 通过校验。
    """
    u = urlparse(src)
    if (u.scheme, u.netloc) == (_ALLOWED_ORIGIN.scheme, _ALLOWED_ORIGIN.netloc):
        return True
    return (
        u.scheme in ("http", "https")
        and _hostname(_ALLOWED_ORIGIN.netloc) in _LOOPBACK
        and _hostname(u.netloc) in _LOOPBACK
    )


def origin_ok(request: Request) -> bool:
    """对所有写请求校验来源（同时挡住登录 CSRF）。

    优先 Origin/Referer；页面设置了 Referrer-Policy: no-referrer，部分浏览器/隐私插件会
    同时去掉 Referer，此时用浏览器自己写入、脚本无法伪造的 Sec-Fetch-Site 兜底。
    """
    src = request.headers.get("origin") or request.headers.get("referer")
    if src:
        return origin_matches(src)
    return request.headers.get("sec-fetch-site") == "same-origin"


class RateLimiter:
    def __init__(self, limit: int, window: float):
        self.limit, self.window = limit, window
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            stale = []
            for k, q in self._hits.items():
                while q and q[0] < now - self.window:
                    q.popleft()
                if not q:
                    stale.append(k)
            for k in stale:
                del self._hits[k]
            q = self._hits.get(key)
            if q is None:
                q = deque()
                self._hits[key] = q
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True


# 每次站点登录都会真实打到学校 CAS，必须同时按 IP 和学号限速，避免撞库和触发账号锁定
login_by_ip = RateLimiter(10, 900)
login_by_student = RateLimiter(5, 900)
