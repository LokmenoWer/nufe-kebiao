"""后台动态刷新：按“谁最逾期”排队，在全站预算内刷；预算随学校的反应自动伸缩。

没有固定时段和统一间隔：每个人该多久刷一次由 refresh_policy 按活跃度、临近上课、近期变动算出，
调度器每个 tick 取最逾期的若干人刷新。预算（后台每秒可用的学校请求数）按 AIMD 调整：
学校报错 / 让我们暂停时立刻减半，正常时每个 tick 加回一点，直到 REFRESH_BUDGET_RPS。

扫码登录的状态轮询在另一条独立线程里，不受刷新批次影响。两条线程发往学校的请求都经过 polite 的全站限速。
"""
from __future__ import annotations

import logging
import random
import threading

from . import polite, service
from .config import REFRESH_BUDGET_RPS, SCHEDULER_TICK
from .polite import UpstreamYield, background_upstream

log = logging.getLogger("kebiao.scheduler")
_stop = threading.Event()
_threads: list[threading.Thread] = []
_thread_lock = threading.Lock()

REQUESTS_PER_REFRESH = 2.5      # 一次刷新平均打学校几次（课表页 + 打印数据，偶尔加校历 / 续期）
MIN_RATE = 0.05                 # 预算下限：学校再不稳也保持极低频地探一探
RECOVER_STEP = 0.1              # 每个正常 tick 加回上限的 10%
USER_GAP = (0.3, 1.0)           # 两个人之间的小抖动（秒）


class Budget:
    """后台刷新预算（令牌桶 + AIMD）。单位：次刷新。"""

    def __init__(self, max_rps: float = REFRESH_BUDGET_RPS, tick: float = SCHEDULER_TICK):
        self.max_rate = max_rps
        self.rate = max_rps
        self.tick = tick
        self.tokens = 0.0

    def adjust(self, penalty: float, cooldown: float) -> None:
        if penalty > 0 or cooldown > 0:
            self.rate = max(MIN_RATE, self.rate / 2)
        else:
            self.rate = min(self.max_rate, self.rate + self.max_rate * RECOVER_STEP)

    def refill(self) -> float:
        per_tick = self.rate * self.tick / REQUESTS_PER_REFRESH
        # 最多攒两个 tick 的量：闲了一阵也不会一口气冲向学校
        self.tokens = min(self.tokens + per_tick, per_tick * 2)
        return self.tokens


_budget = Budget()


def run_once(budget: Budget | None = None) -> int:
    """一个 tick：调整预算，从最逾期的人开始刷。返回刷了几个人。"""
    budget = budget or _budget
    before = budget.rate
    budget.adjust(*polite.health())
    budget.refill()
    due = service.candidates(limit=1_000_000)
    done = 0
    for _score, uid in due:
        if _stop.is_set() or budget.tokens < 1:
            break
        if not service.still_due(uid):
            continue
        try:
            with background_upstream():
                r = service.refresh_user(uid, "scheduled")
            log.info("scheduled refresh user=%s ok=%s", uid, r.get("ok"))
        except service.Busy:
            continue  # 用户正在手动刷新，不空等
        except UpstreamYield:
            log.info("scheduled refresh yielded user=%s", uid)
            break
        except KeyError:
            continue  # 用户已删除
        except Exception as e:
            # 只记录类型：异常原文可能包含带 ticket 的 URL
            log.error("scheduled refresh crashed user=%s: %s", uid, type(e).__name__)
        budget.tokens -= 1
        done += 1
        if _stop.wait(random.uniform(*USER_GAP)):
            break
    # 有人到期、刷了人、或预算变了才记一行：平时安静，需要时能看出调度器在干什么
    if due or done or budget.rate != before:
        log.info("tick: due=%d refreshed=%d budget=%.2f/s (max %.2f)%s",
                 len(due), done, budget.rate, budget.max_rate,
                 "" if budget.rate >= before else " ↓ 学校报错或要求暂停，预算减半")
    return done


def _loop() -> None:
    while not _stop.wait(SCHEDULER_TICK * random.uniform(0.9, 1.1)):
        try:
            service.housekeeping()
        except Exception as e:
            log.error("housekeeping failed: %s", type(e).__name__)
        try:
            run_once()
        except Exception as e:
            log.error("scheduler tick failed: %s", type(e).__name__)


def _qr_loop() -> None:
    # 每秒看一次：有人在扫码就向学校查一次状态（实际频率受全站限速约束）
    while not _stop.wait(1.0):
        try:
            if service.qr_waiting():
                service.pump_qr_once()
        except Exception as e:
            log.error("qr pump failed: %s", type(e).__name__)


def start(refresh: bool = True) -> None:
    """扫码轮询总是启动（否则扫码登录无法完成）；refresh=False 时不跑后台定时刷新。"""
    with _thread_lock:
        _stop.clear()
        _threads[:] = [t for t in _threads if t.is_alive()]
        if _threads:
            return  # 已经启动过（重复调用不应制造第二条轮询线程）
        _threads.append(threading.Thread(target=_qr_loop, name="qr-pump", daemon=True))
        if refresh:
            _threads.append(threading.Thread(target=_loop, name="refresh-scheduler", daemon=True))
        for t in _threads:
            t.start()


def stop() -> None:
    _stop.set()
    with _thread_lock:
        threads = list(_threads)
        _threads.clear()
    for t in threads:
        t.join(timeout=5)
