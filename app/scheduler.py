"""后台定时刷新：单线程串行处理到期用户，只在允许的时段工作，每个用户之间随机间隔。

扫码登录的状态轮询在另一条独立线程里：刷新批次会在用户之间停 5~20 秒，
不能让扫码的人跟着等。两条线程发往学校的请求都经过 polite 的全站限速。
"""
from __future__ import annotations

import datetime as dt
import logging
import random
import threading
from zoneinfo import ZoneInfo

from . import service
from .config import REFRESH_ALLOWED_HOURS, SCHEDULER_TICK, TIMEZONE
from .polite import UpstreamYield, background_upstream

log = logging.getLogger("kebiao.scheduler")
_stop = threading.Event()


def _allowed_now() -> bool:
    return dt.datetime.now(ZoneInfo(TIMEZONE)).hour in REFRESH_ALLOWED_HOURS


def run_once(batch: int = 20) -> int:
    done = 0
    for uid in service.due_users(batch):
        if _stop.is_set() or not _allowed_now():
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
        except Exception:
            log.exception("scheduled refresh crashed user=%s", uid)
        done += 1
        if _stop.wait(random.uniform(5, 20)):  # 用户之间的抖动间隔
            break
    return done


def _loop() -> None:
    while not _stop.wait(SCHEDULER_TICK * random.uniform(0.8, 1.2)):
        try:
            service.housekeeping()
        except Exception:
            log.exception("housekeeping failed")
        if _allowed_now():
            try:
                run_once()
            except Exception:
                log.exception("scheduler tick failed")


def _qr_loop() -> None:
    # 每秒看一次：有人在扫码就向学校查一次状态（实际频率受全站限速约束）
    while not _stop.wait(1.0):
        try:
            if service.qr_waiting():
                service.pump_qr_once()
        except Exception:
            log.exception("qr pump failed")


def start(refresh: bool = True) -> None:
    """扫码轮询总是启动（否则扫码登录无法完成）；refresh=False 时不跑后台定时刷新。"""
    _stop.clear()
    threading.Thread(target=_qr_loop, name="qr-pump", daemon=True).start()
    if refresh:
        threading.Thread(target=_loop, name="refresh-scheduler", daemon=True).start()


def stop() -> None:
    _stop.set()
