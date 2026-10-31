"""动态刷新策略：每个人的课表“允许旧到什么程度”，以及谁最该先刷。

没有固定时段、没有统一间隔。一个人的目标间隔由这些信号决定：
- 活跃度：最近多久打开过网站 / 小组件 / 日历订阅（越活跃越勤）；
- 临近上课：数据只需要在用到之前新鲜——间隔不超过“距下一节课的三分之一”（最短 30 分钟），
  离上课越近刷得越勤，半夜离早课还远时自然放缓；
- 近期变动：3 天内刚发现过调课，间隔减半（调课往往扎堆）；
- 近期没课：7 天内都没有课（假期、开学前、结课后）至少 24 小时一刷。

调度器按“逾期程度”= 数据年龄 / 目标间隔 排队，在全站预算内从最逾期的开始刷。
本模块是纯函数，不碰数据库和网络，便于测试。
"""
from __future__ import annotations

from dataclasses import dataclass

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

CLASS_SOON_INTERVAL = 30 * MINUTE   # 临近上课时的最短间隔
CLASS_LEAD_FRACTION = 3             # 间隔不超过距下一节课时间的 1/3
RECENT_CHANGE = 3 * DAY
NO_CLASS_HORIZON = 7 * DAY
NO_CLASS_INTERVAL = 24 * HOUR

# 有人主动要求刷新（打开了过期页面 / 同班同学课表变动）：直接排到逾期最严重的人前面
REQUESTED_BOOST = 100.0


@dataclass(frozen=True)
class Signals:
    last_seen_at: float | None = None
    last_success_at: float | None = None
    last_change_at: float | None = None
    next_class_at: float | None = None      # None = 近期（两个月内）没有课
    refresh_requested_at: float | None = None


@dataclass(frozen=True)
class Target:
    seconds: float
    reason: str


def target(sig: Signals, now: float) -> Target:
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
    else:
        lead = max(CLASS_SOON_INTERVAL, until_class / CLASS_LEAD_FRACTION)
        if lead < seconds:
            seconds = lead
            reason = ("临近上课" if until_class <= 3 * HOUR
                      else "快到上课时间" if until_class <= DAY else "这周有课")

    if sig.last_change_at and now - sig.last_change_at <= RECENT_CHANGE:
        seconds, reason = seconds / 2, f"{reason}，课表近期有变动"

    return Target(max(MIN_INTERVAL, seconds), reason)


def overdue(sig: Signals, now: float) -> float:
    """≥ 1 表示该刷了；越大越该先刷。从没成功过的人视为极度逾期。"""
    if sig.refresh_requested_at and (not sig.last_success_at or sig.refresh_requested_at > sig.last_success_at):
        return REQUESTED_BOOST + (now - sig.refresh_requested_at) / MINUTE
    if not sig.last_success_at:
        return REQUESTED_BOOST
    return (now - sig.last_success_at) / target(sig, now).seconds


def next_due(sig: Signals, now: float) -> float:
    """预计下次刷新时刻（展示用；实际还取决于全站排队情况）。"""
    if sig.refresh_requested_at and (not sig.last_success_at or sig.refresh_requested_at > sig.last_success_at):
        return now
    base = sig.last_success_at or now
    return max(now, base + target(sig, now).seconds)
