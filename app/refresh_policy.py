"""动态刷新策略：每个人的课表“什么时候必须是新的”，以及谁最该先刷。

临时调课（换教室、改时间、停课、补课）真正要紧的只有一个时刻：出门去上那节课之前。
所以策略分两层，取更早到期的那个：

1. 出门前检查点：下一节课开始前 CHECK_LEAD（30 分钟）是“检查点”。检查点前 CHECK_WINDOW（10 分钟）内
   必须刷到过一次——自己刷到的、或同一节课的同班同学刚刷过且没发现变动，都算。
   每节课只需要这一次，不再像以前那样离上课越近刷得越密（那些中间的刷新并不能让出门时的数据更新）。
   只对最近两周用过、或订阅了课表变动邮件的人做：没人看的课表不值得为它在早高峰占学校的额度。
2. 基础间隔：按活跃度分档（常用 4 小时 …… 很久没用 7 天）；3 天内刚调过课减半（调课往往扎堆）；
   7 天内都没课（假期、开学前、结课后）至少 24 小时一刷。深夜（北京时间 0:00–6:30）教务没人改课，
   基础刷新推迟到 6:30，检查点和主动请求不受影响。

调度器按“逾期程度”排队：有人主动请求 > 检查点 > 基础间隔，在全站预算内从最逾期的开始刷。
本模块是纯函数，不碰数据库和网络，便于测试。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from .config import TIMEZONE

MINUTE = 60
HOUR = 3600
DAY = 86400

MIN_INTERVAL = 15 * MINUTE

# 活跃度分档：(最近一次使用距今不超过, 基础间隔, 说明)
ACTIVITY_TIERS = (
    (3 * DAY, 4 * HOUR, "常用"),
    (14 * DAY, 6 * HOUR, "近两周用过"),
    (60 * DAY, 24 * HOUR, "不常用"),
)
INACTIVE_INTERVAL = 7 * DAY
CHECKPOINT_IDLE = 14 * DAY          # 超过这么久没用（且没订阅变动邮件）就不做出门前检查

CHECK_LEAD = 30 * MINUTE            # 检查点：上课前 30 分钟（出门前）
CHECK_WINDOW = 10 * MINUTE          # 检查点之前这么久内刷过就算数
RECENT_CHANGE = 3 * DAY
NO_CLASS_HORIZON = 7 * DAY
NO_CLASS_INTERVAL = 24 * HOUR
QUIET_END = (6, 30)                 # 北京时间 0:00 到这个时刻之间不做基础刷新

# 排队优先级：主动请求（打开了过期页面 / 同班同学课表变动）> 检查点 > 基础间隔
REQUESTED_BOOST = 100.0
CHECKPOINT_BOOST = 10.0

_TZ = ZoneInfo(TIMEZONE)


@dataclass(frozen=True)
class Signals:
    last_seen_at: float | None = None
    last_success_at: float | None = None
    last_change_at: float | None = None
    next_class_at: float | None = None      # None = 近期（两个月内）没有课
    refresh_requested_at: float | None = None
    subscribed: bool = False                # 订阅了课表变动邮件：没打开网站也值得做检查点
    peer_checked_at: float | None = None    # 下一节课的同班同学最近一次刷新成功（没发现变动）


@dataclass(frozen=True)
class Target:
    seconds: float
    reason: str


def requested(sig: Signals) -> bool:
    return bool(sig.refresh_requested_at and (not sig.last_success_at
                                              or sig.refresh_requested_at > sig.last_success_at))


def _after_quiet(ts: float) -> float:
    """落在深夜里的时刻推迟到当天 6:30。"""
    local = dt.datetime.fromtimestamp(ts, _TZ)
    end = local.replace(hour=QUIET_END[0], minute=QUIET_END[1], second=0, microsecond=0)
    return end.timestamp() if local < end else ts


def target(sig: Signals, now: float) -> Target:
    """基础间隔（不含检查点和深夜推迟）：给界面展示“约每多久刷一次”。"""
    seen = sig.last_seen_at or sig.last_success_at
    idle = now - seen if seen else float("inf")
    seconds, reason = INACTIVE_INTERVAL, "很久没用"
    for limit, interval, label in ACTIVITY_TIERS:
        if idle <= limit:
            seconds, reason = interval, label
            break

    until_class = sig.next_class_at - now if sig.next_class_at else None
    if until_class is None or until_class > NO_CLASS_HORIZON:
        if seconds < NO_CLASS_INTERVAL:
            seconds, reason = NO_CLASS_INTERVAL, "近期没有课"
    elif seconds > NO_CLASS_INTERVAL:
        seconds, reason = NO_CLASS_INTERVAL, "这周有课"   # 很久没用但这周有课：至少一天看一眼

    if sig.last_change_at and now - sig.last_change_at <= RECENT_CHANGE:
        seconds, reason = seconds / 2, f"{reason}，课表近期有变动"

    return Target(max(MIN_INTERVAL, seconds), reason)


def checks_before_class(sig: Signals, now: float) -> bool:
    seen = sig.last_seen_at or sig.last_success_at
    return sig.subscribed or bool(seen and now - seen <= CHECKPOINT_IDLE)


def checkpoint(sig: Signals, now: float) -> tuple[float, float] | None:
    """下一节课的检查窗口 (开始, 检查点)；不需要检查、或已经有人在窗口内刷过时为 None。"""
    if not sig.next_class_at or sig.next_class_at <= now or not checks_before_class(sig, now):
        return None
    due = sig.next_class_at - CHECK_LEAD
    opens = due - CHECK_WINDOW
    verified = max(sig.last_success_at or 0, sig.peer_checked_at or 0)
    return None if verified >= opens else (opens, due)


def _base_due(sig: Signals, now: float) -> float:
    return _after_quiet((sig.last_success_at or now) + target(sig, now).seconds)


def overdue(sig: Signals, now: float) -> float:
    """≥ 1 表示该刷了；越大越该先刷。从没成功过的人视为极度逾期。"""
    if requested(sig):
        return REQUESTED_BOOST + (now - sig.refresh_requested_at) / MINUTE
    if not sig.last_success_at:
        return REQUESTED_BOOST
    cp = checkpoint(sig, now)
    if cp and now >= cp[0]:
        # 越接近上课越急；过了检查点还没刷到的继续往前排
        return CHECKPOINT_BOOST + (now - cp[0]) / MINUTE
    if now < _base_due(sig, now):
        return (now - sig.last_success_at) / (_base_due(sig, now) - sig.last_success_at) * 0.99
    return (now - sig.last_success_at) / target(sig, now).seconds


def next_due(sig: Signals, now: float) -> float:
    """预计下次刷新时刻（展示用；实际还取决于全站排队情况）。"""
    if requested(sig) or not sig.last_success_at:
        return now
    due = _base_due(sig, now)
    cp = checkpoint(sig, now)
    if cp:
        due = min(due, cp[0])
    return max(now, due)


def reason(sig: Signals, now: float) -> str:
    """界面上的一句话说明。"""
    base = target(sig, now).reason
    if requested(sig):
        return "已排队，马上刷新"
    cp = checkpoint(sig, now)
    if cp and now >= cp[0]:
        return "上课前确认"
    if checks_before_class(sig, now) and sig.next_class_at:
        return f"{base}；每节课出门前再确认一次"
    return base
