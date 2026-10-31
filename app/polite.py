"""对学校服务器"克制"的 HTTP 会话：全站每秒最多 1 次请求 + 并发上限 + 少量随机抖动。

所有发往 authserver / jwxt 的请求（包括重定向的每一跳）都经过 send()，
因此这里是唯一的节流点。另外按学校的反应自适应放慢：
- 429 / 503 带 Retry-After：全站暂停到指定时间（上限 10 分钟）；不带则暂停 30 秒；
- 5xx / 超时 / 连接失败：在最小间隔上再加一段惩罚间隔，连续出错翻倍（上限 60 秒），正常响应后逐步减半。
暂停期间后台刷新直接让路；交互请求最多等 INTERACTIVE_MAX_WAIT 秒，再久就报“学校繁忙”。
（进程内的锁只对单进程有效，所以服务必须只跑 1 个 worker。）
"""
from __future__ import annotations

import contextvars
import math
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
    """后台请求发现有交互请求在等上游（或全站在暂停访问），本轮让出。"""


class UpstreamCooldown(requests.exceptions.ConnectionError):
    """学校要求暂停访问，而交互请求不值得为此等太久。"""

    def __init__(self, retry_after: int):
        super().__init__(f"upstream cooling down for {retry_after}s")
        self.retry_after = retry_after


# ---- 上游健康度：自适应退避 ----
MAX_PENALTY = 60.0           # 秒：出错时额外增加的请求间隔上限
MAX_COOLDOWN = 600.0         # 秒：Retry-After 最多认 10 分钟
DEFAULT_COOLDOWN = 30.0      # 秒：429/503 没给 Retry-After 时暂停多久
INTERACTIVE_MAX_WAIT = 10.0  # 秒：暂停期间交互请求最多陪等多久
_health_lock = threading.Lock()
_penalty = 0.0
_cooldown_until = 0.0


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None  # HTTP-date 形式少见，按默认暂停处理
    return min(max(seconds, 1.0), MAX_COOLDOWN)


def _record(status: int | None, retry_after: str | None = None) -> None:
    """status=None 表示超时 / 连接失败。"""
    global _penalty, _cooldown_until
    with _health_lock:
        if status in (429, 503):
            pause = _retry_after(retry_after) or DEFAULT_COOLDOWN
            _cooldown_until = max(_cooldown_until, time.monotonic() + pause)
        if status is None or status >= 500 or status == 429:
            _penalty = min(MAX_PENALTY, max(1.0, _penalty * 2))
        else:
            _penalty = _penalty / 2 if _penalty >= 0.5 else 0.0


def health() -> tuple[float, float]:
    """(当前惩罚间隔, 剩余暂停秒数)：调度器据此调整后台刷新预算。"""
    with _health_lock:
        return _penalty, max(0.0, _cooldown_until - time.monotonic())


def cooldown_remaining() -> float:
    with _health_lock:
        return max(0.0, _cooldown_until - time.monotonic())


def reset_health() -> None:
    """测试用：清空退避状态。"""
    global _penalty, _cooldown_until
    with _health_lock:
        _penalty = 0.0
        _cooldown_until = 0.0


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


def _pace(interactive: bool) -> None:
    global _last_request_at
    with _pace_lock:  # 持锁等待：所有线程的请求按发起时刻严格排队
        with _health_lock:
            penalty, resume_at = _penalty, _cooldown_until
        # 排队期间别人可能刚撞上 429：不能持锁陪睡到暂停结束
        pause = resume_at - time.monotonic()
        if pause > 0 and not interactive:
            raise UpstreamYield()
        if pause > INTERACTIVE_MAX_WAIT:
            raise UpstreamCooldown(math.ceil(pause))
        target = max(_last_request_at + MIN_INTERVAL + penalty + random.uniform(0, JITTER), resume_at)
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
        if not interactive and (interactive_waiting() or cooldown_remaining() > 0):
            raise UpstreamYield()
        if interactive and (wait := cooldown_remaining()) > INTERACTIVE_MAX_WAIT:
            raise UpstreamCooldown(math.ceil(wait))
        if interactive:
            with _waiters_lock:
                global _interactive_waiters
                _interactive_waiters += 1
        try:
            with _slots:
                # 拿到名额后再看一眼：等待期间可能来了交互请求
                if not interactive and interactive_waiting():
                    raise UpstreamYield()
                _pace(interactive)
                try:
                    r = super().send(request, **kwargs)
                except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
                    _record(None)
                    raise
                _record(r.status_code, r.headers.get("Retry-After"))
                return r
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
