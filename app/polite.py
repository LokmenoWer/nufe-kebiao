"""对学校服务器"克制"的 HTTP 会话：全站每秒最多 1 次请求 + 并发上限 + 少量随机抖动。

所有发往 authserver / jwxt 的请求（包括重定向的每一跳）都经过 send()，
因此这里是唯一的节流点。用户侧不再单独限制手动刷新次数，请求多了只会在这里排队。
（进程内的锁只对单进程有效，所以服务必须只跑 1 个 worker。）
"""
from __future__ import annotations

import contextvars
import os
import random
import threading
import time
from contextlib import contextmanager

import requests

USER_AGENT = os.getenv(
    "UPSTREAM_USER_AGENT",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 nufe-kebiao/1.0",
)


def _env_number(name: str, default: float, *, integer: bool = False) -> float:
    raw = os.getenv(name, "").strip()
    try:
        value = int(raw) if integer else float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


MAX_CONCURRENCY = int(_env_number("UPSTREAM_MAX_CONCURRENCY", 2, integer=True))
HARD_MIN_INTERVAL = 1.0  # 秒：任意两次请求的发起间隔下限，即全站每秒最多 1 次


def _min_interval(raw: str | None) -> float:
    """配置只能把间隔调大，不能低于硬下限。"""
    try:
        return max(HARD_MIN_INTERVAL, float(raw or HARD_MIN_INTERVAL))
    except ValueError:
        return HARD_MIN_INTERVAL


MIN_INTERVAL = _min_interval(os.getenv("UPSTREAM_MIN_INTERVAL"))
JITTER = 0.2  # 在最小间隔之上再随机多等 0~0.2 秒
TIMEOUT = _env_number("UPSTREAM_TIMEOUT", 20.0)

_slots = threading.BoundedSemaphore(MAX_CONCURRENCY)
_pace_lock = threading.Lock()
_last_request_at = 0.0

# 交互请求（登录、手动刷新、验证码）占用上游时，后台刷新让路
_role: contextvars.ContextVar[str] = contextvars.ContextVar("upstream_role", default="interactive")
_interactive_waiters = 0
_waiters_lock = threading.Lock()


class UpstreamYield(Exception):
    """后台请求发现有交互请求在等上游，本轮让出。"""


@contextmanager
def background_upstream():
    token = _role.set("background")
    try:
        yield
    finally:
        _role.reset(token)


def interactive_waiting() -> bool:
    with _waiters_lock:
        return _interactive_waiters > 0


def _pace() -> None:
    global _last_request_at
    with _pace_lock:  # 持锁等待：所有线程的请求按发起时刻严格排队
        target = _last_request_at + MIN_INTERVAL + random.uniform(0, JITTER)
        while (remaining := target - time.monotonic()) > 0:
            time.sleep(remaining)
        _last_request_at = time.monotonic()


class PoliteSession(requests.Session):
    def __init__(self) -> None:
        super().__init__()
        self.headers.update({
            "User-Agent": USER_AGENT,
            "Accept-Language": "zh-CN,zh;q=0.9",
        })

    def send(self, request, **kwargs):  # type: ignore[override]
        kwargs.setdefault("timeout", TIMEOUT)
        interactive = _role.get() != "background"
        if not interactive and interactive_waiting():
            raise UpstreamYield()
        if interactive:
            with _waiters_lock:
                global _interactive_waiters
                _interactive_waiters += 1
        try:
            with _slots:
                # 拿到名额后再看一眼：等待期间可能来了交互请求
                if not interactive and interactive_waiting():
                    raise UpstreamYield()
                _pace()
                return super().send(request, **kwargs)
        finally:
            if interactive:
                with _waiters_lock:
                    _interactive_waiters -= 1


def dump_cookies(session: requests.Session) -> list[dict]:
    return [
        {
            "name": c.name, "value": c.value, "domain": c.domain, "path": c.path,
            "secure": c.secure, "expires": c.expires,
        }
        for c in session.cookies
    ]


def load_cookies(session: requests.Session, cookies: list[dict]) -> None:
    if not isinstance(cookies, list):
        raise TypeError("cookie jar must be a list")
    for c in cookies:
        if (not isinstance(c, dict) or not isinstance(c.get("name"), str) or not isinstance(c.get("value"), str)
                or not isinstance(c.get("domain"), str) or not isinstance(c.get("path"), str)):
            continue
        try:
            session.cookies.set(
                c["name"], c["value"], domain=c["domain"], path=c["path"],
                secure=bool(c.get("secure")),
                expires=c.get("expires") if isinstance(c.get("expires"), int | float) else None,
            )
        except (AttributeError, TypeError, ValueError):
            continue
