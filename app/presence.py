"""谁在线：内存里记下最近的请求，管理员页展示用。只有 1 个 worker，所以进程内计数就是全站的。"""
from __future__ import annotations

import collections
import hashlib
import threading
import time

from . import db

ONLINE_WINDOW = 300            # 秒：多久内有请求算“在线”

_presence: dict[int, tuple[float, str]] = {}   # uid -> (最近请求时刻, 渠道 web / widget / ics)
_visitors: dict[str, float] = {}               # IP 的哈希 -> 最近请求时刻（含未登录的访客）
_presence_lock = threading.Lock()


def mark_user(uid: int, via: str) -> None:
    with _presence_lock:
        _presence[uid] = (time.time(), via)


def mark_visitor(ip: str) -> None:
    key = hashlib.sha256(ip.encode()).hexdigest()[:16]
    now = time.time()
    with _presence_lock:
        _visitors[key] = now
        if len(_visitors) > 5000:
            for k in [k for k, t in _visitors.items() if now - t > ONLINE_WINDOW]:
                del _visitors[k]


def online(now: float | None = None) -> dict:
    now = time.time() if now is None else now
    with _presence_lock:
        users = {uid: v for uid, v in _presence.items() if now - v[0] <= ONLINE_WINDOW}
        visitors = sum(1 for t in _visitors.values() if now - t <= ONLINE_WINDOW)
    by_via = collections.Counter(via for _, via in users.values())
    conn = db.connect()
    active = lambda sec: conn.execute(  # noqa: E731
        "SELECT COUNT(*) FROM users WHERE last_seen_at >= ?", (now - sec,)).fetchone()[0]
    return {
        "window_seconds": ONLINE_WINDOW,
        "users": len(users),
        "by_via": dict(by_via),
        "visitors": visitors,
        "active_1h": active(3600),
        "active_24h": active(86400),
        "active_7d": active(7 * 86400),
        "sessions": conn.execute("SELECT COUNT(*) FROM sessions WHERE expires_at > ?", (now,)).fetchone()[0],
    }
