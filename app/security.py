from __future__ import annotations

import base64
import hmac
import secrets
import threading
import time
from collections import OrderedDict, deque
from urllib.parse import urlparse

from fastapi import Depends, HTTPException, Request, Response

from . import crypto, db, presence
from .config import COOKIE_PATH, COOKIE_SECURE, PUBLIC_BASE_URL, SESSION_DAYS_LONG, SESSION_DAYS_SHORT

SESSION_COOKIE = "kb_session"
# "这个浏览器上次登录的是谁"：扫码登录前还不知道用户，用它复用该用户已被学校信任的设备 ID，
# 避免每次扫码都触发短信二次认证。内容是加密的用户 id，设备 ID 本身不出服务器。
DEVICE_HINT_COOKIE = "kb_dev"
DEVICE_HINT_DAYS = 180
MAX_SESSIONS_PER_USER = 8
RENEW_EVERY = 86400  # 秒：滑动续期的最小间隔


def create_session(response: Response, uid: int, keep_login: bool = False) -> str:
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    now = db.now()
    days = SESSION_DAYS_LONG if keep_login else SESSION_DAYS_SHORT
    with db.tx() as c:
        # 用户可能在登录流程结束前删除了账号：不要给不存在的外键建会话
        if not c.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
            raise HTTPException(401, "账号已删除，请重新登录")
        c.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        c.execute("""INSERT INTO sessions(token_hash, user_id, csrf, created_at, expires_at, persistent)
                     VALUES(?,?,?,?,?,?)""",
                  (crypto.token_hash(token), uid, csrf, now, now + days * 86400, int(keep_login)))
        # 允许手机、电脑同时在线，但每人最多保留最近 MAX_SESSIONS_PER_USER 个会话
        c.execute("""DELETE FROM sessions WHERE user_id=? AND token_hash NOT IN (
                         SELECT token_hash FROM sessions WHERE user_id=?
                         ORDER BY created_at DESC, rowid DESC LIMIT ?)""",
                  (uid, uid, MAX_SESSIONS_PER_USER))
    _set_session_cookie(response, token, keep_login)
    return csrf


def _set_session_cookie(response: Response, token: str, persistent: bool) -> None:
    # 不保持登录时不设 max_age：浏览器会话 cookie，关闭浏览器即失效
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_DAYS_LONG * 86400 if persistent else None,
                        httponly=True, secure=COOKIE_SECURE, samesite="lax", path=COOKIE_PATH)


def renew_session(request: Request, response: Response, sess) -> None:
    """滑动续期：只要还在用，会话就不会因为“从登录起满 N 天”而突然失效。

    每天最多续一次（减少写库和 Set-Cookie）；勾了“保持登录”的同时把浏览器 cookie 的 max_age 续满。
    """
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return
    persistent = bool(sess["session_persistent"])
    days = SESSION_DAYS_LONG if persistent else SESSION_DAYS_SHORT
    new_expiry = db.now() + days * 86400
    if new_expiry - sess["expires_at"] < RENEW_EVERY:
        return
    with db.tx() as c:
        c.execute("UPDATE sessions SET expires_at=? WHERE token_hash=?", (new_expiry, crypto.token_hash(token)))
    if persistent:
        _set_session_cookie(response, token, True)
        set_device_hint(response, sess["id"])


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
    s.csrf, s.expires_at, s.persistent AS session_persistent, u.id, u.student_id, u.name, u.created_at, u.cred_mode,
    u.password_consent_at, u.status, u.fail_count, u.last_error, u.last_refresh_at,
    u.last_success_at, u.next_refresh_at, u.current_semester_id, u.semesters_json,
    u.ics_token_hash, u.refresh_hours, u.sso_session_only, u.keepalive_at, u.sso_lost_at, u.last_seen_at
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
    presence.mark_user(row["id"], "web")
    raw_name = row["name"]
    if isinstance(raw_name, bytes | bytearray | memoryview):
        try:
            name = crypto.decrypt(bytes(raw_name), "user_name", row["id"]).decode()
        except (crypto.CryptoError, UnicodeDecodeError):
            name = None
        return {**dict(row), "name": name}
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
    """滑动窗口限速器：OrderedDict 按最近访问排序，均摊 O(1) 淘汰过期项并限制最大键数，防随机键撑爆内存。"""

    def __init__(self, limit: int, window: float, max_keys: int = 4096, prev: RateLimiter | None = None):
        self.limit, self.window, self.max_keys = limit, window, max_keys
        self._hits: OrderedDict[str, deque] = (
            prev._hits if prev is not None and isinstance(getattr(prev, "_hits", None), OrderedDict)
            else OrderedDict()
        )
        self._evicted: OrderedDict[str, tuple[int, float]] = (
            prev._evicted if prev is not None and isinstance(getattr(prev, "_evicted", None), OrderedDict)
            else OrderedDict()
        )
        self._lock: threading.Lock = getattr(prev, "_lock", None) or threading.Lock()

    def _evict_one(self, cutoff: float) -> bool:
        """腾一个位置：只淘汰已过期键或仅试探 1 次的孤立键，绝不把正在被爆破的多频次目标清掉。"""
        for k, q in list(self._hits.items()):
            while q and q[0] < cutoff:
                q.popleft()
            if not q:
                del self._hits[k]
                return True
            if len(q) == 1:
                last_ts = q[-1]
                del self._hits[k]
                if self.limit > 1:
                    self._evicted[k] = (1, last_ts + self.window)
                    while len(self._evicted) > max(self.max_keys * 4, 16384):
                        self._evicted.popitem(last=False)
                return True
        return False

    def hit(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window
        with self._lock:
            if not self._hits and self._evicted:
                self._evicted.clear()
            while self._hits:
                oldest_k, oldest_q = next(iter(self._hits.items()))
                while oldest_q and oldest_q[0] < cutoff:
                    oldest_q.popleft()
                if oldest_q:
                    break
                del self._hits[oldest_k]
            q = self._hits.get(key)
            if q is not None:
                while q and q[0] < cutoff:
                    q.popleft()
                self._hits.move_to_end(key)
            else:
                restored = self._evicted.pop(key, None)
                if len(self._hits) >= self.max_keys and not self._evict_one(cutoff):
                    if restored is not None and restored[1] > now:
                        self._evicted[key] = restored
                    return False
                q = deque()
                if restored is not None and restored[1] > now:
                    ts = max(cutoff, restored[1] - self.window)
                    q.extend(ts for _ in range(restored[0]))
                self._hits[key] = q
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True


# 每次站点登录都会真实打到学校 CAS，必须同时按 IP 和学号限速，避免撞库和触发账号锁定
login_by_ip = RateLimiter(10, 900, prev=globals().get("login_by_ip"))
login_by_student = RateLimiter(5, 900, prev=globals().get("login_by_student"))
captcha_refresh_by_ip = RateLimiter(20, 60, prev=globals().get("captcha_refresh_by_ip"))
captcha_refresh_by_flow = RateLimiter(12, 300, prev=globals().get("captcha_refresh_by_flow"))
