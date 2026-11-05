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
import time

from . import polite, service
from .config import REFRESH_BUDGET_RPS, SCHEDULER_TICK
from .polite import UpstreamYield, background_upstream

log = logging.getLogger("kebiao.scheduler")
_stop: threading.Event = globals().get("_stop") or threading.Event()
_threads: list[threading.Thread] = globals().get("_threads") if "_threads" in globals() else []
_thread_lock: threading.Lock = globals().get("_thread_lock") or threading.Lock()
_refresh_wanted: bool = globals().get("_refresh_wanted", False)

REQUESTS_PER_REFRESH = 2.5      # 一次刷新平均打学校几次（课表页 + 打印数据，偶尔加校历 / 续期）
KEEPALIVE_COST = 2 / REQUESTS_PER_REFRESH  # 一次保活（教务课表页 + 认证中心各一次）折合多少次刷新
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


# 热重载：换成新版的类（改 Budget 的逻辑才会生效），但继承被学校报错压下去的速率和剩余令牌
_prev_budget = globals().get("_budget")
_budget = Budget()
if _prev_budget is not None:
    _budget.rate = min(_prev_budget.rate, REFRESH_BUDGET_RPS)
    _budget.tokens = _prev_budget.tokens
last_tick: dict | None = globals().get("last_tick")   # 最近一个 tick 的概要，管理员页展示用


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
    kept = keepalive_once(budget)
    global last_tick
    last_tick = {"at": time.time(), "due": len(due), "refreshed": done, "kept_alive": kept,
                 "budget_rps": round(budget.rate, 3)}
    # 有人到期、刷了人、或预算变了才记一行：平时安静，需要时能看出调度器在干什么
    if due or done or kept or budget.rate != before:
        log.info("tick: due=%d refreshed=%d kept_alive=%d budget=%.2f/s (max %.2f)%s",
                 len(due), done, kept, budget.rate, budget.max_rate,
                 "" if budget.rate >= before else " ↓ 学校报错或要求暂停，预算减半")
    return done


def keepalive_once(budget: Budget) -> int:
    """刷新排完后用剩下的预算给扫码登录的人保活。返回保活了几个人。"""
    kept = 0
    for uid in service.keepalive_candidates():
        if _stop.is_set() or budget.tokens < KEEPALIVE_COST:
            break
        try:
            with background_upstream():
                service.keepalive_user(uid)
        except service.Busy:
            continue  # 正在刷新，本身就碰到了教务
        except UpstreamYield:
            break
        except KeyError:
            continue
        except Exception as e:
            log.error("keepalive crashed user=%s: %s", uid, type(e).__name__)
        budget.tokens -= KEEPALIVE_COST
        kept += 1
        if _stop.wait(random.uniform(*USER_GAP)):
            break
    return kept


def _loop() -> None:
    while not _stop.wait(SCHEDULER_TICK * random.uniform(0.9, 1.1)):
        with _thread_lock:
            if not _refresh_wanted:  # 热重载后 SCHEDULER_ENABLED 关了：下个 tick 退出
                if threading.current_thread() in _threads:
                    _threads.remove(threading.current_thread())
                return
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
    global _refresh_wanted
    with _thread_lock:
        _stop.clear()
        _refresh_wanted = refresh
        _threads[:] = [t for t in _threads if t.is_alive()]
        running = {t.name for t in _threads}
        # 只补缺的线程：重复调用不制造第二条，挂掉的那条能拉起来
        missing = []
        if "qr-pump" not in running:
            missing.append(threading.Thread(target=_qr_loop, name="qr-pump", daemon=True))
        if refresh and "refresh-scheduler" not in running:
            missing.append(threading.Thread(target=_loop, name="refresh-scheduler", daemon=True))
        _threads.extend(missing)
        for t in missing:
            t.start()


def alive() -> dict:
    """各后台线程是否还活着：{线程名: bool}。"""
    with _thread_lock:
        return {t.name: t.is_alive() for t in _threads}


def stop() -> None:
    _stop.set()
    with _thread_lock:
        threads = list(_threads)
        _threads.clear()
    # 两条线程共用 5 秒：都是 daemon，正卡在学校请求里的那条不等它，免得拖慢重启
    deadline = time.monotonic() + 5
    for t in threads:
        t.join(timeout=max(0.0, deadline - time.monotonic()))
