"""服务器管理员页：日志、在线人数、后台刷新排期。

- 密码只以 PBKDF2 哈希的形式放在 .env 的 ADMIN_PASSWORD_HASH 里；没配就整个后台不开放（404）。
  生成：python -m app.admin hash
- 管理员会话只存在内存里（只有 1 个 worker），服务重启后需要重新登录。
- 日志是进程内的环形缓冲，只保留最近 LOG_CAPACITY 条；更早的看 journalctl -u kebiao。
- 学号、姓名在页面上脱敏显示：管理员看排期不需要知道具体是谁。
"""
from __future__ import annotations

import base64
import collections
import hashlib
import hmac
import logging
import os
import platform
import resource
import secrets
import sys
import threading
import time

from fastapi import Depends, HTTPException, Request, Response

from . import db, polite, presence, refresh_policy, scheduler, service
from .config import COOKIE_PATH, COOKIE_SECURE, DB_PATH, SCHEDULER_ENABLED, SCHEDULER_TICK

ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "").strip()
ENABLED = bool(ADMIN_PASSWORD_HASH)
ADMIN_COOKIE = "kb_admin"
SESSION_TTL = 12 * 3600
PBKDF2_ITERATIONS = 600_000
LOG_CAPACITY = 2000
STARTED_AT = time.time()

log = logging.getLogger("kebiao.admin")


# ---------- 密码 ----------

def hash_password(password: str, iterations: int = PBKDF2_ITERATIONS) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"pbkdf2_sha256${iterations}${b64(salt)}${b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt, expected = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), int(iterations))
        return hmac.compare_digest(dk, base64.b64decode(expected))
    except (ValueError, TypeError):
        return False


# ---------- 会话（内存） ----------

_sessions: dict[str, tuple[float, str]] = {}   # token -> (过期时刻, csrf)
_sessions_lock = threading.Lock()


def login(response: Response) -> str:
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    now = time.time()
    with _sessions_lock:
        for k in [k for k, (exp, _) in _sessions.items() if exp < now]:
            del _sessions[k]
        _sessions[token] = (now + SESSION_TTL, csrf)
    # SameSite=Strict：后台没有从别的站点跳进来的需要
    response.set_cookie(ADMIN_COOKIE, token, max_age=SESSION_TTL, httponly=True, secure=COOKIE_SECURE,
                        samesite="strict", path=COOKIE_PATH)
    return csrf


def logout(request: Request, response: Response) -> None:
    token = request.cookies.get(ADMIN_COOKIE)
    if token:
        with _sessions_lock:
            _sessions.pop(token, None)
    response.delete_cookie(ADMIN_COOKIE, path=COOKIE_PATH, secure=COOKIE_SECURE, httponly=True, samesite="strict")


def current_admin(request: Request) -> str:
    """返回该会话的 csrf。后台未开放时一律 404，不暴露它的存在。"""
    if not ENABLED:
        raise HTTPException(404)
    token = request.cookies.get(ADMIN_COOKIE)
    with _sessions_lock:
        entry = _sessions.get(token or "")
    if not entry or entry[0] < time.time():
        raise HTTPException(401, "未登录")
    return entry[1]


def admin_csrf(request: Request, csrf: str = Depends(current_admin)) -> str:
    if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), csrf):
        raise HTTPException(403, "CSRF 校验失败")
    return csrf


# ---------- 日志环形缓冲 ----------

class RingHandler(logging.Handler):
    def __init__(self, capacity: int = LOG_CAPACITY):
        super().__init__(logging.INFO)
        self.records: collections.deque = collections.deque(maxlen=capacity)
        self.seq = 0
        self._ring_lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(record.msg)
        with self._ring_lock:
            self.seq += 1
            self.records.append({"seq": self.seq, "at": record.created, "level": record.levelname,
                                 "logger": record.name, "message": msg[:2000]})

    def since(self, after: int, level: str | None, query: str | None, limit: int) -> list[dict]:
        floor = logging.getLevelName(level) if level else 0
        floor = floor if isinstance(floor, int) else 0
        q = (query or "").lower()
        with self._ring_lock:
            rows = list(self.records)
        out = [r for r in rows if r["seq"] > after and logging.getLevelName(r["level"]) >= floor
               and (not q or q in r["message"].lower() or q in r["logger"].lower())]
        return out[-limit:]


ring = RingHandler()


def install_log_capture() -> None:
    # uvicorn 的两个 logger 不向 root 传播，要单独挂；挂在 logger 上的脱敏 filter 先于 handler 执行
    for name in ("", "uvicorn", "uvicorn.access"):
        lg = logging.getLogger(name)
        if ring not in lg.handlers:
            lg.addHandler(ring)


# ---------- 排期 ----------

def mask_id(student_id: str | None) -> str:
    """2023012345 → 2023****45：留年级和末两位，够区分，认不出是谁。"""
    s = student_id or ""
    if len(s) > 6:
        return s[:4] + "*" * (len(s) - 6) + s[-2:]
    return s[:1] + "*" * max(len(s) - 1, 0)


def mask_name(name: str | None) -> str:
    return (name[0] + "*" * (len(name) - 1)) if name else ""


def schedule(now: float | None = None) -> dict:
    now = db.now() if now is None else now
    conn = db.connect()
    rows = conn.execute(
        f"""SELECT id, student_id, name, cred_mode, status, fail_count, last_error, last_refresh_at, next_refresh_at,
                   refresh_request_reason, sso_session_only, keepalive_at, sso_touched_at, sso_lost_at,
                   {service._SIGNAL_COLS} FROM users""").fetchall()
    queue = {uid: score for score, uid in service.candidates(now=now, limit=1_000_000)}
    items = []
    for r in rows:
        sig = service.signals_of(r)
        tgt = refresh_policy.target(sig, now)
        if r["status"] in ("needs_rebind", "captcha"):
            nxt = None
        elif r["status"] == "error":
            nxt = r["next_refresh_at"]
        else:
            nxt = refresh_policy.next_due(sig, now)
        pending = bool(sig.refresh_requested_at and sig.refresh_requested_at > (sig.last_success_at or 0))
        items.append({
            "uid": r["id"], "student": mask_id(r["student_id"]), "name": mask_name(r["name"]),
            "status": r["status"], "fail_count": r["fail_count"], "last_error": r["last_error"],
            "sso_short": service.sso_short(r["cred_mode"], r["sso_session_only"]),
            "keepalive_at": r["keepalive_at"], "sso_touched_at": r["sso_touched_at"], "sso_lost_at": r["sso_lost_at"],
            "next_refresh_at": nxt, "target_minutes": round(tgt.seconds / 60),
            "reason": refresh_policy.reason(sig, now),
            "pending": pending, "pending_reason": r["refresh_request_reason"] if pending else None,
            "score": round(queue[r["id"]], 2) if r["id"] in queue else None,
            "last_refresh_at": r["last_refresh_at"], "last_success_at": r["last_success_at"],
            "last_seen_at": r["last_seen_at"], "next_class_at": r["next_class_at"],
        })
    # 已在队里的按逾期程度排最前，其余按预计时间；暂停的（无下次时间）放最后
    items.sort(key=lambda i: (i["score"] is None, -(i["score"] or 0), i["next_refresh_at"] is None,
                              i["next_refresh_at"] or 0))
    penalty, cooldown = polite.health()
    b = scheduler._budget
    return {
        "now": now,
        "scheduler": {
            "enabled": SCHEDULER_ENABLED, "alive": scheduler.alive(), "tick_seconds": SCHEDULER_TICK,
            "budget_rps": round(b.rate, 3), "max_rps": b.max_rate, "tokens": round(b.tokens, 2),
            "upstream_penalty": round(penalty, 2), "upstream_cooldown": round(cooldown, 1),
            "last_tick": scheduler.last_tick,
        },
        "due": len(queue),
        "items": items,
    }


def recent_refreshes(limit: int = 60) -> list[dict]:
    rows = db.connect().execute(
        """SELECT l.at, l.trigger, l.ok, l.message, u.student_id FROM refresh_log l
           JOIN users u ON u.id = l.user_id ORDER BY l.at DESC LIMIT ?""", (limit,)).fetchall()
    return [{"at": r["at"], "trigger": r["trigger"], "ok": bool(r["ok"]), "message": r["message"],
             "student": mask_id(r["student_id"])} for r in rows]


# ---------- 概况 ----------

def overview() -> dict:
    conn = db.connect()
    status = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) AS n FROM users GROUP BY status")}
    since = time.time() - 86400
    refresh_24h = {f"{r['trigger']}_{'ok' if r['ok'] else 'fail'}": r["n"] for r in conn.execute(
        "SELECT trigger, ok, COUNT(*) AS n FROM refresh_log WHERE at >= ? GROUP BY trigger, ok", (since,))}
    db_bytes = sum(os.path.getsize(p) for p in (DB_PATH, DB_PATH + "-wal") if os.path.exists(p))
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {
        "started_at": STARTED_AT, "now": time.time(),
        "python": sys.version.split()[0], "platform": platform.platform(terse=True),
        "pid": os.getpid(), "max_rss_mb": round(rss / (1024 * 1024 if sys.platform == "darwin" else 1024), 1),
        "db_mb": round(db_bytes / 1024 / 1024, 2),
        "users": sum(status.values()), "user_status": status,
        "ics_subscribers": conn.execute("SELECT COUNT(*) FROM users WHERE ics_token_hash IS NOT NULL").fetchone()[0],
        "widget_users": conn.execute("SELECT COUNT(*) FROM users WHERE widget_token_hash IS NOT NULL").fetchone()[0],
        "email_users": conn.execute("SELECT COUNT(*) FROM users WHERE enc_email IS NOT NULL").fetchone()[0],
        "refresh_24h": refresh_24h,
        "online": presence.online(),
    }


if __name__ == "__main__":  # python -m app.admin hash
    import getpass

    if sys.argv[1:] != ["hash"]:
        sys.exit("用法：python -m app.admin hash")
    pw = getpass.getpass("管理员密码：")
    if len(pw) < 12:
        sys.exit("密码至少 12 位")
    print(f"ADMIN_PASSWORD_HASH={hash_password(pw)}")
