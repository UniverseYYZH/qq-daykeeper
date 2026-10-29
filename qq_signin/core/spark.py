"""好友火花守护的定时计算与消息历史判断。

火花是 QQ 的“好友互动标识”：双方当天都发过消息才会续上。
本模块只读取私聊历史，判断指定好友今天有没有发来消息；不读取/保存
消息正文，也不做自动回复之外的行为。
"""

from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta
from typing import Any, Iterable

START_TIME = time(18, 0)
FINAL_TIME = time(23, 50)
RANDOM_MIN_MINUTES = 45
RANDOM_MAX_MINUTES = 75
MIN_GAP_MINUTES = 30
MAX_REMINDERS_PER_DAY = 3


def compute_next_check(
    now: datetime,
    *,
    last_check_at: datetime | None = None,
    rng: random.Random | None = None,
) -> datetime:
    """计算下一次检查时间。

    - 18:00 后第一次检查在 18:45–19:15；
    - 后续间隔 45–75 分钟；
    - 23:20 之后不再插入随机检查，统一到 23:50 最终检查；
    - 与上一次检查尽量间隔 30 分钟以上。
    """
    rng = rng or random.Random()
    final = datetime.combine(now.date(), FINAL_TIME)

    if now >= final:
        # 今天的最终检查已经过去，安排明天 18:00 后的第一次
        tomorrow = now.date() + timedelta(days=1)
        base = datetime.combine(tomorrow, START_TIME)
        return base + timedelta(minutes=rng.randint(RANDOM_MIN_MINUTES, RANDOM_MAX_MINUTES))

    if now < datetime.combine(now.date(), START_TIME):
        base = datetime.combine(now.date(), START_TIME)
    else:
        base = now

    candidate = base + timedelta(
        minutes=rng.randint(RANDOM_MIN_MINUTES, RANDOM_MAX_MINUTES)
    )

    cutoff = final - timedelta(minutes=MIN_GAP_MINUTES)
    if candidate > cutoff:
        candidate = final

    if last_check_at is not None:
        minimum = last_check_at + timedelta(minutes=MIN_GAP_MINUTES)
        if candidate < minimum:
            candidate = minimum
        if candidate > cutoff:
            candidate = final

    return candidate


def parse_dt(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def contact_sent_today(
    messages: Iterable[dict[str, Any]],
    friend_id: int,
    now: datetime,
) -> bool:
    """判断好友今天是否发来过私聊消息。

    只检查发送者和时间，不解析消息正文。
    """
    day_start = datetime.combine(now.date(), time.min).timestamp()
    for message in messages:
        if not isinstance(message, dict):
            continue
        sender = message.get("sender")
        if isinstance(sender, dict):
            sender_id = sender.get("user_id") or sender.get("uin")
        else:
            sender_id = None
        sender_id = sender_id or message.get("user_id") or message.get("sender_id")
        if sender_id is None:
            continue
        try:
            if int(sender_id) != int(friend_id):
                continue
        except (TypeError, ValueError):
            continue

        timestamp = _message_timestamp(message)
        if timestamp is not None and timestamp >= day_start:
            return True
    return False


def latest_message_time(
    messages: Iterable[dict[str, Any]],
    friend_id: int,
) -> datetime | None:
    """返回好友最近一条消息时间，用于状态展示。"""
    latest: datetime | None = None
    for message in messages:
        if not isinstance(message, dict):
            continue
        sender = message.get("sender")
        if isinstance(sender, dict):
            sender_id = sender.get("user_id") or sender.get("uin")
        else:
            sender_id = None
        sender_id = sender_id or message.get("user_id") or message.get("sender_id")
        try:
            if int(sender_id) != int(friend_id):
                continue
        except (TypeError, ValueError):
            continue
        timestamp = _message_timestamp(message)
        if timestamp is None:
            continue
        moment = datetime.fromtimestamp(timestamp)
        if latest is None or moment > latest:
            latest = moment
    return latest


def choose_message(messages: list[str], rng: random.Random | None = None) -> str:
    rng = rng or random.Random()
    return rng.choice(messages)


def _message_timestamp(message: dict[str, Any]) -> float | None:
    value = (
        message.get("time")
        or message.get("timestamp")
        or message.get("msgTime")
        or message.get("msg_time")
    )
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    # NapCat 一般返回秒；兼容毫秒
    if number > 10_000_000_000:
        number /= 1000.0
    return number


def today_iso(now: datetime | None = None) -> str:
    return (now or datetime.now()).date().isoformat()


def next_day_start(now: datetime) -> datetime:
    return datetime.combine(now.date() + timedelta(days=1), START_TIME)
