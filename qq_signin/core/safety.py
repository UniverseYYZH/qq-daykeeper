"""账号保护：硬性限流、每日额度与风控自动暂停。

重要：第三方协议端本身就有封号风险，这些措施只能降低高频操作带来的
额外风险，不能保证账号不被封。不要用主号测试，不要群发广告。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .models import AppState

MIN_SEND_INTERVAL = 5.0
MAX_TARGETS_PER_TASK = 20
MAX_MESSAGES_PER_DAY = 100
MAX_SIGN_GROUPS_PER_RUN = 20
MAX_SIGN_GROUPS_PER_DAY = 50
RISK_PAUSE_MINUTES = 120

# 只匹配比较明确的风控/登录异常，避免把普通“群不存在”误判成风控
RISK_KEYWORDS = (
    "风控",
    "操作频繁",
    "操作太快",
    "请求过于频繁",
    "发送频繁",
    "账号异常",
    "账号被限制",
    "登录失效",
    "登录已失效",
    "token失效",
    "冻结",
    "封禁",
    "too many requests",
    "frequency limit",
    "risk control",
)


class SafetyError(RuntimeError):
    """触发了本地安全限制。"""


def _today(now: datetime) -> str:
    return now.date().isoformat()


def _prepare_day(state: AppState, now: datetime) -> None:
    today = _today(now)
    if state.safety.date != today:
        state.safety.date = today
        state.safety.messages = 0
        state.safety.sign_groups = 0
        state.safety.paused_until = ""
        state.safety.last_reason = ""


def check_pause(state: AppState, now: datetime) -> None:
    """如果处于风控暂停中，抛出 SafetyError。"""
    _prepare_day(state, now)
    if not state.safety.paused_until:
        return
    try:
        until = datetime.fromisoformat(state.safety.paused_until)
    except ValueError:
        state.safety.paused_until = ""
        return
    if until > now:
        remaining = int((until - now).total_seconds() // 60) + 1
        raise SafetyError(
            f"安全保护：检测到风控信号，已暂停发送，约 {remaining} 分钟后解除。"
            f"原因：{state.safety.last_reason or '未知'}"
        )
    state.safety.paused_until = ""


def reserve_messages(state: AppState, count: int, now: datetime) -> None:
    """预留当天的消息发送额度；超出限制时拒绝执行。"""
    _prepare_day(state, now)
    check_pause(state, now)
    if count > MAX_TARGETS_PER_TASK:
        raise SafetyError(
            f"安全保护：单个任务最多 {MAX_TARGETS_PER_TASK} 个目标，"
            f"当前选了 {count} 个。请拆成多个任务。"
        )
    if state.safety.messages + count > MAX_MESSAGES_PER_DAY:
        raise SafetyError(
            f"安全保护：今日消息额度只剩 "
            f"{max(0, MAX_MESSAGES_PER_DAY - state.safety.messages)} 条，"
            f"本次需要 {count} 条。明天再发，或减少目标。"
        )
    state.safety.messages += count


def reserve_sign_groups(state: AppState, count: int, now: datetime) -> None:
    """预留当天的群打卡额度。"""
    _prepare_day(state, now)
    check_pause(state, now)
    if count > MAX_SIGN_GROUPS_PER_RUN:
        raise SafetyError(
            f"安全保护：单次最多操作 {MAX_SIGN_GROUPS_PER_RUN} 个群，"
            f"当前选了 {count} 个。请分批执行。"
        )
    if state.safety.sign_groups + count > MAX_SIGN_GROUPS_PER_DAY:
        raise SafetyError(
            f"安全保护：今日群打卡额度只剩 "
            f"{max(0, MAX_SIGN_GROUPS_PER_DAY - state.safety.sign_groups)} 个群，"
            f"本次需要 {count} 个。明天再试。"
        )
    state.safety.sign_groups += count


def is_risk_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(keyword.lower() in text for keyword in RISK_KEYWORDS)


def pause_for_risk(state: AppState, now: datetime, reason: str) -> None:
    """检测到风控信号后暂停一段时间。"""
    _prepare_day(state, now)
    until = now + timedelta(minutes=RISK_PAUSE_MINUTES)
    state.safety.paused_until = until.isoformat(timespec="seconds")
    state.safety.last_reason = reason[:200]


def safety_summary(state: AppState, now: datetime) -> str:
    _prepare_day(state, now)
    return (
        f"今日消息 {state.safety.messages}/{MAX_MESSAGES_PER_DAY}，"
        f"群打卡 {state.safety.sign_groups}/{MAX_SIGN_GROUPS_PER_DAY}"
    )
