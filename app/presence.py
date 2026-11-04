"""谁在线：内存里记下最近的请求，管理员页展示用。只有 1 个 worker，所以进程内计数就是全站的。"""
from __future__ import annotations

import collections
import hashlib
import threading
import time

from . import db

ONLINE_WINDOW = 300            # 秒：多久内有请求算“在线”
MAX_TRACKED = 5000             # 在线字典硬上限：防扫描/洪水撑大内存

_presence: collections.OrderedDict[int, tuple[float, str]] = (
    globals()["_presence"] if isinstance(globals().get("_presence"), collections.OrderedDict)
    else collections.OrderedDict(globals().get("_presence") or {})
)
_visitors: collections.OrderedDict[str, float] = (
    globals()["_visitors"] if isinstance(globals().get("_visitors"), collections.OrderedDict)
    else collections.OrderedDict(globals().get("_visitors") or {})
)
_presence_lock: threading.Lock = globals().get("_presence_lock") or threading.Lock()


def mark_user(uid: int, via: str) -> None:
    now = time.time()
    with _presence_lock:
        _presence[uid] = (now, via)
        _presence.move_to_end(uid)
        while len(_presence) > MAX_TRACKED:
            _presence.popitem(last=False)


def mark_visitor(ip: str) -> None:
    key = hashlib.sha256(ip.encode()).hexdigest()[:16]
    now = time.time()
    with _presence_lock:
        _visitors[key] = now
        _visitors.move_to_end(key)
        while _visitors:
            oldest_k, oldest_t = next(iter(_visitors.items()))
            if now - oldest_t <= ONLINE_WINDOW and len(_visitors) <= MAX_TRACKED:
                break
            del _visitors[oldest_k]


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
